"""A rewrite whose process died (the app restarted) reads as stopped, not as a bar standing still."""

import json
import os
import time

from ml.tailoring import pipeline


def test_a_run_whose_process_is_gone_is_interrupted(tmp_path):
    pipeline._progress(tmp_path, stage=4, done=False, error=None, note="round 2 of 3")
    state = json.loads((tmp_path / "progress.json").read_text())
    assert state["pid"] == os.getpid() and not pipeline.interrupted(tmp_path, state)   # this process: alive
    state["pid"] = 2 ** 22 + 12345                                                     # no such process
    assert pipeline.interrupted(tmp_path, state)
    assert not pipeline.interrupted(tmp_path, {**state, "done": True})


def test_an_old_run_without_a_pid_is_interrupted_after_half_an_hour(tmp_path):
    (tmp_path / "progress.json").write_text(json.dumps({"stage": 4, "done": False}))
    assert not pipeline.interrupted(tmp_path, {"stage": 4, "done": False})
    old = time.time() - pipeline.STALE_S - 60
    os.utime(tmp_path / "progress.json", (old, old))
    assert pipeline.interrupted(tmp_path, {"stage": 4, "done": False})


def test_a_slow_skim_test_never_holds_up_a_finished_resume(monkeypatch):
    """A busy plan kept the bar at 99% for thirteen minutes on advice about a resume already made."""
    from ml.tailoring import review
    monkeypatch.setattr(review, "skim_test", lambda *a: time.sleep(2) or {"first_six_seconds": "late"})
    started = time.monotonic()
    skim, note = pipeline.skim_within("resume", "job", None, wait_s=0.2)
    assert time.monotonic() - started < 1.5 and skim == {} and "finished without it" in note
    monkeypatch.setattr(review, "skim_test", lambda *a: {"first_six_seconds": "fast"})
    assert pipeline.skim_within("resume", "job", None, wait_s=1) == ({"first_six_seconds": "fast"}, "")
