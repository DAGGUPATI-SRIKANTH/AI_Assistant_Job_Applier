"""Email about applications: read, tied to the right application, and turned into its next stage."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.apply import inbox, tracker
from data_engineering.db import session as session_mod
from data_engineering.db.models import Job
from data_engineering.db.session import get_session, init_engine

NOW = datetime.now(timezone.utc)
# What Gmail adds to mail that really came from Lever's hiring system.
LEVER_AUTH = ("mx.google.com; dkim=pass header.i=@hire.lever.co header.s=k1 header.b=abc; spf=pass "
              "smtp.mailfrom=no-reply@hire.lever.co; dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from=hire.lever.co")
# Lever's signature, covering the To line, as on Lever's real mail.
LEVER_SIGNED = [("hire.lever.co", ["from", "to", "subject", "date"])]


@pytest.fixture
def db(monkeypatch, tmp_path):
    before = (session_mod._engine, session_mod._SessionFactory)
    init_engine("sqlite:///:memory:")
    monkeypatch.setattr(tracker, "STAGES_FILE", tmp_path / "stages.json")
    monkeypatch.setattr(inbox, "UPDATES", tmp_path / "updates.json")
    monkeypatch.setattr(inbox, "STATE", tmp_path / "state.json")
    monkeypatch.setattr("backend.apply.anyform.UNCONFIRMED", tmp_path / "unconfirmed.json")
    monkeypatch.setattr(inbox, "_applicant_email", lambda: "")      # never the real profile
    naive = lambda d: d.replace(tzinfo=None)
    with get_session() as s:
        s.add(Job(id=1, source="ashby", company="Initech", company_slug="initech", external_id="1", content_hash="a",
                  title="Platform Engineer", status="Not Applied", is_open=True,
                  found_at=naive(NOW - timedelta(days=3)), application_url="https://jobs.ashbyhq.com/initech/1"))
        s.add(Job(id=2, source="jazzhr", company="globex inc.", company_slug="globex", external_id="2",
                  content_hash="b", title="Senior DevOps Engineer", status="Applied", is_open=True,
                  applied_at=naive(NOW - timedelta(days=5)), found_at=naive(NOW - timedelta(days=9)),
                  application_url="https://x/2"))
        s.add(Job(id=3, source="lever", company="Hooli AI", company_slug="hooliai", external_id="3",
                  content_hash="c", title="Staff Infrastructure Engineer (R1001)", status="Not Applied",
                  is_open=True, found_at=naive(NOW - timedelta(days=2)), application_url="https://x/3"))
        s.add(Job(id=4, source="lever", company="Hooli AI", company_slug="hooliai", external_id="4",
                  content_hash="d", title="Staff Platform Engineer (R1002)", status="Closed", is_open=False,
                  found_at=naive(NOW - timedelta(days=20)), application_url="https://x/4"))
    runs = tmp_path / "apply-runs"
    for job_id in (1, 3):
        (runs / f"{job_id}-20261017-120000").mkdir(parents=True)
    yield tmp_path
    session_mod._engine, session_mod._SessionFactory = before


def test_what_a_message_says():
    assert inbox.classify("Thank you for applying to Initech",
                          "Our team will review your application and should we find a match, "
                          "we'll reach out for an initial screen.") == "confirmation"
    assert inbox.classify("Your Application with Globex",
                          "Thank you for applying. Unfortunately, we have decided not to move forward.") == "rejection"
    assert inbox.classify("Next steps with Acme", "We would like to schedule a call to discuss the role: "
                          "https://calendly.com/acme/30min") == "interview"
    assert inbox.classify("Acme interview", "Your final round onsite is on Friday.") == "final"
    assert inbox.classify("Your Acme coding challenge", "Please complete the assessment by Monday.") == "assessment"
    assert inbox.classify("Latest Jobs for you - DevOps", "15 new jobs") is None
    assert inbox.classify("Lunch on Friday?", "See you then.") is None


def test_a_message_is_tied_to_the_application_it_names(db):
    among = inbox.targets()
    found = inbox.match("Initech <no-reply@ashbyhq.com>", "Thank you for applying to Initech", "", among, NOW)
    assert found and found.job_id == 1
    # A recruiter's cold mail names no company applied to.
    assert inbox.match("Shivam <g@centraprise.com>", "Immediate Interview // Hiring // Java developer", "",
                       among, NOW) is None
    # Mail from before the app had the job is about another application at that company.
    assert inbox.match("Hooli AI <no-reply@hire.lever.co>", "Thank you for your application to Hooli AI", "",
                       among, NOW - timedelta(days=15)) is None


def test_a_receipt_marks_the_job_applied_on_the_day_it_came(db):
    [one] = [t for t in inbox.targets() if t.job_id == 1]
    sent = NOW - timedelta(hours=2)
    update = inbox.act(one, "confirmation", {"id": "m1", "subject": "Thank you for applying to Initech",
                                             "from": "Initech <no-reply@ashbyhq.com>", "date": sent})
    assert update["change"] == "Marked Applied"
    from backend.apply import runner
    import json
    record = json.loads(runner.AUDIT_LOG.read_text().splitlines()[-1])
    assert record["how"] == "email" and record["confirmed_by_email"] == "Thank you for applying to Initech"
    with get_session() as s:
        job = s.get(Job, 1)
        assert job.status == "Applied" and abs(job.applied_at.replace(tzinfo=timezone.utc) - sent) < timedelta(seconds=1)


def test_a_rejection_becomes_the_applications_stage_once(db):
    [ub] = [t for t in inbox.targets() if t.job_id == 2]
    message = {"id": "m2", "subject": "Your Application with Globex", "from": "Globex <x@applytojob.com>",
               "date": NOW - timedelta(days=1)}
    assert inbox.act(ub, "rejection", message)["change"] == "Rejected"
    assert inbox.act(ub, "rejection", {**message, "id": "m3"}) is None          # once, not per email
    a = tracker.application(2)
    assert a["status"] == "Rejected" and a["stages"][0]["note"].startswith("Email: Your Application with Globex")


def test_a_receipt_for_a_job_applied_to_outside_the_app_is_found_by_its_title(db):
    found = inbox.lookup_job("Hooli AI <no-reply@hire.lever.co>", "Thank you for your application to Hooli AI",
                             "We received your application for Staff Platform Engineer (R1002), and we are delighted.")
    assert found and found.job_id == 4
    assert inbox.lookup_job("Hooli AI <no-reply@hire.lever.co>", "Thank you for your application to Hooli AI",
                            "We received your application.") is None               # no title: not sure enough


def test_the_pop_up_shows_unseen_updates_until_dismissed(db, monkeypatch):
    from backend.api import app as webapp
    monkeypatch.setattr("backend.api.inbox_pages.cfg", lambda: None)
    inbox._write(inbox.UPDATES, [{"id": "u1", "job_id": 1, "company": "Initech", "title": "Platform Engineer",
                                  "kind": "confirmation", "label": "Application received", "change": "Marked Applied",
                                  "subject": "Thank you for applying to Initech", "from": "Initech", "date": NOW.isoformat(),
                                  "seen": False}])
    client = webapp.app.test_client()
    assert [u["id"] for u in client.get("/inbox/updates?unseen=1").get_json()["updates"]] == ["u1"]
    assert client.post("/inbox/updates/seen", json={"ids": ["u1"]}).get_json()["dismissed"] == 1
    assert client.get("/inbox/updates?unseen=1").get_json()["updates"] == []


def test_a_receipt_and_the_rejection_after_it_in_one_look(db, monkeypatch):
    """One look: the receipt made it an application, and the rejection three days later had to count too."""
    sent = NOW - timedelta(days=4)
    mail = [
        {"uid": b"1", "id": "z1", "from": "Hooli AI <no-reply@hire.lever.co>", "date": sent, "auth": LEVER_AUTH,
         "address": "no-reply@hire.lever.co", "name": "Hooli AI", "to": ["me@example.com"], "signed": LEVER_SIGNED,
         "subject": "Thank you for your application to Hooli AI"},
        {"uid": b"2", "id": "z2", "from": "Hooli AI <no-reply@hire.lever.co>", "date": sent + timedelta(days=3),
         "auth": LEVER_AUTH, "address": "no-reply@hire.lever.co", "name": "Hooli AI", "to": ["me@example.com"],
         "signed": LEVER_SIGNED, "subject": "Your Hooli AI Application: Staff Platform Engineer"},
    ]
    texts = {b"1": "We received your application for Staff Platform Engineer (R1002), and we are delighted.",
             b"2": "For the Staff Platform Engineer (R1002) role, we have identified other candidates and will not "
                   "be moving forward with your candidacy at this time."}

    class FakeBox:
        conn = None
        def __init__(self, creds): pass
        def body(self, uid): return texts[uid]
        def close(self): pass

    monkeypatch.setattr(inbox, "Box", FakeBox)
    monkeypatch.setattr(inbox, "_search", lambda conn, days: [m["uid"] for m in mail])
    monkeypatch.setattr(inbox, "_headers", lambda conn, uids: mail)
    monkeypatch.setattr(inbox, "credentials", lambda: ("me@example.com", "pw"))
    monkeypatch.setattr(inbox, "_notify", lambda found: None)
    result = inbox.check(days=10)
    assert [u["change"] for u in result["updates"]] == ["Marked Applied", "Rejected"]
    assert tracker.application(4)["status"] == "Rejected"


def test_a_role_the_employer_closed_is_told_from_a_rejection():
    closed = [("Update on your application", "The position you applied for has been closed."),
              ("Your application", "Unfortunately, the requisition has been cancelled and we will not be moving "
                                   "forward with any candidates."),
              ("Acme careers", "This position is no longer available. Thank you for your interest."),
              ("Acme", "We are no longer accepting applications for this role.")]
    assert [inbox.classify(s, b) for s, b in closed] == ["closed"] * 4
    assert inbox.classify("Acme", "Unfortunately, the position has been filled.") == "rejection"
    # A receipt saying what may happen, and the person's own withdrawal, are not a closed role.
    assert inbox.classify("Thank you for applying to Acme",
                          "This posting will be closed once the position is filled.") == "confirmation"
    assert inbox.classify("Your application", "Your application for the role has been withdrawn as you asked.") is None


def test_a_closed_role_becomes_the_applications_stage_and_leaves_its_status(db):
    [ub] = [t for t in inbox.targets() if t.job_id == 2]
    message = {"id": "m9", "subject": "Update on your Globex application", "from": "Globex <x@applytojob.com>",
               "date": NOW - timedelta(days=1)}
    update = inbox.act(ub, "closed", message)
    assert update["change"] == "Position closed" and update["label"] == "Position closed"
    a = tracker.application(2)
    assert a["current"] == "Position closed" and a["status"] == "Applied"


# -- only mail that provably came from the employer or its hiring system moves an application ------

def test_a_sender_name_or_a_look_alike_domain_moves_nothing(db):
    among = inbox.targets()
    rejection = ("Your application", "Unfortunately the position has been closed.")
    # Anyone can write "Globex" as a sender's name, or make "globex" the first part of their own domain.
    assert inbox.match("Globex <hr@attacker.example>", *rejection, among, NOW) is None
    assert inbox.match("Talent <hr@globex.attacker.example>", *rejection, among, NOW) is None
    # The employer's own domain, and a hiring system writing for it, still count.
    assert inbox.match("Talent <hr@globex.com>", *rejection, among, NOW).job_id == 2
    assert inbox.match("Globex <no-reply@applytojob.com>", *rejection, among, NOW).job_id == 2


def test_only_authenticated_mail_counts():
    assert inbox.verified("no-reply@hire.lever.co", LEVER_AUTH) == "lever.co"
    assert inbox.verified("hr@globex.com", "mx.google.com; dkim=pass header.d=globex.com; "
                                           "dmarc=pass header.from=globex.com") == "globex.com"
    assert inbox.verified("hr@globex.com", "mx.google.com; spf=softfail; dmarc=fail header.from=globex.com") is None
    # Signed, but by someone else's domain: not proof the mail is Globex's.
    assert inbox.verified("hr@globex.com", "mx.google.com; dkim=pass header.i=@sender.example") is None
    assert inbox.verified("hr@globex.com", "") is None


def test_a_check_written_by_the_sender_proves_nothing():
    # Not Gmail's own header; a pass tucked into a comment; and a ";" smuggled in the quoted envelope
    # sender, which would otherwise read as a result of its own.
    assert inbox.verified("hr@globex.com", "globex.com; dmarc=pass header.from=globex.com") is None
    assert inbox.verified("hr@globex.com", "mx.google.com; dkim=neutral (dkim=pass header.d=globex.com) "
                                           "header.d=sender.example") is None
    assert inbox.verified("hr@globex.com", 'mx.google.com; spf=pass smtp.mailfrom="x;dkim=pass header.d=globex.com"'
                                           '@sender.example; dmarc=fail header.from=globex.com') is None


class FakeImap:
    """A mailbox answering FETCH with these raw headers, as Gmail's IMAP does."""

    def __init__(self, raw):
        self.raw = raw

    def uid(self, command, batch, what):
        return "OK", [(b"1 (UID 7 BODY[HEADER.FIELDS (FROM)] {%d}" % len(self.raw), self.raw), b")"]


def test_only_gmails_own_check_and_one_plain_sender_are_read():
    gmail = b"Authentication-Results: mx.google.com; dmarc=fail header.from=hire.lever.co\r\n"
    forged = b"Authentication-Results: mx.google.com; dkim=pass header.i=@hire.lever.co; dmarc=pass header.from=hire.lever.co\r\n"
    head = inbox._headers(FakeImap(gmail + forged + b"From: Hooli AI <no-reply@hire.lever.co>\r\nSubject: Hi\r\n\r\n"),
                          [b"7"])[0]
    assert head["address"] == "no-reply@hire.lever.co" and inbox.verified(head["address"], head["auth"]) is None
    for sender in (b"From: no-reply@hire.lever.co <x@sender.example>",          # parsers read it two ways
                   b"From: no-reply@hire.lever.co, x@sender.example",          # two senders
                   b"From: Hooli AI <no-reply@hire.lever.co>\r\nFrom: X <x@sender.example>"):   # two From lines
        head = inbox._headers(FakeImap(forged + sender + b"\r\nSubject: Hi\r\n\r\n"), [b"7"])[0]
        assert head["address"] == "", sender


def test_platforms_anyone_can_send_through_and_free_mailboxes_speak_for_no_employer(db):
    among = inbox.targets()
    closed = ("Your application", "Unfortunately the Senior DevOps Engineer position has been closed.")
    for sender in ("Globex <messages-noreply@linkedin.com>", "Globex Recruiting <notifications@calendly.com>",
                   "Globex Careers <globex@gmail.com>"):
        assert inbox.match(sender, *closed, among, NOW) is None, sender
    assert inbox.registrable("us.greenhouse-mail.io") == "greenhouse-mail.io"
    assert inbox.registrable("mail.globex.co.uk") == "globex.co.uk"


def test_unauthenticated_mail_never_moves_an_application(db, monkeypatch):
    spoof = [{"uid": b"9", "id": "s1", "from": "Globex <no-reply@applytojob.com>", "date": NOW - timedelta(hours=2),
              "auth": "mx.google.com; spf=fail; dmarc=fail header.from=applytojob.com", "address": "no-reply@applytojob.com",
              "name": "Globex", "to": ["me@example.com"], "signed": [],
              "subject": "Your Application with Globex"}]

    class FakeBox:
        conn = None
        def __init__(self, creds): pass
        def body(self, uid): return "Unfortunately, the position has been closed."
        def close(self): pass
    monkeypatch.setattr(inbox, "Box", FakeBox)
    monkeypatch.setattr(inbox, "_search", lambda conn, days: [b"9"])
    monkeypatch.setattr(inbox, "_headers", lambda conn, uids: spoof)
    monkeypatch.setattr(inbox, "credentials", lambda: ("me@example.com", "pw"))
    monkeypatch.setattr(inbox, "_notify", lambda found: None)
    assert inbox.check(days=2)["updates"] == [] and tracker.application(2)["stages"] == []


def test_one_dmarc_result_from_gmail_and_a_signature_that_covers_who_it_was_for():
    two = "mx.google.com; dmarc=fail header.from=globex.com; dmarc=pass header.from=globex.com"
    assert inbox.verified("hr@globex.com", two) is None                          # one was written by someone else
    assert inbox.verified("hr@globex.com", "mx.google.com; dkim=pass header.d=globex.com") is None   # DKIM alone
    signed = "mx.google.com; dkim=pass header.d=globex.com; dmarc=pass header.from=globex.com"
    assert inbox.verified("hr@globex.com", signed, [("globex.com", ["from", "subject"])]) is None   # To not signed
    assert inbox.verified("hr@globex.com", signed, [("globex.com", ["from", "to", "subject"])]) == "globex.com"


def test_one_strict_reading_of_the_sender_and_one_mailbox_however_written():
    raw = b'From: "Hooli AI" <No-Reply@hire.lever.co>\r\nTo: Jordan <jordan.example+jobs@gmail.com>\r\n'\
          b'DKIM-Signature: v=1; a=rsa-sha256; d=hire.lever.co; s=k1;\r\n h=from:to:subject; bh=x; b=y\r\n\r\n'
    import email
    import email.policy
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert inbox.sender(msg) == ("no-reply@hire.lever.co", "Hooli AI")
    assert inbox.recipients(msg) == ["jordan.example+jobs@gmail.com"]
    assert inbox.signatures(msg) == [("hire.lever.co", ["from", "to", "subject"])]
    assert inbox.mailbox_key("jordan.example+jobs@gmail.com") == inbox.mailbox_key("JordanExample@googlemail.com")


def test_someone_elses_signed_mail_sent_on_moves_nothing(db, monkeypatch):
    replay = [{"uid": b"5", "id": "r1", "from": "Hooli AI <no-reply@hire.lever.co>", "date": NOW - timedelta(hours=1),
               "auth": LEVER_AUTH, "address": "no-reply@hire.lever.co", "name": "Hooli AI", "signed": LEVER_SIGNED,
               "to": ["someone.else@example.org"], "subject": "Your Hooli AI Application: Staff Platform Engineer"}]

    class FakeBox:
        conn = None
        def __init__(self, creds): pass
        def body(self, uid): return "For the Staff Platform Engineer (R1002) role, the position has been closed."
        def close(self): pass
    monkeypatch.setattr(inbox, "Box", FakeBox)
    monkeypatch.setattr(inbox, "_search", lambda conn, days: [b"5"])
    monkeypatch.setattr(inbox, "_headers", lambda conn, uids: replay)
    monkeypatch.setattr(inbox, "credentials", lambda: ("me@example.com", "pw"))
    monkeypatch.setattr(inbox, "_notify", lambda found: None)
    assert inbox.check(days=2)["updates"] == []
