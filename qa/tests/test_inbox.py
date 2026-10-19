"""Email about applications: read, tied to the right application, and turned into its next stage."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.apply import inbox, tracker
from data_engineering.db import session as session_mod
from data_engineering.db.models import Job
from data_engineering.db.session import get_session, init_engine

NOW = datetime.now(timezone.utc)
DOMAINS = ("hire.lever.co", "globex.com", "sender.example")


@pytest.fixture(scope="module")
def keys():
    """A DKIM signing key for each test domain, made now, and the DNS record that publishes it."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    out = {}
    for domain in DOMAINS:
        key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption())
        der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        out[domain] = (pem, b"v=DKIM1; k=rsa; p=" + base64.b64encode(der))
    return out


@pytest.fixture
def dns(keys, monkeypatch):
    """The test domains' keys where the DKIM check looks them up (sel._domainkey.<domain>)."""
    def lookup(name, timeout=5):
        domain = name.decode().rstrip(".").split("._domainkey.")[-1]
        return keys[domain][1] if domain in keys else None
    monkeypatch.setattr(inbox, "DKIM_DNS", lookup)
    return keys


def signed(keys, sender, to, subject, body, by=None, cover=(b"from", b"to", b"subject"), length=False) -> bytes:
    """A message as it would arrive, DKIM-signed by `by` (the sender's own domain unless said)."""
    import dkim
    domain = by or sender.rpartition("@")[2].rstrip(">")
    msg = (f"From: {sender}\r\nTo: {to}\r\nSubject: {subject}\r\n"
           f"Date: Mon, 19 Oct 2026 09:00:00 +0000\r\n\r\n{body}\r\n").encode()
    return dkim.sign(msg, b"sel", domain.encode(), keys[domain][0], include_headers=list(cover), length=length) + msg


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


def fake_mailbox(monkeypatch, mail, raws):
    class FakeBox:
        conn = None
        def __init__(self, creds): pass
        def raw(self, uid): return raws[uid]
        def body(self, uid): return inbox.text_of(raws[uid])
        def close(self): pass
    monkeypatch.setattr(inbox, "Box", FakeBox)
    monkeypatch.setattr(inbox, "_search", lambda conn, days: [m["uid"] for m in mail])
    monkeypatch.setattr(inbox, "_headers", lambda conn, uids: mail)
    monkeypatch.setattr(inbox, "credentials", lambda: ("me@example.com", "pw"))
    monkeypatch.setattr(inbox, "_notify", lambda found: None)


def head(uid, sender, name, subject, sent, to="me@example.com"):
    """A message's header as the mailbox lists it. It arrived now: the test signs it now."""
    import time
    return {"uid": uid, "id": uid.decode(), "from": f"{name} <{sender}>", "address": sender, "name": name,
            "to": [to], "subject": subject, "date": sent, "arrived": time.time()}


def test_a_receipt_and_the_rejection_after_it_in_one_look(db, dns, monkeypatch):
    """One look: the receipt made it an application, and the rejection three days later had to count too."""
    sent = NOW - timedelta(days=4)
    lever = "no-reply@hire.lever.co"
    mail = [head(b"1", lever, "Hooli AI", "Thank you for your application to Hooli AI", sent),
            head(b"2", lever, "Hooli AI", "Your Hooli AI Application: Staff Platform Engineer", sent + timedelta(days=3))]
    raws = {b"1": signed(dns, f"Hooli AI <{lever}>", "me@example.com", mail[0]["subject"],
                         "We received your application for Staff Platform Engineer (R1002), and we are delighted."),
            b"2": signed(dns, f"Hooli AI <{lever}>", "me@example.com", mail[1]["subject"],
                         "For the Staff Platform Engineer (R1002) role, we have identified other candidates and "
                         "will not be moving forward with your candidacy at this time.")}
    fake_mailbox(monkeypatch, mail, raws)
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


def test_a_signature_by_the_senders_own_domain_checked_here_is_proof(dns):
    raw = signed(dns, "Talent <hr@globex.com>", "me@example.com", "Your application", "Thank you.")
    assert inbox.verified("hr@globex.com", raw) == "globex.com"
    lever = signed(dns, "Hooli AI <no-reply@hire.lever.co>", "me@example.com", "Thanks", "Received.")
    assert inbox.verified("no-reply@hire.lever.co", lever) == "lever.co"


def test_header_lines_prove_nothing(dns):
    # Everything a sender can write: a passing check of its own, and a signature that is only text.
    forged = (b"Authentication-Results: mx.google.com; dkim=pass header.d=globex.com; dmarc=pass header.from=globex.com\r\n"
              b"DKIM-Signature: v=1; a=rsa-sha256; d=globex.com; s=sel; h=from:to:subject; bh=AAAA; b=AAAA\r\n"
              b"From: Talent <hr@globex.com>\r\nTo: me@example.com\r\nSubject: Closed\r\n\r\nThe position has been closed.\r\n")
    assert inbox.verified("hr@globex.com", forged) is None


def test_someone_elses_signature_a_partly_signed_body_or_an_unsigned_to_line_prove_nothing(dns):
    other = signed(dns, "Globex <hr@globex.com>", "me@example.com", "Closed", "Closed.", by="sender.example")
    assert inbox.verified("hr@globex.com", other) is None                      # signed, by another domain
    no_to = signed(dns, "Globex <hr@globex.com>", "me@example.com", "Closed", "Closed.", cover=(b"from", b"subject"))
    assert inbox.verified("hr@globex.com", no_to) is None                      # whom it was for, unsigned
    part = signed(dns, "Globex <hr@globex.com>", "me@example.com", "Hi", "Thanks.", length=True)
    assert inbox.verified("hr@globex.com", part + b"The position has been closed.\r\n") is None   # l=
    good = signed(dns, "Globex <hr@globex.com>", "me@example.com", "Hi", "Thanks for applying.")
    assert inbox.verified("hr@globex.com", good.replace(b"Thanks for applying.", b"The role is closed.")) is None


def test_a_signature_counts_as_of_when_the_message_arrived(dns):
    import time
    raw = signed(dns, "Talent <hr@globex.com>", "me@example.com", "Your application", "Thank you.")
    assert inbox.verified("hr@globex.com", raw, at=time.time()) == "globex.com"
    # Signed long after it supposedly arrived: not the message that arrived then.
    assert inbox.verified("hr@globex.com", raw, at=time.time() - 2 * 86400) is None


class FakeImap:
    """A mailbox answering FETCH with these raw headers, as Gmail's IMAP does."""

    def __init__(self, raw):
        self.raw = raw

    def uid(self, command, batch, what):
        return "OK", [(b'1 (UID 7 INTERNALDATE "19-Oct-2026 09:00:05 +0000" BODY[HEADER.FIELDS (FROM)] {%d}'
                       % len(self.raw), self.raw), b")"]


def test_one_plain_sender_on_one_line_is_read_once():
    head = inbox._headers(FakeImap(b"From: Hooli AI <no-reply@hire.lever.co>\r\nTo: me@example.com\r\n\r\n"), [b"7"])[0]
    assert (head["address"], head["name"], head["to"]) == ("no-reply@hire.lever.co", "Hooli AI", ["me@example.com"])
    assert head["arrived"] is not None
    for sender in (b"From: no-reply@hire.lever.co <x@sender.example>",          # parsers read it two ways
                   b"From: no-reply@hire.lever.co, x@sender.example",          # two senders
                   b"From: Hooli AI <no-reply@hire.lever.co>\r\nFrom: X <x@sender.example>"):   # two From lines
        assert inbox._headers(FakeImap(sender + b"\r\n\r\n"), [b"7"])[0]["address"] == "", sender


def test_platforms_anyone_can_send_through_and_free_mailboxes_speak_for_no_employer(db):
    among = inbox.targets()
    closed = ("Your application", "Unfortunately the Senior DevOps Engineer position has been closed.")
    for sender in ("Globex <messages-noreply@linkedin.com>", "Globex Recruiting <notifications@calendly.com>",
                   "Globex Careers <globex@gmail.com>"):
        assert inbox.match(sender, *closed, among, NOW) is None, sender


def test_unproven_mail_never_moves_an_application(db, dns, monkeypatch):
    sent = NOW - timedelta(hours=2)
    mail = [head(b"9", "no-reply@applytojob.com", "Globex", "Your Application with Globex", sent)]
    unsigned = (b"From: Globex <no-reply@applytojob.com>\r\nTo: me@example.com\r\nSubject: Your Application with Globex"
                b"\r\n\r\nUnfortunately, the position has been closed.\r\n")
    fake_mailbox(monkeypatch, mail, {b"9": unsigned})
    assert inbox.check(days=2)["updates"] == [] and tracker.application(2)["stages"] == []


def test_someone_elses_signed_mail_sent_on_moves_nothing(db, dns, monkeypatch):
    sent = NOW - timedelta(hours=1)
    lever = "no-reply@hire.lever.co"
    # Signed by Lever, for someone else; sent on to you unchanged, signature and all.
    mail = [head(b"5", lever, "Hooli AI", "Your Hooli AI Application: Staff Platform Engineer", sent,
                 to="someone.else@example.org")]
    raw = signed(dns, f"Hooli AI <{lever}>", "someone.else@example.org", mail[0]["subject"],
                 "For the Staff Platform Engineer (R1002) role, the position has been closed.")
    fake_mailbox(monkeypatch, mail, {b"5": raw})
    assert inbox.check(days=2)["updates"] == []


def test_one_mailbox_however_written():
    assert inbox.mailbox_key("jordan.example+jobs@gmail.com") == inbox.mailbox_key("JordanExample@googlemail.com")
    assert inbox.mailbox_key("jordan.example@globex.com") != inbox.mailbox_key("jordanexample@globex.com")
