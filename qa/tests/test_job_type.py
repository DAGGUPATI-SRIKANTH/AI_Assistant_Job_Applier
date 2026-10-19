"""What kind of job a posting is -- full-time, contract, contract-to-hire... -- read from what it
states, shown as a column, and corrected by the person on the job's page."""

import pytest

from backend.core.jobfields import derived_for, job_type, type_of
from data_engineering.db import session as session_mod
from data_engineering.db.models import Job
from data_engineering.db.session import get_session, init_engine
from data_engineering.scraper.base import RawJob


@pytest.mark.parametrize("title, text, stated, kind", [
    ("Senior DevOps Engineer (Contract)", "", None, "contract"),
    ("Contract DevOps Engineer", "", None, "contract"),
    ("W2 Role - Cloud Data Engineer", "", None, "contract"),
    ("Cloud Engineer - C2H", "", None, "contract-to-hire"),
    ("Platform Engineer", "This is a 6-month contract with possible extension.", None, "contract"),
    ("Platform Engineer", "Contract to hire after 90 days.", None, "contract-to-hire"),
    ("Platform Engineer", "Employment Type: Full-time\nWe build things.", None, "full-time"),
    ("Platform Engineer", "Job type: Contract", None, "contract"),                  # a job alert's line
    ("Platform Engineer", "This is a full-time contract role.", None, "contract"),   # a contract, all the same
    ("Platform Engineer", "", "Full-time", "full-time"),                             # Lever's field
    ("Platform Engineer", "", "FullTime", "full-time"),                              # Ashby's field
    ("Platform Engineer", "This is a 12-month contract.", "Full-time", "contract"),
    ("Software Engineering Intern", "", None, "internship"),
    ("Part-time DevOps Consultant", "", None, "part-time"),
    ("SRE", "Duration: 12 months\nRate: $80/hr", None, "contract"),
    # Benefits and pay said for full-time employees: a direct employer's full-time role.
    ("Platform Engineer", "The salary range for full-time employees for this position is below.", None, "full-time"),
    ("Platform Engineer", "We offer great benefits to full-time employees.", None, "full-time"),
    ("Platform Engineer", "Employment Type: Full-time, direct W2 (no C2C, no 1099).", None, "full-time"),
    ("Backend Engineer (Full-time W2)", "", None, "full-time"),
    ("Container Platform Engineer", "100% remote, full-time, direct W2 position with us.", None, "full-time"),
    ("Data Engineer", "Must-have skills - W2 role, 12 months.", None, "contract"),
    ("Cloud Engineer - W2 Only, No C2C", "", None, "contract"),
])
def test_what_the_posting_says_it_is(title, text, stated, kind):
    assert job_type(title, text, None, stated) == kind


@pytest.mark.parametrize("title, text", [
    ("Contracts Manager", ""),                                     # a job about contracts
    ("Senior Contract Manager", "Manage vendor contracts and negotiate contract terms."),
    ("Internal Tools Engineer", ""),
    ("Site Reliability Engineer", "You will work with contractors and manage government contracts."),
    ("Platform Engineer", "Most of our teams work from the office full time."),  # where, not what kind
    ("Platform Engineer", "Full-time and part-time employees working 30 hours a week."),
    ("Platform Engineer", "This is not a contract role."),
    # The employer's government contract, not a contract job.
    ("Systems Engineer", "Maintain the required clearance throughout the contract period of performance."),
    ("Systems Engineer", "You will work on a contract supporting the Navy. This is a DoD contract supporting ships."),
    ("DevOps Engineer", ""),
])
def test_nothing_stated_is_not_stated(title, text):
    assert job_type(title, text) is None


def test_board_words_read_as_kinds():
    assert [type_of(w) for w in ("Full-time", "FullTime", "Regular Full-Time", "Permanent", "Contract to hire",
                                 "Contingent Worker", "Intern", "PartTime", "Temporary", "", None)] == \
        ["full-time", "full-time", "full-time", "full-time", "contract-to-hire", "contract", "internship",
         "part-time", "temporary", None, None]


def test_the_persons_choice_outlives_reading_the_posting_again():
    fields = {"job_type": "contract", "workplace": "remote"}
    assert derived_for(Job(job_type_manual=True), fields) == {"workplace": "remote"}
    assert derived_for(Job(job_type_manual=None), fields) == fields


@pytest.fixture
def db():
    before = (session_mod._engine, session_mod._SessionFactory)
    init_engine("sqlite:///:memory:")
    yield
    session_mod._engine, session_mod._SessionFactory = before


def raw(external="1", stated=None, title="Platform Engineer", text="We build the platform."):
    return RawJob(source="lever", company="Globex", company_slug="globex", external_id=external, title=title,
                  location="Remote", description=text, application_url="https://jobs.lever.co/globex/1",
                  job_type=stated)


def test_the_boards_word_is_kept_and_read_again_when_it_changes(db):
    from data_engineering.scraper.runner import _persist
    _persist([raw(stated="Full-time")])
    with get_session() as s:
        job = s.query(Job).one()
        assert (job.job_type, job.stated_job_type) == ("full-time", "Full-time")
    _persist([raw(stated="Contract")])                     # same text, the board now says contract
    with get_session() as s:
        assert s.query(Job).one().job_type == "contract"
        s.query(Job).one().job_type, s.query(Job).one().job_type_manual = "full-time", True
    _persist([raw(stated="Part-time")])                    # the person's choice stays
    with get_session() as s:
        job = s.query(Job).one()
        assert (job.job_type, job.stated_job_type) == ("full-time", "Part-time")


def test_the_job_page_shows_it_and_takes_a_correction(db, monkeypatch):
    import yaml

    from backend.api import app as webapp
    from backend.config.loader import DEFAULT_CONFIG_PATH, Config
    monkeypatch.setattr(webapp, "_cfg", Config(yaml.safe_load(DEFAULT_CONFIG_PATH.read_text()) or {}))
    with get_session() as s:
        s.add(Job(id=7, source="lever", company="Globex", company_slug="globex", external_id="7", content_hash="h",
                  title="Platform Engineer (Contract)", description="We build.", application_url="https://x",
                  job_type="contract"))
    with get_session() as s:
        row = webapp.job_row(s.get(Job, 7))
    assert (row["jobType"], row["jobTypeFull"], row["jobTypeManual"]) == ("Contract", "Contract", False)
    c = webapp.app.test_client()
    page = c.get("/job/7").get_data(as_text=True)
    assert 'id="jt-now">Contract<' in page and 'id="jt-edit"' in page and 'value="contract-to-hire"' in page
    r = c.post("/job/7/job-type", json={"type": "full-time"}).get_json()
    assert r == {"ok": True, "type": "full-time", "label": "Full-time", "manual": True}
    assert "(set by you)" in c.get("/job/7").get_data(as_text=True)
    assert c.post("/job/7/job-type", json={"type": "weekly"}).status_code == 400
    r = c.post("/job/7/job-type", json={"reset": True}).get_json()
    assert (r["type"], r["manual"]) == ("contract", False)                # back to the posting's reading
    assert c.post("/job/7/job-type", json={"type": ""}).get_json()["label"] == ""    # "not stated"


# A posting with a long run of blanks once took minutes to read: patterns that let blanks in at more
# than one place tried every way of sharing the run out. Reading stays quick on such text now.
SLOW_ONCE = {
    "a number, then blanks": "1" + " " * 30000 + "x",
    "full-time, then blanks": "full-time" + " " * 30000 + "x",
    "blanks either side of a number": "duration:" + " " * 15000 + "5" + " " * 15000 + "x",
    "one very long word": "this is a " + "a" * 100000,
    "blank lines": "6" + " \n" * 15000 + "months contract",
    "numbers and blanks": "1 " * 20000,
}


@pytest.mark.parametrize("name", SLOW_ONCE)
def test_a_posting_full_of_blanks_is_read_quickly(name):
    import time

    from backend.core.jobfields import derive
    text = SLOW_ONCE[name]
    started = time.perf_counter()
    derive("Engineer " + " " * 3000 + "W2", "Remote " + " " * 3000, text, text)
    assert time.perf_counter() - started < 2.0, name
