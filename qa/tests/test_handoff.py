"""A form whose CAPTCHA turns the app away (Lever) is filled to learn what it asks, then sent by the
person from their own browser, with every answer to copy and the files to attach."""

from types import SimpleNamespace

import pytest

from backend.apply import anyform, runner
from backend.apply.greenhouse import FillResult

JOB = SimpleNamespace(id=6301, company="Acme", title="Platform Engineer", source="lever",
                      application_url="https://jobs.lever.co/acme/1a2bdba5-093e-408c-b662-de8e03831fda")
FORM = JOB.application_url + "/apply"


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(anyform, "UNCONFIRMED", tmp_path / "unconfirmed.json")
    monkeypatch.setattr("backend.apply.greenhouse.RUNS_DIR", tmp_path / "runs")
    send = tmp_path / "runs" / "6301-x" / "send"
    send.mkdir(parents=True)
    (send / "Jordan_Example_Resume_Acme.pdf").write_bytes(b"%PDF-1.4")
    return send


def test_only_lever_forms_are_handed_over():
    assert runner.hands_off(FORM) and runner.hands_off("https://jobs.eu.lever.co/acme/x/apply")
    assert not runner.hands_off("https://boards.greenhouse.io/acme/jobs/1")
    assert not runner.hands_off("https://evil.example/?u=https://jobs.lever.co/")


def test_a_lever_form_is_handed_over_with_its_answers_not_waited_on(run_dir, monkeypatch):
    reached = SimpleNamespace(error=None, page=object(), kind="form", url=FORM)
    monkeypatch.setattr(anyform, "reach", lambda page, url, **kw: reached)
    result = FillResult(job_id=JOB.id, url=FORM, filled={"Full name": "Jordan Example",
                                                         "Why Acme?": "Your platform team runs what I ran."},
                        required_blank=["Pronouns"], declarations=["I agree to the privacy notice"])
    monkeypatch.setattr(anyform, "fill", lambda *a, **k: result)
    monkeypatch.setattr(anyform, "watch", lambda *a, **k: pytest.fail("a handed-over form is not watched"))
    monkeypatch.setattr(runner, "_letter_text", lambda cfg, job: None)
    resume = run_dir / "Jordan_Example_Resume_Acme.pdf"
    out = runner._apply_anywhere(None, object(), JOB, None, "[1/1] Acme", runner.Outcome(JOB.id, "Acme", "x", "not-filled"),
                                 resume, None, "tailored", run_dir.parent, 60, auto=False)
    assert (out.result, out.handoff) == ("handoff", FORM)
    kept = anyform.unconfirmed(JOB.id)
    assert kept["handoff"] and kept["filled"]["Why Acme?"].startswith("Your platform")
    assert kept["left"] == ["Pronouns", "I agree to the privacy notice"] and kept["resume"] == str(resume)


def test_the_job_page_lists_the_answers_and_gives_the_named_file(run_dir, monkeypatch):
    from backend.api import app as webapp
    from backend.api import auto_apply
    resume = run_dir / "Jordan_Example_Resume_Acme.pdf"
    anyform.keep_unconfirmed(JOB.id, {"handoff": True, "url": FORM, "resume": str(resume), "cover_letter": None,
                                      "filled": {"Full name": "Jordan Example", "Empty": "  "}, "left": ["Pronouns"]})
    h = auto_apply._handoff(anyform.unconfirmed(JOB.id))
    assert h["resume"] == "Jordan_Example_Resume_Acme.pdf" and h["letter"] is None
    assert h["answers"] == [{"q": "Full name", "a": "Jordan Example"}] and h["left"] == ["Pronouns"]
    client = webapp.app.test_client()
    got = client.get(f"/job/{JOB.id}/auto-apply/handoff/resume")
    assert got.status_code == 200 and got.data == b"%PDF-1.4"
    assert "Jordan_Example_Resume_Acme.pdf" in got.headers["Content-Disposition"]
    assert client.get(f"/job/{JOB.id}/auto-apply/handoff/letter").status_code == 404
    # Never a file from outside the app's own run folders.
    anyform.keep_unconfirmed(JOB.id, {"handoff": True, "resume": "/etc/hosts"})
    assert client.get(f"/job/{JOB.id}/auto-apply/handoff/resume").status_code == 404


def test_the_run_opens_the_form_in_the_persons_own_browser_when_it_ends(monkeypatch):
    order = []

    class Context:
        def new_page(self):
            return SimpleNamespace(is_closed=lambda: False, close=lambda: None)

        def close(self):
            order.append("app window closed")
    applicant = SimpleNamespace(stop_before_submit=True, answers={}, job=None, job_answers={})
    monkeypatch.setattr(runner, "load", lambda path: applicant)
    monkeypatch.setattr(runner, "check_ready", lambda a: [])
    monkeypatch.setattr(runner, "candidates", lambda *a: ([SimpleNamespace(job_id=JOB.id)], 5))
    monkeypatch.setattr(runner, "summarise", lambda q: "1 job")

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, model, job_id):
            return JOB

        def expunge(self, job):
            pass
    monkeypatch.setattr(runner, "get_session", Session)
    monkeypatch.setattr("backend.apply.prepare.load", lambda: {})
    monkeypatch.setattr(runner, "apply_one", lambda *a, **k: runner.Outcome(
        JOB.id, "Acme", "x", "handoff", handoff=FORM))
    monkeypatch.setattr(runner, "open_own_browser", lambda url: order.append(f"opened {url}") or True)
    cfg = SimpleNamespace(get=lambda key, default=None: default)
    runner.run(cfg, job_id=JOB.id, browser_factory=Context)
    # Opened after the loop and its clean-up (runner.run), never while the app's window is up.
    assert order == [f"opened {FORM}"]
