"""Replies about your applications, read from your mailbox: the Applied page kept up to date.

Every half hour while the app runs, and whenever "Check email now" is pressed, the mailbox the app
already reads (read-only, over IMAP, with the app password in .env) is searched for recent mail
about applications. Each message is tied to a job only when it names a company you have applied
to -- or one whose job you were applying to -- and then:

  * a rejection, an interview invitation, an assessment or an offer becomes that application's
    next stage, noted with the email's subject, and its status follows (Interviewing, Offer,
    Rejected);
  * a "thank you for applying" for a job not yet marked Applied marks it Applied, dated by the
    email: the employer's own receipt is the proof the application went;
  * every change is kept as an update, shown as a pop-up on the Jobs page until it is dismissed.

Recruiters' cold mail ("Immediate interview // Hiring") and job alerts name no company you applied
to, or say nothing about an application, and are left alone. Nothing in the mailbox is marked,
moved or deleted; the mail's text is read and not kept, only its subject in the update.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from backend.config.loader import PROJECT_ROOT

log = logging.getLogger(__name__)

FOLDER = PROJECT_ROOT / "data" / "inbox"
STATE = FOLDER / "state.json"
UPDATES = FOLDER / "updates.json"
EVERY_S = 30 * 60                     # how often the app looks while it runs
FIRST_DAYS = 45                       # how far back the first look goes
KEEP_UPDATES = 300
APPLIED = ("Applied", "Interviewing", "Offer", "Rejected")
_lock = threading.Lock()
_busy = threading.Lock()

# -- what a message says ---------------------------------------------------------------------------

_NOISE = re.compile(r"latest jobs for you|jobs you may like|job alert|recommended jobs|new jobs (for|matching)|"
                    r"jobs matching|we found \d+ jobs|people are hiring|similar jobs|top jobs", re.I)
_CONFIRM = re.compile(r"thank(s| you) for (applying|your application|your interest in)|"
                      r"(received|receipt of) your application|application (has been |was )?(received|submitted)|"
                      r"we('ve| have) received your application|successfully (submitted|applied)", re.I)
_REJECT = re.compile(r"move forward with other (candidates|applicants)|decided (not to|to pursue other|to move forward "
                     r"with other)|pursue other (candidates|applicants)|not (be )?moving forward with your|"
                     r"will not be (proceeding|moving forward)|regret to inform|no longer (being )?considered|"
                     r"not been selected|were not selected|position has been filled|other candidates whose|"
                     r"unfortunately[^.]{0,140}\b(not|other candidates|another candidate|decided|unable to offer)\b",
                     re.I)
# The role itself closed or cancelled ("the requisition has been cancelled"), not the person turned
# down. "Will be closed once filled", in a receipt, says what may happen and is not this; "your
# application for the role has been withdrawn" is the person's own doing, so "withdrawn" is left out.
_CLOSED = re.compile(r"\b(position|role|job|requisition|opening|posting|vacancy)\b[^.]{0,60}?\b(has|have) been "
                     r"(closed|cancell?ed|eliminated)\b|\b(position|role|job|requisition|opening|posting)\b"
                     r"[^.]{0,40}?\bis no longer (available|open)\b|no longer accepting applications|"
                     r"decided not to fill (the|this) (position|role|opening)|"
                     r"\b(closed|cancell?ed) (the|this) (position|role|requisition|opening)\b", re.I)
_OFFER = re.compile(r"pleased to (offer|extend)|extend (you )?an offer|offer letter|job offer|"
                    r"offer of employment", re.I)
_ASSESS = re.compile(r"\bassessment\b|coding (challenge|exercise|test)|take[- ]home|hackerrank|codesignal|codility|"
                     r"online test|technical exercise", re.I)
_INVITE = re.compile(r"(invite you|would like to|like to schedule|schedule (a|an|your|some time)|your availability|"
                     r"book a time|pick a time|select a time|set up a time)[^.]{0,90}"
                     r"\b(interview|call|chat|conversation|screen|meeting)\b|calendly\.com|goodtime\.io|"
                     r"interview (invitation|request)|invitation to interview", re.I)
_FINAL = re.compile(r"final (round|interview|stage)|on-?site interview|onsite", re.I)
_TECH = re.compile(r"technical (interview|screen|round)|system design|coding interview|pair programming", re.I)

# -- who a message is from -------------------------------------------------------------------------
#
# Anyone can put "Acme" in a sender's name, and "x@acme.attacker.com" begins with "acme". So mail
# moves an application only when Gmail found it really came from its domain (DKIM or DMARC passed,
# in the Authentication-Results header it adds), and that domain is the employer's own or a hiring
# system that sends employers' mail (whose messages name the employer in the sender or the text).
HIRING_DOMAINS = frozenset({
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "ashbyhq.com", "myworkday.com", "myworkdayjobs.com",
    "workday.com", "applytojob.com", "jazzhr.com", "icims.com", "smartrecruiters.com", "workablemail.com",
    "workable.com", "bamboohr.com", "breezy.hr", "recruitee.com", "jobvite.com", "taleo.net",
    "successfactors.com", "oraclecloud.com", "ultipro.com", "ukg.com", "paylocity.com", "rippling.com",
    "gem.com", "dover.com", "teamtailor.com", "pinpointhq.com", "paycomonline.net", "adp.com",
    "calendly.com", "goodtime.io", "linkedin.com", "indeed.com", "indeedemail.com"})
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}


def registrable(domain: str) -> str:
    """The domain a company owns: "us.greenhouse-mail.io" -> "greenhouse-mail.io", "acme.attacker.com"
    -> "attacker.com", "mail.acme.co.uk" -> "acme.co.uk"."""
    parts = [p for p in (domain or "").lower().strip(".").split(".") if p]
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def verified(sender: str, auth: str) -> str | None:
    """The sender's domain (registrable) when the message provably came from it, else None."""
    _, address = parseaddr(sender or "")
    org = registrable(address.rpartition("@")[2])
    if not org:
        return None
    said = (auth or "").lower()
    for result, domain in re.findall(r"\b(dmarc|dkim)=pass\b[^;]*?\b(?:header\.from|header\.d|header\.i)=@?([a-z0-9.-]+)",
                                     said):
        if registrable(domain) == org:
            return org
    return None


def _own_domain(org: str, names) -> bool:
    """Is this the employer's own domain: "acme.com" for Acme."""
    first = org.split(".")[0]
    return bool(first) and any(n.replace(" ", "") == first for n in names)


KIND_STAGE = {"rejection": "rejected", "closed": "closed", "offer": "offer", "assessment": "technical",
              "interview": "recruiter_screen", "technical": "technical", "final": "final"}
KIND_LABEL = {"confirmation": "Application received", "rejection": "Not moving forward",
              "closed": "Position closed", "offer": "Offer",
              "assessment": "Assessment sent", "interview": "Interview invitation",
              "technical": "Technical interview", "final": "Final interview"}


def classify(subject: str, body: str) -> str | None:
    """What a message is about an application, or None when it is not about one."""
    subject, text = subject or "", f"{subject}\n{body or ''}"
    if _NOISE.search(subject):
        return None
    if _CLOSED.search(text):
        return "closed"                    # the reason, where a closed role also says "not moving forward"
    if _REJECT.search(text):
        return "rejection"                 # before "thank you for applying", which rejections open with
    if _OFFER.search(text):
        return "offer"
    if _CONFIRM.search(subject):
        # "Thank you for applying to Acme" -- its text says what may come ("should we find a match,
        # we'll reach out for an initial screen"), which is no invitation.
        return "confirmation"
    if _ASSESS.search(subject) or (_ASSESS.search(text) and re.search(r"complete|invite|link|due", text, re.I)):
        return "assessment"
    if _INVITE.search(text) or re.search(r"\binterview\b", subject, re.I):
        return "final" if _FINAL.search(text) else "technical" if _TECH.search(text) else "interview"
    if _CONFIRM.search(text):
        return "confirmation"
    return None


# -- which application it is about ------------------------------------------------------------------

def _norm(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip() + " "


@dataclass
class Target:
    job_id: int
    company: str
    title: str
    status: str
    names: tuple[str, ...]            # the company as mail may write it, normalised
    engaged: bool                     # applied, or an Auto-apply run for it
    applied: datetime | None
    since: datetime | None = None     # when the app first had the job: mail from before is about another


_COMMON = {"the", "inc", "llc", "group", "global", "services", "solutions", "systems", "technologies", "technology",
           "company", "corporation", "international", "consulting", "labs", "health", "care", "partners", "digital",
           "data", "software", "cloud", "general", "american", "national", "first", "united", "network"}


def _names(company: str) -> tuple[str, ...]:
    from data_engineering.companies.careers_detect import name_key, normalize_name
    out = {n.strip() for n in (normalize_name(company), name_key(company)) if n and len(n.strip()) >= 3}
    first = (name_key(company) or "").split(" ")[0]
    if len(first) >= 4 and first not in _COMMON:
        out.add(first)                # "Globex" for "Globex Communications"
    return tuple(sorted(out, key=len, reverse=True))


def targets() -> list[Target]:
    """Jobs a message may be about: every application, and every job Auto-apply was run for."""
    from backend.apply.greenhouse import RUNS_DIR
    from data_engineering.db.models import Job
    from data_engineering.db.session import get_session
    from sqlalchemy import or_
    ran = {int(p.name.split("-")[0]) for p in RUNS_DIR.glob("*-*") if p.name.split("-")[0].isdigit()}
    out = []
    with get_session() as session:
        rows = session.query(Job).filter(or_(Job.status.in_(APPLIED), Job.applied_at.isnot(None),
                                             Job.id.in_(sorted(ran) or [-1]))).all()
        for job in rows:
            applied = job.applied_at.replace(tzinfo=timezone.utc) if job.applied_at and not job.applied_at.tzinfo \
                else job.applied_at
            found = job.found_at.replace(tzinfo=timezone.utc) if job.found_at and not job.found_at.tzinfo \
                else job.found_at
            out.append(Target(job.id, job.company or "", job.title or "", job.status, _names(job.company or ""),
                              True, applied, found))
    return out


def match(sender: str, subject: str, body: str, among: list[Target], sent: datetime | None = None) -> Target | None:
    """The application a message is about: its company named where an employer's mail names it.

    A message sent before the app had the job is about some other application at that company
    (a receipt of 3 October, for a role the app found on the 15th)."""
    from datetime import timedelta
    if sent is not None:
        among = [t for t in among if t.since is None or sent >= t.since - timedelta(days=1)]
    display, address = parseaddr(sender or "")
    local, _, domain = address.lower().partition("@")
    org = registrable(domain)
    hiring = org in HIRING_DOMAINS
    head, subj, text = _norm(display), _norm(subject), _norm((body or "")[:6000])
    scored = []
    for t in among:
        own = _own_domain(org, t.names)
        if not (hiring or own):
            continue                   # from elsewhere, only the employer's own domain speaks for it
        score = 3 if own else 0
        for n in t.names:
            word = f" {n} "
            if word in head or (hiring and n.replace(" ", "") == local):
                score = max(score, 3)
            elif word in subj and len(n) >= 4:
                score = max(score, 2 if n in t.names[:2] else 1)
            elif word in text and len(n) >= 5 and n in t.names[:2]:
                score = max(score, 1)
        if not score:
            continue
        title_words = [w for w in re.findall(r"[a-z0-9]{3,}", t.title.lower())
                       if w not in {"senior", "engineer", "the", "and", "for", "with", "staff", "lead"}]
        said = sum(1 for w in set(title_words) if f" {w} " in subj or f" {w} " in text)
        req = re.search(r"\b[rj]r?-?\d{3,}\b", t.title.lower())
        if req and req.group(0) in (subject + " " + (body or "")).lower():
            said += 3
        scored.append((score + min(said, 3), score, t))
    if not scored:
        return None
    scored.sort(key=lambda s: (s[0], s[2].applied or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    best_total, best_company, best = scored[0]
    if best_company < 2 and best_total < 4:
        return None                    # named only in passing: not enough to act on
    return best


def lookup_job(sender: str, subject: str, body: str) -> Target | None:
    """A job applied to outside the app, from the employer's receipt: the company named by the
    sender or the subject, and the job's title in the message. One clear answer, or None."""
    from data_engineering.db.models import Job
    from data_engineering.db.session import get_session
    display, address = parseaddr(sender or "")
    org = registrable(address.rpartition("@")[2])
    where = _norm(display) + _norm(subject)
    text = _norm(subject + " " + (body or "")[:8000])
    hits = []
    with get_session() as session:
        for company, in session.query(Job.company).distinct():
            if not company:
                continue
            names = _names(company)
            if org not in HIRING_DOMAINS and not _own_domain(org, names):
                continue               # a sender's name is anyone's to write
            if not any(f" {n} " in where for n in names[:2] if len(n) >= 4):
                continue
            for job in session.query(Job).filter(Job.company == company):
                title = _norm(job.title)
                if len(title.strip()) >= 6 and title in text:
                    hits.append(Target(job.id, job.company, job.title, job.status, names, False, None))
    if len({h.title.lower() for h in hits}) != 1:
        return None                    # none, or several different jobs: not sure enough to act
    return hits[0]


# -- the mailbox --------------------------------------------------------------------------------------

def credentials() -> tuple[str, str] | None:
    address = os.getenv("MAIL_ADDRESS", "").strip()
    password = os.getenv("MAIL_APP_PASSWORD", "").replace(" ", "").strip()
    return (address, password) if address and password else None


def _connect(address: str, password: str, host: str = "imap.gmail.com"):
    connection = imaplib.IMAP4_SSL(host, timeout=60)
    connection.login(address, password)
    for folder in ('"[Gmail]/All Mail"', "INBOX"):
        typ, _ = connection.select(folder, readonly=True)
        if typ == "OK":
            return connection
    raise RuntimeError("No mailbox folder could be opened.")


_QUERY = ('newer_than:{days}d -category:promotions -category:social (subject:application OR subject:applying '
          'OR subject:interview OR subject:candidacy OR subject:offer OR subject:assessment OR "thank you for applying" '
          'OR "your application" OR "next steps" OR "move forward" OR "other candidates")')


def _search(connection, days: int) -> list[bytes]:
    connection.literal = _QUERY.format(days=days).encode()
    typ, data = connection.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
    return data[0].split() if typ == "OK" and data and data[0] else []


def _headers(connection, uids: list[bytes]) -> list[dict]:
    """Sender, subject, date and id of many messages in one request."""
    out = []
    for start in range(0, len(uids), 100):
        batch = b",".join(uids[start:start + 100]).decode()
        typ, data = connection.uid("FETCH", batch,
                                   "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID AUTHENTICATION-RESULTS)])")
        for part in data or []:
            if not isinstance(part, tuple):
                continue
            uid = re.search(rb"UID (\d+)", part[0])
            msg = email.message_from_bytes(part[1], policy=email.policy.default)
            out.append({"uid": uid.group(1) if uid else None, "from": _text(msg["From"]),
                        "subject": _text(msg["Subject"]), "date": _when(msg["Date"]),
                        "auth": " ; ".join(str(a) for a in (msg.get_all("Authentication-Results") or [])),
                        "id": _text(msg["Message-ID"]) or f"uid:{uid.group(1).decode() if uid else ''}"})
    return out


class Box:
    """The mailbox, opened again when Gmail drops the connection (it did, mid-message, with an SSL
    error -- and the next messages read as empty)."""

    def __init__(self, creds: tuple[str, str]):
        self.creds = creds
        self.conn = _connect(*creds)

    def reopen(self) -> None:
        try:
            self.conn.logout()
        except Exception:
            pass
        self.conn = _connect(*self.creds)

    def body(self, uid: bytes) -> str:
        from data_engineering.scraper.mailbox import _body_text
        for attempt in range(3):
            try:
                typ, data = self.conn.uid("FETCH", uid, "(BODY.PEEK[])")
                raw = next((p[1] for p in data or [] if isinstance(p, tuple)), b"")
                return _body_text(email.message_from_bytes(raw, policy=email.policy.default))[:20000]
            except (TimeoutError, OSError, imaplib.IMAP4.abort, imaplib.IMAP4.error) as exc:
                log.debug("Reading a message failed (%s); opening the mailbox again", exc)
                time.sleep(1 + attempt)
                try:
                    self.reopen()
                except Exception:
                    pass
        return ""

    def close(self) -> None:
        try:
            self.conn.logout()
        except Exception:
            pass


def _text(value) -> str:
    from data_engineering.scraper.mailbox import _decode
    return " ".join(_decode(value).split())


def _when(value) -> datetime | None:
    try:
        moment = parsedate_to_datetime(str(value))
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# -- state and updates ------------------------------------------------------------------------------

def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def state() -> dict:
    return _read(STATE, {})


def updates(unseen_only: bool = False) -> list[dict]:
    items = _read(UPDATES, [])
    return [u for u in items if not u.get("seen")] if unseen_only else items


def mark_seen(ids: list[str] | None = None) -> int:
    """The pop-up dismissed: these updates (or all) are not shown again."""
    with _lock:
        items, n = _read(UPDATES, []), 0
        for u in items:
            if not u.get("seen") and (ids is None or u["id"] in ids):
                u["seen"] = True
                n += 1
        _write(UPDATES, items)
    return n


# -- acting on a message ------------------------------------------------------------------------------

def act(target: Target, kind: str, message: dict) -> dict | None:
    """Move the application on, once; returns the update to show, or None when nothing changed."""
    from backend.apply import anyform, tracker
    from backend.apply.runner import record_application
    from data_engineering.db.models import Job
    from data_engineering.db.session import get_session
    when: datetime = message.get("date") or datetime.now(timezone.utc)
    note = f"Email: {message.get('subject', '')[:200]}"
    change = None
    if kind == "confirmation":
        if target.status in APPLIED:
            return None                # already recorded: the receipt only confirms it
        pending = anyform.unconfirmed(target.job_id) or {}
        with get_session() as session:
            job = session.get(Job, target.job_id)
            url = job.application_url if job else ""
        # With an Auto-apply run waiting on "Did it go?", it is that run's application; without one it
        # went outside the app, and the email is all there is to say about it.
        entry = {**{k: v for k, v in pending.items() if k != "kept_at"}, "url": pending.get("url") or url,
                 "how": pending.get("how") or ("you" if pending else "email"),
                 "confirmed_by_email": message.get("subject", "")[:200]}
        record_application(target.job_id, entry, at=when)
        anyform.forget_unconfirmed(target.job_id)
        change = "Marked Applied"
    else:
        stage = KIND_STAGE[kind]
        current = tracker.application(target.job_id) or {}
        if any(e.get("stage") == stage for e in current.get("stages") or []):
            return None                # this news is on the page already
        day = min(when.astimezone().date(), datetime.now().date()).isoformat()
        try:
            tracker.add_stage(target.job_id, stage, day, note)
        except ValueError:
            # Dated before the application was recorded (applied by hand, marked later): today instead.
            tracker.add_stage(target.job_id, stage, None, note)
        change = tracker.STAGES[stage][0]
    return {"id": message["id"], "job_id": target.job_id, "company": target.company, "title": target.title,
            "kind": kind, "label": KIND_LABEL.get(kind, kind), "change": change,
            "subject": message.get("subject", "")[:200], "from": message.get("from", "")[:120],
            "date": when.isoformat(), "seen": False}


def _receipt(head: dict) -> dict:
    display, address = parseaddr(head.get("from") or "")
    return {"id": head["id"], "job_id": None, "company": display or address, "title": "",
            "kind": "confirmation", "label": "Application received", "change": "Not in your job list",
            "subject": head.get("subject", "")[:200], "from": head.get("from", "")[:120],
            "date": (head.get("date") or datetime.now(timezone.utc)).isoformat(), "seen": False}


def check(cfg=None, days: int | None = None, say=lambda *_: None, dry_run: bool = False,
          again: bool = False) -> dict:
    """Look through recent mail once. Returns {checked, matched, updates, error}.

    `dry_run` reads the mail and says what would change, and changes nothing: no stage, no status,
    no update, no record of what was read. `again` reads mail already read once more: harmless,
    since nothing is recorded twice, and how a reply missed the first time is caught."""
    if not _busy.acquire(blocking=False):
        return {"checked": 0, "matched": 0, "updates": [], "error": "A check is already running."}
    try:
        return _check(days, say, dry_run, again)
    finally:
        _busy.release()


def _preview(target: Target, kind: str, message: dict) -> dict:
    """What act() would do, said without doing it -- the same skips included."""
    from backend.apply import tracker
    if kind == "confirmation":
        change = "would mark Applied" if target.status not in APPLIED else "already Applied: nothing to do"
    else:
        held = {e.get("stage") for e in (tracker.application(target.job_id) or {}).get("stages") or []}
        stage = KIND_STAGE.get(kind)
        change = f"already recorded ({stage}): nothing to do" if stage in held else f"would add stage: {stage}"
    return {"id": message["id"], "job_id": target.job_id, "company": target.company, "title": target.title,
            "kind": kind, "label": KIND_LABEL.get(kind, kind), "change": change,
            "subject": message.get("subject", "")[:200], "from": message.get("from", "")[:120],
            "date": (message.get("date") or datetime.now(timezone.utc)).isoformat(), "seen": False}


def _check(days: int | None, say, dry_run: bool = False, again: bool = False) -> dict:
    creds = credentials()
    if creds is None:
        return {"checked": 0, "matched": 0, "updates": [],
                "error": "No mailbox set up: add MAIL_ADDRESS and MAIL_APP_PASSWORD to .env (a Google app password)."}
    st = state()
    if days is None:
        last = _when(st.get("last_check")) if st.get("last_check") else None
        days = FIRST_DAYS if last is None else max(2, (datetime.now(timezone.utc) - last).days + 2)
    done: set[str] = set(st.get("done") or [])
    among = targets()
    found: list[dict] = []
    checked = matched = 0
    try:
        box = Box(creds)
    except Exception as exc:
        return {"checked": 0, "matched": 0, "updates": [], "error": f"Could not open the mailbox: {exc}"}
    try:
        heads = [h for h in _headers(box.conn, _search(box.conn, days)) if again or h["id"] not in done]
        say(f"{len(heads)} new message(s) to look at")
        for head in sorted(heads, key=lambda h: h["date"] or datetime.min.replace(tzinfo=timezone.utc)):
            checked += 1
            done.add(head["id"])
            if _NOISE.search(head["subject"]):
                continue
            org = verified(head["from"], head.get("auth", ""))
            if org is None:
                continue                   # not provably from where it says: never moves an application
            target = match(head["from"], head["subject"], "", among, head["date"])
            body = ""
            if target is None:
                # The company may be named only in the text (a hiring system's own sender name).
                if org not in HIRING_DOMAINS:
                    continue
                body = box.body(head["uid"])
                target = match(head["from"], head["subject"], body, among, head["date"])
                if target is None and classify(head["subject"], body) == "confirmation":
                    target = lookup_job(head["from"], head["subject"], body)
                    if target is None:          # a receipt for a job not on the list: still worth seeing
                        found.append(_receipt(head))
                        continue
                if target is None:
                    continue
            matched += 1
            body = body or box.body(head["uid"])
            kind = classify(head["subject"], body)
            if kind is None:
                continue
            update = _preview(target, kind, head) if dry_run else act(target, kind, head)
            if update is not None:
                found.append(update)
                target.status = "Applied" if kind == "confirmation" else target.status
            if kind == "confirmation" and target not in among:
                # An application found by its receipt (sent outside the app) is one from now on: the
                # rejection that came three days later, in the same look, is about it.
                target.engaged, target.since = True, None
                among.append(target)
    finally:
        box.close()
    if dry_run:
        return {"checked": checked, "matched": matched, "updates": found, "error": None}
    with _lock:
        if found:
            items = found[::-1] + _read(UPDATES, [])
            _write(UPDATES, items[:KEEP_UPDATES])
        _write(STATE, {"last_check": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "done": sorted(done)[-5000:], "last_result": {"checked": checked, "matched": matched,
                                                                     "updates": len(found)}})
    if found:
        _notify(found)
    return {"checked": checked, "matched": matched, "updates": found, "error": None}


def _notify(found: list[dict]) -> None:
    """A Mac notification as well as the pop-up, for news that should not wait for the page."""
    import subprocess
    first = found[0]
    title = f"{first['company']}: {first['label']}" + (f" (+{len(found) - 1} more)" if len(found) > 1 else "")
    text = first["title"][:90]
    script = f'display notification {json.dumps(text)} with title "Job Applier" subtitle {json.dumps(title)}'
    try:
        subprocess.run(["osascript", "-e", script], timeout=10, capture_output=True)
    except Exception:
        pass


# -- the half-hourly look --------------------------------------------------------------------------------

def watch(load_cfg, every_s: int = EVERY_S, first_after_s: int = 90) -> threading.Thread:
    """Look at the mailbox now and then while the app runs, on a thread of its own."""
    def loop():
        time.sleep(first_after_s)
        while True:
            try:
                load_cfg()                          # .env into the environment; the database
                result = check()
                if result.get("updates"):
                    log.info("Email: %d application update(s)", len(result["updates"]))
                elif result.get("error"):
                    log.info("Email check: %s", result["error"])
            except Exception as exc:                # a bad night on the network never stops the app
                log.warning("Email check failed: %s", exc)
            time.sleep(every_s)
    thread = threading.Thread(target=loop, name="inbox-watch", daemon=True)
    thread.start()
    return thread
