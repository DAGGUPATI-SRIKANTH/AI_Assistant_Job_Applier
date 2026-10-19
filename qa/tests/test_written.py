"""Written answers: kept from the forms they went out on, found again for questions like them."""

import json
from types import SimpleNamespace as NS

import pytest

from backend.apply import written
from data_engineering.db import session as session_mod
from data_engineering.db.models import Job
from data_engineering.db.session import get_session, init_engine

ESTATE_Q = "Describe the deepest AWS estate you have owned: accounts, IAM, and how changes reached production."
ESTATE_A = ("At Globex I owned 40 AWS accounts under one Organization. People signed in through IAM Identity "
            "Center with permission sets per team, and every change went through Terraform plans reviewed in "
            "pull requests before a pipeline applied them, first to staging and then to production.")
DR_Q = "Tell us about a disaster recovery plan you designed across regions."
DR_A = ("I designed warm-standby recovery for the payments service: databases replicated to a second region, "
        "infrastructure rebuilt from code, and a failover rehearsed every quarter against a 15-minute RPO and a "
        "one-hour RTO, with the runbook kept next to the code.")


def test_a_written_answer_is_told_from_a_fact_and_a_cover_letter():
    assert written.is_written(ESTATE_Q, ESTATE_A)
    assert not written.is_written("Years of Kubernetes experience?", "5")
    assert not written.is_written("Cover Letter", ESTATE_A)
    assert not written.is_written("Additional information", ESTATE_A)
    # The cover letter, typed by the app into a box labelled otherwise, is not an answer.
    assert not written.is_written("Anything you would like the team to know?", "Dear Hiring Manager,\n" + ESTATE_A)


def test_each_answer_is_kept_once_and_can_be_let_go():
    assert written.keep(ESTATE_Q, ESTATE_A, "Globex", "Platform Engineer", 7)
    assert not written.keep(ESTATE_Q + "  ", "  " + ESTATE_A)               # the same, spaced otherwise
    kept = written.keep_from({DR_Q: DR_A, "Years of Kubernetes experience?": "5", "Cover letter": DR_A * 2},
                             "Initech", "SRE")
    assert kept == [DR_Q]
    [estate] = [i for i in written.load() if i["question"] == ESTATE_Q]
    assert (estate["company"], estate["title"], estate["job_id"]) == ("Globex", "Platform Engineer", 7)
    assert written.remove(estate["id"]) and not written.remove(estate["id"])
    assert [i["question"] for i in written.load()] == [DR_Q]


def test_a_question_starred_or_cut_short_is_still_the_one_answer():
    cut = ESTATE_Q[:60]                                   # how some forms' labels were kept
    assert written.keep(cut, ESTATE_A, "Globex")
    assert not written.keep(ESTATE_Q + " *", ESTATE_A, "Globex")
    assert [i["question"] for i in written.load()] == [ESTATE_Q + " *"]    # the whole question took its place
    assert not written.keep(cut, ESTATE_A) and not written.keep(ESTATE_Q.upper(), ESTATE_A)
    assert len(written.load()) == 1


def test_the_same_question_in_another_employers_words_finds_the_earlier_answer():
    written.keep(ESTATE_Q, ESTATE_A, "Globex")
    written.keep(DR_Q, DR_A, "Initech")
    [first, *_] = written.similar("What is the largest AWS environment you have managed, including identity "
                                  "and access and how changes were deployed?")
    assert first["question"] == ESTATE_Q
    [first, *_] = written.similar("Walk us through a failover or recovery design spanning multiple regions, "
                                  "with the RPO and RTO you held.")
    assert first["question"] == DR_Q
    assert written.similar("Why do you want to work at Acme?") == []
    assert written.similar("Salary?") == []


def test_the_draft_builds_on_the_earlier_answer_without_naming_its_employer(monkeypatch):
    from backend.apply import essays
    written.keep(ESTATE_Q, ESTATE_A, "Globex")
    asked = {}

    def complete(system, user, cfg, **kw):
        asked["user"] = user
        return NS(text=json.dumps({"answer": "I owned 40 AWS accounts with IAM Identity Center and Terraform.",
                                   "gaps": []}))

    monkeypatch.setattr("ml.resume.llm.complete", complete)
    job = NS(title="Cloud Engineer", company="Acme", description="AWS")
    got = essays.draft(None, job, "Tell us about the largest AWS environment you have managed.", "Ran AWS.")
    assert "THE CANDIDATE'S OWN EARLIER ANSWER (to Globex)" in asked["user"] and ESTATE_A in asked["user"]
    assert "never name the other employer" in asked["user"]
    assert got["unsupported_numbers"] == []                  # 40 is in the person's own earlier answer
    assert got["from_earlier"] == [{"question": ESTATE_Q, "company": "Globex"}]


@pytest.fixture
def job(monkeypatch):
    before = (session_mod._engine, session_mod._SessionFactory)
    init_engine("sqlite:///:memory:")
    monkeypatch.setattr("backend.apply.runner.say", lambda line: None)
    with get_session() as s:
        s.add(Job(id=9, source="greenhouse", company="Acme", company_slug="acme", external_id="9",
                  content_hash="h", title="Cloud Engineer", status="Not Applied", is_open=True,
                  application_url="https://boards.greenhouse.io/acme/jobs/9"))
    with get_session() as s:
        row = s.get(Job, 9)
        s.expunge(row)
    yield row
    session_mod._engine, session_mod._SessionFactory = before


def _settle(job, verdict, answers, filled=None, how="you"):
    from backend.apply import runner
    from backend.apply.greenhouse import FillResult
    outcome = runner.Outcome(job.id, job.company, job.title, "")
    result = FillResult(job_id=job.id, url="https://x/9", filled=filled or {"First Name": "Sam"})
    runner._settle(outcome, job, verdict, how, "https://x/9", "/r.pdf", "tailored", None, result, "/e",
                   answers)
    return runner


def test_a_sent_form_keeps_the_written_answers_and_records_every_final_answer(job):
    runner = _settle(job, "submitted", {ESTATE_Q: ESTATE_A, "Years of AWS experience?": "8"})
    assert [(i["question"], i["company"], i["job_id"]) for i in written.load()] == [(ESTATE_Q, "Acme", 9)]
    record = json.loads(runner.AUDIT_LOG.read_text().splitlines()[-1])
    assert record["filled"] == {"First Name": "Sam", ESTATE_Q: ESTATE_A, "Years of AWS experience?": "8"}
    assert record["filled_by_app"] == {"First Name": "Sam"}


def test_an_answer_written_on_a_form_left_unsent_is_still_kept(job):
    runner = _settle(job, "skipped", {DR_Q: DR_A})
    assert [i["question"] for i in written.load()] == [DR_Q]
    assert not runner.AUDIT_LOG.exists()                     # not sent, so not an application


def test_a_form_the_app_sent_itself_keeps_the_answers_it_typed(job):
    """Answers given for the job on the Apply page are typed in by the app; sent with no one watching,
    there is no last reading of the form, and what the app typed is what went."""
    _settle(job, "submitted", {}, filled={"First Name": "Sam", ESTATE_Q: ESTATE_A,
                                          "Why are you interested?": "Dear Hiring Manager, " + DR_A}, how="auto")
    assert [i["question"] for i in written.load()] == [ESTATE_Q]


def test_what_was_changed_on_the_form_is_kept_not_what_the_app_typed_first(job):
    edited = ESTATE_A.replace("40 AWS accounts", "45 AWS accounts")
    _settle(job, "submitted", {ESTATE_Q: edited}, filled={ESTATE_Q[:60]: ESTATE_A})
    assert [i["answer"] for i in written.load()] == [edited]


def test_the_questions_page_lists_them_and_deletes_one():
    from backend.api import app as webapp
    written.keep(ESTATE_Q, ESTATE_A, "Globex", when="2026-10-01T10:00:00+00:00")
    written.keep(DR_Q, DR_A, "Initech", when="2026-10-05T10:00:00+00:00")
    client = webapp.app.test_client()
    items = client.get("/apply/written.json").get_json()["items"]
    assert [i["company"] for i in items] == ["Initech", "Globex"]                 # newest first
    assert client.post(f"/apply/written/{items[0]['id']}/delete").get_json()["ok"]
    assert [i["company"] for i in client.get("/apply/written.json").get_json()["items"]] == ["Globex"]
    assert "Your written answers" in client.get("/apply/questions").get_data(as_text=True)
