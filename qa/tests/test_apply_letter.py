"""Auto-apply does the whole checklist: a job with no cover letter gets one, or goes on without."""

import pytest

from data_engineering.db import session as session_mod
from data_engineering.db.models import Job
from data_engineering.db.session import get_session, init_engine


class Cfg:
    def __init__(self, root):
        self.root = str(root)

    def get(self, key, default=None):
        return self.root if key == "resume.output_dir" else default


@pytest.fixture
def job(monkeypatch, tmp_path):
    before = (session_mod._engine, session_mod._SessionFactory)
    init_engine("sqlite:///:memory:")
    monkeypatch.setattr("backend.apply.runner.say", lambda line: None)
    with get_session() as s:
        s.add(Job(id=5, source="jazzhr", company="Papersave", company_slug="papersave", external_id="p5",
                  content_hash="h", title="DevOps Engineer", status="Not Applied", is_open=True,
                  application_url="https://papersave.applytojob.com/apply/p5"))
    with get_session() as s:
        row = s.get(Job, 5)
        s.expunge(row)
    yield row, Cfg(tmp_path)
    session_mod._engine, session_mod._SessionFactory = before


def test_a_job_without_a_letter_gets_one_written(job, monkeypatch):
    from backend.apply.runner import write_letter_if_missing
    from ml.resume.packet import LETTER_FILE, packet_dir
    row, cfg = job
    folder = packet_dir(cfg, row.company, row.title, row.external_id, job_id=row.id)

    def fake_write(cfg_, job_id):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / LETTER_FILE).write_text("Dear Papersave team, …")
        return type("Outcome", (), {"status": "written"})()

    monkeypatch.setattr("ml.resume.pipeline.write_letter", fake_write)
    assert write_letter_if_missing(cfg, row) and (folder / LETTER_FILE).exists()
    monkeypatch.setattr("ml.resume.pipeline.write_letter", lambda *a: pytest.fail("written twice"))
    assert write_letter_if_missing(cfg, row)                     # one already there: not written again


def test_a_letter_that_fails_leaves_the_job_as_it_was(job, monkeypatch):
    from backend.apply.runner import write_letter_if_missing
    row, cfg = job

    def rejected(cfg_, job_id):                                  # the page's writer marks it Manual Review
        with get_session() as s:
            s.get(Job, job_id).status = "Manual Review"
        return type("Outcome", (), {"status": "rejected"})()

    monkeypatch.setattr("ml.resume.pipeline.write_letter", rejected)
    assert write_letter_if_missing(cfg, row) is False
    with get_session() as s:
        assert s.get(Job, 5).status == "Not Applied"
