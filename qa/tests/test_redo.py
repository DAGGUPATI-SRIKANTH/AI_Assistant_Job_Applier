"""A tailored resume made from a master since repaired is made again before it is sent."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.apply import prepare, runner
from ml.tailoring import pipeline

JOB = SimpleNamespace(id=11127, company="Acme", title="Cloud Engineer", external_id="x", description="Run Azure.")
WHY = "made from your resume before its repair"


@pytest.fixture(autouse=True)
def redo_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "REDO", tmp_path / "redo.json")
    monkeypatch.setattr(pipeline, "JOBS", tmp_path / "jobs")
    return tmp_path


def test_a_marked_job_says_why_until_a_new_version_takes_its_place(redo_file, monkeypatch):
    pipeline.mark_redo([11127, 11498], WHY)
    assert pipeline.needs_redo(11127) == WHY and pipeline.needs_redo(7) is None
    # A rewrite placed as the job's resume clears the mark; one that is not placed keeps it.
    monkeypatch.setattr(pipeline, "place_for_job", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "source_of", lambda folder: "new")
    folder = redo_file / "runs" / "r2"
    folder.mkdir(parents=True)
    assert pipeline.finish_job_run(11127, folder, redo_file / "out.docx", 80.0)
    assert pipeline.needs_redo(11127) is None and pipeline.needs_redo(11498) == WHY


def test_auto_apply_never_attaches_the_old_file(redo_file, monkeypatch):
    old = redo_file / "old.pdf"
    old.write_text("made from the damaged master")
    monkeypatch.setattr(runner, "tailored_resume", lambda cfg, job: old)
    monkeypatch.setattr(runner, "selected_master", lambda cfg, job: redo_file / "selected.pdf")
    pipeline.mark_redo([JOB.id], WHY)

    asked = []

    def tailor_again(cfg, job, again=False):
        asked.append(again)
        pipeline.redo_done(job.id)              # what finish_job_run does when the new one is placed
    monkeypatch.setattr(prepare, "_tailor", tailor_again)
    assert runner.choose_resume(None, JOB) == (old, "tailored just now") and asked == [True]

    # Tailoring fails: the person's own lines go, not the damaged file.
    pipeline.mark_redo([JOB.id], WHY)
    monkeypatch.setattr(prepare, "_tailor", lambda cfg, job, again=False: 1 / 0)
    resume, note = runner.choose_resume(None, JOB)
    assert resume == redo_file / "selected.pdf" and "unavailable" in note
    assert runner.choose_resume(None, JOB, tailor=False)[0] == redo_file / "selected.pdf"


def test_the_morning_preparation_tailors_a_marked_job_again(monkeypatch):
    monkeypatch.setattr(pipeline, "job_best", lambda job_id: ("old-run", 87.0))

    class Started(Exception):
        pass
    monkeypatch.setattr(pipeline, "new_run", lambda: (_ for _ in ()).throw(Started()))
    monkeypatch.setattr("ml.resume.pipeline.base_resume_path", lambda cfg: Path("base.docx"))
    assert prepare._tailor(None, JOB) == {"run": "old-run", "ats": 87.0}      # done already: kept
    pipeline.mark_redo([JOB.id], WHY)
    with pytest.raises(Started):
        prepare._tailor(None, JOB)                                            # marked: made again
