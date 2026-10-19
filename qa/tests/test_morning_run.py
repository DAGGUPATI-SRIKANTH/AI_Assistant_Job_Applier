"""The morning run on the Jobs page: the step it is really on, and Start, Stop and the 6 AM switch."""

import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

from backend.api import app as webapp
from backend.api import morning_pages

NOW = datetime.now()


def board(t, slug):
    return (f"{t:%H:%M:%S}  INFO    data_engineering.scraper.runner greenhouse  {slug}"
            "   9 seen    1 match    0 new    0 closed")


def failed(t, slug):
    return (f"{t:%H:%M:%S}  WARNING data_engineering.scraper.runner FAILED workday/{slug} — "
            "FetchError: Giving up after 3 retries")


def run_log(tmp_path, monkeypatch, *rows, began=None):
    began = began or NOW - timedelta(minutes=30)
    head = [f"{began:%Y-%m-%d %H:%M:%S}  === daily starting ===",
            f"{began:%H:%M:%S}  INFO    data_engineering.scraper.runner Run x — scraping 4 companies, 6 at a time"]
    log = tmp_path / "daily_run.log"
    log.write_text("\n".join(head + list(rows)) + "\n")
    monkeypatch.setattr(webapp, "DAILY_LOG", log)
    monkeypatch.setattr(morning_pages, "DAILY_LOG", log)
    return log


def test_failed_boards_count_so_a_finished_fetch_is_not_still_fetching(tmp_path, monkeypatch):
    """18 Oct: 1,199 of 21,835 boards failed, never wrote "seen", and the page sat at 94.5%."""
    t = NOW - timedelta(minutes=5)
    run_log(tmp_path, monkeypatch, board(t, "a"), board(t, "b"), board(t, "c"), failed(t, "d"))
    p = webapp._daily_progress()
    assert (p["done"], p["total"], p["percent"], p["failed"]) == (4, 4, 100.0, 1)
    assert p["stage"] == "scoring" and p["eta"] is None


def test_each_step_after_the_fetch_is_named(tmp_path, monkeypatch):
    t = NOW - timedelta(minutes=5)
    rows = [board(t, s) for s in "abcd"] + ["  4 boards scraped   36 postings seen   2 rows updated"]
    run_log(tmp_path, monkeypatch, *rows)
    assert webapp._daily_progress()["stage"] == "suggesting"
    rows += ["Preparing 10 application(s): tailoring, cover letter, questions…",
             "  ready: Acme — Cloud Engineer", "  skipped Globex — SRE: TimeoutError: slow",
             "  ready: Initech — Platform Engineer · 2 question(s) for you"]
    run_log(tmp_path, monkeypatch, *rows)
    p = webapp._daily_progress()
    assert (p["stage"], p["prepared"], p["prepare_total"]) == ("preparing", 3, 10)


def test_a_scheduled_run_waiting_for_the_lid_says_so(tmp_path, monkeypatch):
    began = NOW - timedelta(hours=3)
    run_log(tmp_path, monkeypatch, f"{began:%Y-%m-%d %H:%M:%S}  waiting for the Mac to be opened — lid shut",
            began=began)
    p = webapp._daily_progress()
    assert p["stage"] == "waiting" and p["started_by"] == "schedule"
    run_log(tmp_path, monkeypatch, f"{began:%Y-%m-%d %H:%M:%S}  waiting for the Mac to be opened — lid shut",
            f"{NOW:%Y-%m-%d %H:%M:%S}  the Mac is open — starting", board(NOW, "a"), began=began)
    assert webapp._daily_progress()["stage"] == "fetching"


def test_a_run_started_by_hand_is_told_from_the_6am_one(tmp_path, monkeypatch):
    began = NOW - timedelta(minutes=2)
    run_log(tmp_path, monkeypatch, f"{began:%Y-%m-%d %H:%M:%S}  started from the Jobs page", began=began)
    assert webapp._daily_progress()["started_by"] == "you"


def test_a_lock_nobody_holds_does_not_keep_the_page_at_running(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data" / "daily.lock").mkdir(parents=True)
    monkeypatch.setattr(webapp, "_pipeline_alive", lambda task: False)
    assert webapp._scheduled_run_holding("scrape") is None
    monkeypatch.setattr(webapp, "_pipeline_alive", lambda task: True)
    assert webapp._scheduled_run_holding("scrape") == "daily"


def test_only_this_copys_script_is_the_run(tmp_path, monkeypatch):
    """19 Oct: a shell loop waiting for the run to end -- its command line held "daily_pipeline.sh
    daily" -- was taken for the run, and Stop ended it with the run. Neither that nor another copy
    of the app on the same Mac is this copy's morning run."""
    here, other = tmp_path / "Job Applier" / "devops", tmp_path / "other copy" / "devops"
    for folder in (here, other):
        folder.mkdir(parents=True)
        (folder / "daily_pipeline.sh").write_text("sleep 30\n")
    script = here / "daily_pipeline.sh"
    monkeypatch.setattr(webapp, "PIPELINE_SCRIPT", script)
    procs = [subprocess.Popen(["/bin/bash", str(script), "daily"]),                     # the run
             subprocess.Popen(["/bin/bash", str(other / "daily_pipeline.sh"), "daily"]),  # another copy
             subprocess.Popen(["/bin/sh", "-c", f'sleep 30; : "{script} daily"']),      # mentions it
             subprocess.Popen(["/bin/bash", str(script), "discover"])]                  # another task
    try:
        assert webapp.pipeline_pids("daily") == [procs[0].pid]
        assert webapp.pipeline_pids("discover") == [procs[3].pid]
        assert webapp._pipeline_alive("daily") is True
    finally:
        for p in procs:
            p.kill()
            p.wait()
    assert webapp.pipeline_pids("daily") == [] and webapp._pipeline_alive("daily") is False


@pytest.fixture
def controls(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    for name, path in (("LOCK", data / "daily.lock"), ("PIDFILE", data / "daily.pid"),
                       ("AUTO_OFF", data / "daily_auto_off"), ("OUT", data / "launchd_daily.log"),
                       ("DAILY_LOG", data / "daily_run.log")):
        monkeypatch.setattr(morning_pages, name, path)
    monkeypatch.setattr(morning_pages, "task_pid", lambda task: None)
    monkeypatch.setattr(morning_pages, "pipeline_pids", lambda: [])
    monkeypatch.setattr(morning_pages, "not_ready", lambda: None)
    return data, webapp.app.test_client()


def test_nothing_runs_before_there_is_a_resume_to_search_with(controls, monkeypatch):
    """A new Mac opened after 6 AM caught up "today's" morning a minute after the app first
    started -- reading every board against config.yaml's example search, before the person had
    uploaded anything. Neither the catch-up nor Start runs until there is a resume and a search."""
    data, client = controls
    monkeypatch.setattr(morning_pages, "not_ready", lambda: "there is no resume yet — upload one")
    started = []
    monkeypatch.setattr(morning_pages.subprocess, "Popen", lambda *a, **k: started.append(a))
    assert not morning_pages.missed_morning(NOW.replace(hour=9, minute=0))
    assert not morning_pages.catch_up()
    reply = client.post("/run/morning/start")
    assert reply.status_code == 409 and "no resume yet" in reply.get_json()["error"] and started == []


def test_start_runs_the_script_by_hand_even_with_the_6am_start_off(controls, monkeypatch):
    data, client = controls
    started = {}

    class Popen:
        def __init__(self, args, **kw):
            started.update(args=args, **kw)

    monkeypatch.setattr(morning_pages.subprocess, "Popen", Popen)
    (data / "daily.lock").mkdir()                   # left by a run that was killed
    assert client.post("/run/morning/auto", json={"on": False}).get_json()["auto"] is False
    assert client.post("/run/morning/start").get_json() == {"ok": True, "started": True}
    assert started["args"] == ["/bin/bash", str(morning_pages.SCRIPT), "daily"]
    assert started["env"]["MANUAL"] == "1" and started["start_new_session"] is True
    assert not (data / "daily.lock").exists()


def test_start_waits_for_a_fetch_started_from_the_page(controls, monkeypatch):
    _, client = controls
    monkeypatch.setattr(morning_pages, "task_pid", lambda task: 4242 if task == "scrape" else None)
    reply = client.post("/run/morning/start")
    assert reply.status_code == 409 and "fetch you started" in reply.get_json()["error"]


@pytest.mark.skipif(os.name == "nt", reason="process groups are Unix-only")
def test_stop_ends_the_run_and_all_it_started_and_clears_its_lock(controls, monkeypatch):
    data, client = controls
    # A stand-in for the script and its children: a process group of its own.
    run = subprocess.Popen(["/bin/sh", "-c", "sleep 30 & sleep 30; wait"], start_new_session=True)
    try:
        monkeypatch.setattr(morning_pages, "pipeline_pids", lambda: [run.pid])
        (data / "daily.lock").mkdir()
        (data / "daily.pid").write_text(str(run.pid))
        assert client.post("/run/morning/stop").get_json() == {"ok": True, "stopped": True}
        run.wait(timeout=5)
        with pytest.raises(ProcessLookupError):
            os.killpg(run.pid, 0)                       # the whole group is gone
        assert not (data / "daily.lock").exists() and not (data / "daily.pid").exists()
        assert "=== daily stopped from the Jobs page ===" in (data / "daily_run.log").read_text()
    finally:
        try:
            os.killpg(run.pid, signal.SIGKILL)
        except OSError:
            pass


@pytest.mark.skipif(os.name != "nt", reason="taskkill is Windows-only")
def test_stop_on_windows_ends_the_run_and_its_children_and_clears_its_lock(controls, monkeypatch):
    data, client = controls
    # A stand-in for the script: a process that starts a child and prints the child's pid.
    child = "import subprocess, sys, time; c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); print(c.pid, flush=True); time.sleep(30)"
    run = subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE, text=True)
    child_pid = int(run.stdout.readline())
    try:
        monkeypatch.setattr(morning_pages, "pipeline_pids", lambda: [run.pid])
        (data / "daily.lock").mkdir()
        assert client.post("/run/morning/stop").get_json() == {"ok": True, "stopped": True}
        run.wait(timeout=5)
        listed = subprocess.run(["tasklist", "/FI", f"PID eq {child_pid}", "/NH"],
                                capture_output=True, text=True).stdout
        assert str(child_pid) not in listed                 # the child is gone too
        assert not (data / "daily.lock").exists()
        assert "=== daily stopped from the Jobs page ===" in (data / "daily_run.log").read_text()
    finally:
        subprocess.run(["taskkill", "/PID", str(run.pid), "/T", "/F"], capture_output=True)


def test_stop_with_nothing_running_says_so(controls):
    _, client = controls
    assert client.post("/run/morning/stop").get_json()["stopped"] is False


def test_the_6am_switch_and_the_last_run_are_shown(controls):
    data, client = controls
    (data / "daily_run.log").write_text(
        "2026-10-18 06:02:09  === daily starting ===\n2026-10-18 15:55:07  === daily finished, exit 0 ===\n")
    state = client.get("/run/morning").get_json()
    assert state["auto"] is True and state["running"] is False
    assert state["last"] == {"started": "2026-10-18 06:02:09", "ended": "2026-10-18 15:55:07", "how": "finished"}
    client.post("/run/morning/auto", json={"on": False})
    assert (data / "daily_auto_off").exists() and client.get("/run/morning").get_json()["auto"] is False
    client.post("/run/morning/auto", json={"on": True})
    assert not (data / "daily_auto_off").exists()


def test_a_morning_on_the_github_fetch_says_so(tmp_path, monkeypatch):
    t = NOW - timedelta(minutes=5)
    wait = f"{t:%H:%M:%S}  cloud: waiting for today's GitHub fetch (started 06:21, usually about 150 min) instead of fetching again"
    run_log(tmp_path, monkeypatch, wait)
    p = webapp._daily_progress()
    assert (p["stage"], p["cloud_started"], p["cloud_usual"]) == ("cloud-wait", "06:21", 150)
    run_log(tmp_path, monkeypatch, wait, f"{t:%H:%M:%S}  cloud: bringing in today's GitHub fetch (run 42)…")
    assert webapp._daily_progress()["stage"] == "cloud-collect"
    run_log(tmp_path, monkeypatch, wait, f"{t:%H:%M:%S}  cloud: bringing in today's GitHub fetch (run 42)…",
            f"{t:%H:%M:%S}  cloud: 12 new postings, 900 seen again, 3 taken down, from 21,000 boards")
    p = webapp._daily_progress()
    assert p["stage"] == "scoring" and p["from_cloud"]
    # No run today: the boards are read here, and the page shows that fetch.
    run_log(tmp_path, monkeypatch, f"{t:%H:%M:%S}  cloud: no GitHub fetch today — fetching on this Mac",
            board(t, "a"))
    assert webapp._daily_progress()["stage"] == "fetching"


def test_a_mac_that_was_off_at_6am_starts_the_morning_run_when_it_comes_on(controls, monkeypatch):
    data, _ = controls
    started = []

    class Popen:
        def __init__(self, args, **kw):
            started.append(kw["env"])
    monkeypatch.setattr(morning_pages.subprocess, "Popen", Popen)
    morning = datetime.now().replace(hour=9, minute=30)
    (data / "daily_run.log").write_text(
        f"{morning - timedelta(days=1):%Y-%m-%d} 06:00:01  === daily starting ===\n")   # yesterday's only
    assert morning_pages.missed_morning(morning)
    assert not morning_pages.missed_morning(morning.replace(hour=5, minute=50))   # before 6: not missed yet
    monkeypatch.setattr(morning_pages, "missed_morning", lambda now=None: True)
    assert morning_pages.catch_up() and started[0]["CATCH_UP"] == "1" and "MANUAL" not in started[0]


def test_no_catch_up_when_today_ran_it_runs_or_6am_is_switched_off(controls):
    data, client = controls
    morning = datetime.now().replace(hour=9, minute=30)
    (data / "daily_run.log").write_text(f"{morning:%Y-%m-%d} 06:00:01  === daily starting ===\n")
    assert not morning_pages.missed_morning(morning)
    (data / "daily_run.log").write_text("")
    client.post("/run/morning/auto", json={"on": False})
    assert not morning_pages.missed_morning(morning)
    client.post("/run/morning/auto", json={"on": True})
    (data / "daily.lock").mkdir()
    assert not morning_pages.missed_morning(morning)


def test_another_site_cannot_press_the_apps_buttons(controls):
    _, client = controls
    for headers in ({"Sec-Fetch-Site": "cross-site", "Origin": "https://attacker.example"},
                    {"Origin": "https://attacker.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "same-site"}):
        reply = client.post("/run/morning/auto", data="on=false", headers=headers)
        assert reply.status_code == 403, headers
    assert not morning_pages.AUTO_OFF.exists()                       # the 6 AM start left as it was
    # This app's own pages, and tools on this Mac (no browser headers), still can.
    assert client.post("/run/morning/auto", json={"on": True},
                       headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost:8770"}).status_code == 200
    assert client.post("/run/morning/auto", json={"on": True}).status_code == 200
    # A site re-pointing its own name at this Mac reads nothing either.
    assert client.get("/run/morning", headers={"Host": "attacker.example:8770"}).status_code == 403
    assert client.get("/run/morning", headers={"Host": "127.0.0.1:8770"}).status_code == 200
