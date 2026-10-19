"""Work through the apply queue: fill each form, submit or hand it over, record it.

One visible browser for the whole run, one tab per application. For each job:

  1. Pick the resume. The tailored one if the guard passed it; otherwise tailor
     now; if that is refused or no model is running, the base resume — which is
     the person's own words, so it is always safe to send.
  2. Open the Greenhouse form itself (`greenhouse.form_url`), not the posting's
     link, and fill everything the profile can answer.
  3. If nothing needs a person — no CAPTCHA, no unanswered declaration, no
     blank required question — and the profile allows it, submit. Otherwise
     wait for the person: they finish the form and press Submit, or close the
     tab to skip it.
  4. Only a confirmation page counts as applied. Then the job is marked
     Applied, and a line goes into data/applications.jsonl with what was sent.

Printed lines are the interface: the web page shows the tail of this run's
output, so every line is written for the person watching.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.apply import greenhouse
from backend.apply.profile import Applicant, ProfileIncomplete, check_ready, load
from backend.apply.queue import build_queue, summarise
from backend.config.loader import PROJECT_ROOT
from data_engineering.db.models import Job
from data_engineering.db.session import get_session

log = logging.getLogger(__name__)

AUDIT_LOG = PROJECT_ROOT / "data" / "applications.jsonl"
DEFAULT_WAIT_MINUTES = 15


class NotReady(RuntimeError):
    """Nothing can be applied to until the person fixes this."""


@dataclass
class Outcome:
    job_id: int
    company: str
    title: str
    result: str          # submitted | skipped | timeout | not-filled | stopped | handoff
    how: str = ""        # auto | you
    detail: str = ""
    handoff: str = ""    # the form to open in the person's own browser, once the app's is closed


# Boards whose CAPTCHA turns the app's own browser away at Submit even when the person presses it
# (Lever's hCaptcha: "There was an error verifying your application"). The app fills the form to
# learn what it asks, then hands it over: the person sends it from their own browser.
HANDOFF = (re.compile(r"^https://jobs\.(?:eu\.)?lever\.co/", re.I),)


def hands_off(url: str) -> bool:
    return any(p.match(url or "") for p in HANDOFF)


def open_own_browser(url: str) -> bool:
    """The person's own Chrome -- their profile, their sign-ins -- not the app's; else their browser."""
    for args in (["open", "-a", "Google Chrome", url], ["open", url]):
        try:
            if subprocess.run(args, capture_output=True, timeout=20).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def say(line: str) -> None:
    print(line, flush=True)


# -- limits ---------------------------------------------------------------

def start_of_today_utc(now: datetime | None = None) -> datetime:
    """Local midnight, in UTC. "Ten a day" means the person's day."""
    local = (now or datetime.now(timezone.utc)).astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def remaining_today(cfg, applicant: Applicant, applied_today: int) -> int:
    """Both caps apply: the project's and the person's. The lower one wins."""
    project_cap = int(cfg.get("limits.max_applications_per_day", 15) or 0)
    return max(0, min(project_cap, applicant.max_per_day) - applied_today)


def _applied_since(session, since: datetime) -> int:
    return (session.query(Job)
            .filter(Job.applied_at.isnot(None), Job.applied_at >= since.replace(tzinfo=None))
            .count())


def _last_applied_by_company(session) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    for company, when in (session.query(Job.company, Job.applied_at)
                          .filter(Job.applied_at.isnot(None)).all()):
        if company not in latest or when > latest[company]:
            latest[company] = when
    return latest


# -- documents ------------------------------------------------------------

def tailored_resume(cfg, job) -> Path | None:
    """The guard-approved tailored resume for this job, PDF first, if any.

    One longer than the page limit was made before the limit existed and is
    skipped: attaching a nine-page resume is worse than tailoring again.
    """
    from ml.resume.packet import packet_root
    from ml.resume.pipeline import over_page_limit
    from ml.resume.writer import output_filename

    docx = packet_root(cfg) / output_filename(job.company, job.title, job.external_id or "")
    if over_page_limit(cfg, docx):
        return None
    for candidate in (docx.with_suffix(".pdf"), docx):
        if candidate.exists():
            return candidate
    return None


def base_resume() -> Path | None:
    from ml.resume.parser import find_base_resume
    return find_base_resume(PROJECT_ROOT / "ml" / "resume")


def selected_master(cfg, job) -> Path | None:
    """The master resume cut down to this posting, with no model involved.

    Nothing is reworded — it is the person's own lines, the ones this job
    description most needs, to the page limit. The fallback whenever
    tailoring is refused or unavailable, instead of the whole master resume.
    """
    from ml.resume.fit import fit_to_pages
    from ml.resume.packet import packet_root
    from ml.resume.pdf import write_pdf
    from ml.resume.pipeline import load_base_resume
    from ml.resume.tailor import TailorResult
    from ml.resume.writer import output_filename

    try:
        base = load_base_resume(cfg)
    except Exception:
        return base_resume()
    result = TailorResult(summary=None)
    limit = int(cfg.get("resume.max_pages", 4) or 0)
    if limit > 0:
        fit_to_pages(base, result, job.description or job.title,
                     requirements=job.requirements, company=job.company, max_pages=limit)
    stem = output_filename(job.company, job.title, job.external_id or "")[:-5]
    target = packet_root(cfg) / f"{stem}_selected.pdf"
    write_pdf(base, result, target)
    return target


def choose_resume(cfg, job, *, tailor: bool = True) -> tuple[Path | None, str]:
    """(file to attach, where it came from).

    A tailored resume set to be made again (one made from a master since repaired) is never
    sent as it is: it is tailored again first, or the person's own lines go instead."""
    from ml.tailoring.pipeline import needs_redo
    redo = needs_redo(job.id)
    found = None if redo else tailored_resume(cfg, job)
    if found:
        return found, "tailored"
    note = "tailoring off"
    if tailor:
        try:
            if redo:
                say(f"Tailoring the resume again first: the one made for this job was {redo} "
                    "(about 8 minutes)…")
                from backend.apply.prepare import _tailor
                _tailor(cfg, job, again=True)
                status = "did not replace the old one"
            else:
                from ml.resume.pipeline import tailor_jobs
                outcomes = tailor_jobs(cfg, [job.id])
                status = outcomes[0].status if outcomes else "not tailored"
            found = None if needs_redo(job.id) else tailored_resume(cfg, job)
            if found:
                return found, "tailored just now"
            note = f"tailoring {status}"
        except Exception as exc:              # no model running, no key, ...
            note = f"tailoring unavailable ({type(exc).__name__})"
    return selected_master(cfg, job), f"your resume selected for this job — {note}"


def file_name(applicant, company: str, kind: str, suffix: str) -> str:
    """The name an employer sees: "Jordan_Example_Resume_Acme.pdf" -- the person, what it is, for whom.

    The company keeps its own capitals and drops "Inc."/"LLC"; anything that is not a letter or a
    digit becomes one underscore, so the name survives every upload form.
    """
    from ml.resume.packet import file_name as named   # one rule for the job's folder and the upload
    return named(getattr(applicant, "full_name", ""), company, kind, suffix)


def sent_copy(path: Path, evidence: Path, applicant, company: str, kind: str) -> Path:
    """A copy of `path` named for the employer, in the run's send/ folder."""
    folder = evidence / "send"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / file_name(applicant, company, kind, Path(path).suffix)
    shutil.copy2(path, target)
    return target


def write_letter_if_missing(cfg, job, head: str = "") -> bool:
    """Auto-apply does every step of the job page's checklist: a cover letter is written for a job
    that has none -- the same writer and honesty check as the page's "let Claude write it" -- so
    a form that asks for one gets one. Returns whether the job has a letter now.

    A letter the check rejects, or a model that does not answer, leaves the application to go on
    without one: the job keeps its status (the page's writer would mark it Manual Review).
    """
    from ml.resume.packet import LETTER_FILE, packet_dir
    folder = packet_dir(cfg, job.company, job.title, job.external_id or "", job_id=job.id)
    if (folder / LETTER_FILE).exists():
        return True
    if cfg is not None and not cfg.get("apply.write_letter", True):
        return False
    from ml.resume.pipeline import write_letter
    say(f"{head}: writing the cover letter (a minute)…" if head else "Writing the cover letter…")
    with get_session() as session:
        status_before = session.get(Job, job.id).status
    try:
        outcome = write_letter(cfg, job.id)
        written = outcome.status not in ("rejected", "error") and (folder / LETTER_FILE).exists()
    except Exception as exc:                      # no letter is better than no application
        log.warning("Cover letter for job %s not written: %s", job.id, exc)
        written = False
    if not written:
        with get_session() as session:
            row = session.get(Job, job.id)
            if row is not None and row.status != status_before:
                row.status = status_before           # a failed letter is not a verdict on the job
        say("   No cover letter this time: the draft did not pass the honesty check, or no model answered. "
            "The application goes on without one; the job page can write one.")
    else:
        say("   Cover letter written and checked.")
    return written


def cover_letter(cfg, job, evidence: Path) -> Path | None:
    """The letter as it goes out: the formatted PDF when the person saved one, else the text.

    The PDF is written when the letter is saved on the job page (ml/resume/letter_edit.py),
    with the name and contact line on top. It is used only when it is newer than the
    text, so an edit made outside the page is never sent stale.
    """
    import shutil
    from ml.resume.letter_edit import PDF_FILE
    from ml.resume.packet import LETTER_FILE, packet_dir

    folder = packet_dir(cfg, job.company, job.title, job.external_id or "", job_id=job.id)
    letter = folder / LETTER_FILE
    if not letter.exists():
        return None
    pdf = folder / PDF_FILE
    if pdf.exists() and pdf.stat().st_mtime >= letter.stat().st_mtime:
        target = evidence / "cover-letter.pdf"
        shutil.copy2(pdf, target)
        return target
    target = evidence / "cover-letter.txt"
    target.write_text(letter.read_text(encoding="utf-8"), encoding="utf-8")
    return target


# -- recording ------------------------------------------------------------

def record_application(job_id: int, entry: dict, *, audit_log: Path | None = None,
                       at: datetime | None = None) -> None:
    """Mark the job Applied and keep a line saying exactly what went out.

    `at` is when it went, when that is known better than "now": an employer's
    "thank you for applying" email carries the moment of the submission."""
    audit_log = audit_log or AUDIT_LOG          # read at call time, not import
    now = at or datetime.now(timezone.utc)
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is not None:
            job.status = "Applied"
            job.applied_at = now
    audit_log.parent.mkdir(parents=True, exist_ok=True)
    with audit_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": now.isoformat(), "job_id": job_id, **entry}) + "\n")


# -- the run --------------------------------------------------------------

def _eligible(job, applicant: Applicant, quiet: bool = False) -> bool:
    """Checked again at apply time: a job prepared before the profile said otherwise
    (a Top Secret role, prepared before "no clearance" was answered) must not go out."""
    from backend.core.eligibility import eligible
    ok, why = eligible(job, applicant.authorisation or {})
    if not ok and not quiet:
        say(f"Not applying to {job.company} — {job.title}: {', '.join(why)} (from your profile).")
    return ok


def candidates(cfg, applicant: Applicant, limit: int, job_id: int | None = None,
               job_ids: list[int] | None = None):
    """What this run will apply to, and how many today's cap still allows."""
    with get_session() as session:
        left = remaining_today(cfg, applicant, _applied_since(session, start_of_today_utc()))
        if job_ids:
            # The prepared ones, in the order they were chosen this morning.
            jobs = [j for j in (session.get(Job, i) for i in job_ids)
                    if j is not None and j.is_open and _eligible(j, applicant)]
            queue = build_queue(jobs, limit=min(len(jobs), left), max_per_company=len(jobs))
            order = {i: n for n, i in enumerate(job_ids)}
            queue.sort(key=lambda c: order.get(c.job_id, 0))
        elif job_id is not None:
            job = session.get(Job, job_id)
            jobs = [job] if job is not None and _eligible(job, applicant) else []
            # Asked for by name, from its own page: any board will do. A form the app has no
            # filler of its own for is followed and filled by backend/apply/anyform.py.
            queue = build_queue(jobs, limit=1, sources=(jobs[0].source,) if jobs else ())
        else:
            min_score = cfg.get("apply.min_score", cfg.get("resume.min_score", 0.45))
            queue = build_queue(
                [j for j in session.query(Job).filter(Job.is_open.is_(True)).all()
                 if _eligible(j, applicant, quiet=True)],
                limit=min(limit, left),
                min_score=float(min_score) if min_score is not None else None,
                last_applied=_last_applied_by_company(session),
            )
    return queue, left


def why_not(job_id: int) -> str:
    """Why one job asked for by name is not going out."""
    from backend.apply.queue import APPLIABLE_STATUSES
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            return "there is no job with that number."
        if not job.is_open:
            return "the posting has closed."
        if job.status not in APPLIABLE_STATUSES:
            return f"it is marked {job.status} already."
        if not (job.application_url or "").strip():
            return "the posting has no link to apply through."
    return "your profile rules it out (the line above says why)."


def run(cfg, *, limit: int = 10, job_id: int | None = None, tailor: bool = True,
        wait_minutes: float | None = None, browser_factory=None,
        prepared: bool = False) -> list[Outcome]:
    try:
        applicant = load(PROJECT_ROOT / "backend" / "config" / "applicant.yaml")
    except ProfileIncomplete as exc:
        raise NotReady(f"{exc}\nFill in your details at http://127.0.0.1:8770/applicant")
    problems = check_ready(applicant)
    if problems:
        raise NotReady("Your applicant details are incomplete:\n"
                       + "\n".join(f"  - {p}" for p in problems)
                       + "\nFix them at http://127.0.0.1:8770/applicant")

    ready = {}
    if prepared:
        from backend.apply.prepare import pending
        ready = {e["job_id"]: e for e in pending()}
        if not ready:
            say("Nothing prepared to apply to. The morning run prepares the day's best matches; "
                "or press Prepare now on the Apply page.")
            return []
    queue, left = candidates(cfg, applicant, limit, job_id, list(ready) or None)
    if left <= 0 and job_id is None:
        say("Today's application limit is reached — nothing more until tomorrow.")
        return []
    if not queue:
        if job_id is not None:
            say(f"Can't auto-apply to this one: {why_not(job_id)}")
            return []
        say("Nothing to apply to right now: no open Greenhouse or Workday posting above your "
            "score threshold that you haven't applied to or marked yourself.")
        return []

    auto = not applicant.stop_before_submit
    wait_s = 60 * float(wait_minutes if wait_minutes is not None
                        else cfg.get("apply.wait_minutes", DEFAULT_WAIT_MINUTES))
    say(f"Applying to {len(queue)} ({summarise(queue)}) · {left} left today · "
        f"{'auto-submit where no CAPTCHA' if auto else 'you press Submit on each'}")

    if browser_factory is None:
        from playwright.sync_api import sync_playwright
        manager = sync_playwright().start()
        from backend.apply.helper import launch
        browser, context = launch(manager, cfg, user_agent=greenhouse.USER_AGENT,
                                  viewport={"width": 1280, "height": 1400})
    else:
        manager, browser, context = None, None, browser_factory()

    outcomes: list[Outcome] = []
    try:
        for index, candidate in enumerate(queue, start=1):
            with get_session() as session:
                job = session.get(Job, candidate.job_id)
                session.expunge(job)
            head = f"[{index}/{len(queue)}] {job.company} — {job.title}"
            try:
                page = context.new_page()
            except Exception:
                say(f"{head}: the browser was closed — stopping.")
                outcomes.append(Outcome(job.id, job.company, job.title, "stopped"))
                break
            # This form's own answers, given on the Apply page, for this job only.
            saved = dict(applicant.answers)
            applicant.job = job
            entry = ready.get(job.id)
            if entry is None:
                from backend.apply.prepare import load as load_prepared
                entry = load_prepared().get(str(job.id))
            if entry is not None:
                from backend.apply.answers import normalise
                own = {normalise(k): v for k, v in (entry.get("answers") or {}).items()}
                applicant.answers.update(own)
                applicant.job_answers = own
            outcome = apply_one(cfg, page, job, applicant, head, auto=auto, tailor=tailor, wait_s=wait_s)
            applicant.answers.clear(); applicant.answers.update(saved)
            applicant.job, applicant.job_answers = None, {}
            outcomes.append(outcome)
            if job.id in ready and outcome.result in ("submitted", "skipped"):
                from backend.apply.prepare import mark
                mark(job.id, "applied" if outcome.result == "submitted" else "skipped")
            try:
                if not page.is_closed():
                    page.close()
            except Exception:
                pass
    finally:
        for closer in (context, browser):
            try:
                if closer is not None and browser_factory is None:
                    closer.close()
            except Exception:
                pass
        if manager is not None:
            manager.stop()

    # Only now, with the app's window gone: a URL sent to Chrome while it was open could land in it.
    handed = [o for o in outcomes if o.handoff]
    for o in handed:
        if not open_own_browser(o.handoff):
            say(f"   Open {o.handoff} in your own browser to send {o.company}'s form.")
    sent = [o for o in outcomes if o.result == "submitted"]
    say(f"Done: {len(sent)} submitted, "
        f"{sum(o.result == 'skipped' for o in outcomes)} skipped, "
        f"{sum(o.result in ('timeout', 'not-filled') for o in outcomes)} not sent"
        + (f", {len(handed)} to send from your own browser" if handed else "") + ".")
    return outcomes


def apply_one(cfg, page, job, applicant: Applicant, head: str, *,
              auto: bool, tailor: bool, wait_s: float) -> Outcome:
    outcome = Outcome(job.id, job.company, job.title, "not-filled")
    evidence = greenhouse._evidence_dir(job.id)

    resume, resume_note = choose_resume(cfg, job, tailor=tailor)
    if resume is None:
        say(f"{head}: no resume to attach — put yours in ml/resume/. Skipped.")
        outcome.detail = "no resume"
        return outcome
    shutil.copy2(resume, evidence / f"resume{resume.suffix}")    # exactly what went out, for the app's pages
    # What the employer receives is named for the person and the company, never "resume.pdf".
    kept = sent_copy(resume, evidence, applicant, job.company, "Resume")
    try:
        # The job page's checklist reads the folder: without it, a run that tailored
        # the resume still showed "0 of 4 done" and the letter step stayed locked.
        from ml.resume.packet import build_packet
        build_packet(cfg, job)
    except Exception as exc:                   # a folder problem never stops the application
        log.warning("No application folder for job %s: %s", job.id, exc)
    write_letter_if_missing(cfg, job, head)
    letter = cover_letter(cfg, job, evidence)
    if letter is not None:
        letter = sent_copy(letter, evidence, applicant, job.company, "Cover_Letter")

    if job.source == "workday":
        return _apply_workday(cfg, page, job, applicant, head, outcome, kept, letter,
                              resume_note, evidence, wait_s)
    if job.source == "jazzhr":
        return _apply_jazzhr(page, job, applicant, head, outcome, kept, letter, resume_note, evidence, wait_s)
    if job.source != "greenhouse":
        return _apply_anywhere(cfg, page, job, applicant, head, outcome, kept, letter, resume_note,
                               evidence, wait_s, auto=auto)
    return _apply_greenhouse(page, job, applicant, head, outcome, kept, letter, resume_note, evidence,
                             wait_s, greenhouse.form_url(job.company_slug, job.external_id), auto=auto)


def _apply_greenhouse(page, job, applicant, head, outcome, kept, letter, resume_note, evidence, wait_s,
                      url, *, auto: bool) -> Outcome:
    """Greenhouse: filled, and sent by itself only when nothing on it needs the person."""
    result = greenhouse.fill(page, job.id, url, applicant, kept, letter,
                             submit=auto, evidence=evidence)
    if not result.ok:
        say(f"{head}: {result.summary()}")
        outcome.detail = result.error or ""
        return outcome

    say(f"{head}: {result.summary()} · {resume_note}")
    answers: dict[str, str] = {}
    if result.submitted and greenhouse.await_submission(page, 30) == "submitted":
        how, verdict = "auto", "submitted"
    else:
        waiting = []
        if result.submitted and greenhouse.challenge_shown(page):
            waiting.append("the CAPTCHA puzzle that appeared")
        elif result.submitted:
            waiting.append("whatever the form flagged after Submit")
        if result.captcha:
            waiting.append("the CAPTCHA")
        if result.declarations:
            waiting.append(f"{len(result.declarations)} legal question(s)")
        if result.required_blank:
            waiting.append("required: " + ", ".join(result.required_blank[:4]))
        say(f"   Waiting for you — finish {'; '.join(waiting) or 'the form'}, "
            f"then press Submit — the \"Submit application\" button at the very bottom of the form, "
            f"outlined in orange. Close the tab to skip this one.")
        verdict, answers = greenhouse.watch_submission(page, wait_s)
        how = "you"

    _settle(outcome, job, verdict, how, url, kept, resume_note, letter, result, evidence, answers)
    return outcome


def _apply_workday(cfg, page, job, applicant, head, outcome, kept, letter, resume_note,
                   evidence, wait_s, url: str | None = None) -> Outcome:
    """Workday: sign-in and Submit are the person's; every step between is filled."""
    from backend.apply import workday

    url = url or job.application_url
    say(f"{head}: opening Workday · {resume_note}")
    # The sign-in wait comes out of the same budget, so allow for it.
    verdict, result = workday.apply(page, job.id, url, applicant, kept,
                                    evidence=evidence, wait_s=wait_s + 300, say=say)
    if verdict == "not-filled":
        say(f"{head}: {result.summary()}")
        outcome.detail = result.error or ""
        return outcome
    _settle(outcome, job, verdict, "you", url, kept, resume_note, letter,
            result, evidence, result.given)
    return outcome


def _letter_text(cfg, job) -> str | None:
    """The cover letter as plain text, for a form that takes it in a box rather than a file."""
    from ml.resume.packet import LETTER_FILE, packet_dir
    folder = packet_dir(cfg, job.company, job.title, job.external_id or "", job_id=job.id)
    try:
        return (folder / LETTER_FILE).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _apply_anywhere(cfg, page, job, applicant, head, outcome, kept, letter, resume_note, evidence,
                    wait_s, *, auto: bool) -> Outcome:
    """Any other board: follow the posting to the employer's own form, fill it, wait for the person."""
    from backend.apply import anyform

    say(f"{head}: opening the posting on {job.source} · {resume_note}")
    reached = anyform.reach(page, job.application_url, say=say, check_s=min(wait_s, 300))
    if reached.error:
        say(f"{head}: not filled: {reached.error}")
        outcome.detail = reached.error
        return outcome
    page = reached.page
    if reached.kind == "greenhouse" and anyform.greenhouse_form(reached.url):
        say("   It leads to a Greenhouse form.")
        return _apply_greenhouse(page, job, applicant, head, outcome, kept, letter, resume_note, evidence,
                                 wait_s, anyform.greenhouse_form(reached.url), auto=auto)
    if reached.kind == "jazzhr":
        say("   It leads to a JazzHR form.")
        return _apply_jazzhr(page, job, applicant, head, outcome, kept, letter, resume_note, evidence,
                             wait_s, url=reached.url)
    if reached.kind == "workday":
        say("   It leads to Workday.")
        return _apply_workday(cfg, page, job, applicant, head, outcome, kept, letter, resume_note,
                              evidence, wait_s, url=reached.url)

    letter_text = _letter_text(cfg, job)
    seen: set[str] = set()
    result = anyform.fill(page, job.id, reached.url, applicant, kept, letter,
                          letter_text=letter_text, evidence=evidence, seen=seen)
    waiting: list[str] = []
    if reached.kind == "signin":
        say("   This site wants you to sign in or make an account first. Do that in the window, "
            "and the app fills the form when it shows.")
    elif reached.kind == "unknown":
        say("   The application form isn't on this page. Open it in the window (its Apply button), "
            "and the app fills it when it shows.")
    elif hands_off(reached.url):
        return _hand_off(job, reached.url, result, head, outcome, kept, letter, resume_note, evidence)
    else:
        say(f"{head}: {result.summary()} · on {reached.url.split('?')[0][:90]}")
        if result.captcha:
            waiting.append("the CAPTCHA")
        if result.declarations:
            waiting.append("the box(es) to tick yourself: " + "; ".join(d[:50] for d in result.declarations[:3]))
        if result.required_blank:
            waiting.append("required: " + ", ".join(result.required_blank[:4]))
        anyform.show_submit(page, scroll=not (result.required_blank or result.declarations))
    say(f"   Waiting for you — check it{', finish ' + '; '.join(waiting) if waiting else ''}, then press "
        f"Submit (outlined in orange). Leave the tab open until this says Applied; close it to skip.")
    verdict, answers, page = anyform.watch(page, applicant, result, wait_s, resume_path=kept,
                                           letter_path=letter, letter_text=letter_text, seen=seen,
                                           evidence=evidence, say=say)
    if verdict == "submitted":
        anyform.forget_unconfirmed(job.id)
    elif result.filled or result.attachments:
        # The site may have said "sent" in words the app does not know: the job page asks.
        anyform.keep_unconfirmed(job.id, {
            "company": job.company, "title": job.title, "url": reached.url, "how": "you",
            "resume": str(kept), "resume_source": resume_note,
            "cover_letter": str(letter) if letter else None, "filled": result.filled,
            "evidence": str(evidence)})
    _settle(outcome, job, verdict, "you", reached.url, kept, resume_note, letter, result, evidence, answers)
    return outcome


def _hand_off(job, url, result, head, outcome, kept, letter, resume_note, evidence) -> Outcome:
    """A form whose CAPTCHA turns the app away: what it asks, answered, for the person's own browser.
    The job page lists each answer with a Copy button and the files to attach, and asks whether it
    went -- "Yes" records the application as the app would have."""
    from backend.apply import anyform
    anyform.keep_unconfirmed(job.id, {
        "company": job.company, "title": job.title, "url": url, "how": "you", "handoff": True,
        "resume": str(kept), "resume_source": resume_note, "cover_letter": str(letter) if letter else None,
        "filled": result.filled, "left": list(result.required_blank) + list(result.declarations),
        "evidence": str(evidence)})
    say(f"{head}: {result.summary()}")
    say("   This site's CAPTCHA turns away the app's own browser even when you press Submit, so this one "
        "goes from yours: the form opens in your own Chrome when this window closes. The job page lists "
        "every answer with a Copy button and the files to attach; say there once it's sent.")
    outcome.result, outcome.how, outcome.handoff = "handoff", "you", url
    return outcome


def _apply_jazzhr(page, job, applicant, head, outcome, kept, letter, resume_note, evidence, wait_s,
                  url: str | None = None) -> Outcome:
    """JazzHR: every field the profile and the person's answers cover is filled; the
    Human Check and Submit are the person's."""
    from backend.apply import jazzhr
    url = url or job.application_url
    say(f"{head}: opening the JazzHR form · {resume_note}")
    result = jazzhr.fill(page, job.id, url, applicant, kept, evidence=evidence)
    if not result.ok:
        say(f"{head}: {result.summary()}")
        outcome.detail = result.error or ""
        return outcome
    say(f"{head}: {result.summary()}")
    waiting = ["tick “I'm not a robot” (the Human Check, outlined in orange)"] if result.captcha else []
    if result.required_blank:
        waiting.append("required: " + ", ".join(result.required_blank[:4]))
    say(f"   Waiting for you — check the answers, {'; '.join(waiting) or 'look it over'}, then press "
        f"Submit Application at the bottom. Close the tab to skip this one.")
    verdict, answers = jazzhr.watch_submission(page, wait_s, evidence=evidence)
    _settle(outcome, job, verdict, "you", url, kept, resume_note, letter, result,
            evidence, answers)
    return outcome


def _settle(outcome, job, verdict, how, url, kept, resume_note, letter, result, evidence,
            answers) -> None:
    """Record and report how one application ended."""
    outcome.result, outcome.how = verdict, how
    # Your written answers ("Describe the deepest AWS estate you have owned…") for the next forms that
    # ask something like them (backend/apply/written.py), whatever became of this one: the last reading of
    # the form, which is what you sent -- or, on a form the app sent itself, what the app typed into it
    # (the answers you gave for this job on the Apply page among them).
    source = answers or (result.filled if verdict == "submitted" else None)
    if source:
        from backend.apply import written
        kept_written = written.keep_from(source, job.company, job.title, job.id)
        if kept_written:
            say(f"   Kept {len(kept_written)} written answer(s) of yours for similar questions later.")
    if verdict == "submitted":
        # The form as it went: your final answers -- what you typed or changed on it -- over what the
        # app typed. Only the app's own fields were kept, and a whole set of answers had to be pasted in.
        final = {**(result.filled or {}), **(answers or {})}
        record_application(job.id, {
            "company": job.company, "title": job.title, "url": url, "how": how,
            "resume": str(kept), "resume_source": resume_note,
            "cover_letter": str(letter) if letter else None,
            "filled": final, "filled_by_app": result.filled, "evidence": str(evidence),
        })
        say(f"   Applied ✓ ({'sent automatically' if how == 'auto' else 'you submitted'})")
        if answers:
            from backend.apply.answers import remember
            learned = remember(answers, company=job.company)
            if learned:
                say(f"   Learned {len(learned)} of your answers for next time: "
                    + "; ".join(learned[:4]))
    elif verdict == "skipped":
        say("   Skipped (tab closed) — it stays in the queue for next time.")
        # What the person typed before closing is still theirs: general answers
        # ("5+ years of Kubernetes?") are kept, so the same question is not asked
        # again. Company-specific ones ("Why five9?") are never reused.
        if answers:
            from backend.apply.answers import remember
            learned = remember(answers, company=job.company)
            if learned:
                say(f"   Kept {len(learned)} of your answers for next time: " + "; ".join(learned[:4]))
    else:
        say("   No submission in the time allowed — moving on; it stays in the queue.")
        if answers:                     # what was typed before time ran out is still theirs
            from backend.apply.answers import remember
            learned = remember(answers, company=job.company)
            if learned:
                say(f"   Kept {len(learned)} of your answers for next time: " + "; ".join(learned[:4]))
