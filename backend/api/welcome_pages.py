"""The Welcome page: a new person's first steps, in order, on one page.

Every step is something the app already does somewhere else -- the upload on the Jobs page, the
details on /applicant, the writing model in Settings, Start on the Jobs page. Someone who has just
installed the app meets them here once, with what is done ticked off, instead of hunting for them.
It is also where the search made from the resume can be corrected without opening a YAML file:
before this page, "Not right? Edit backend/config/search_profile.yaml" was the only way.
"""

from __future__ import annotations

from flask import jsonify, render_template, request

from backend.api import app as webapp
from backend.api.app import PROJECT_ROOT, app

MAX_TERMS = 60          # per list; a search of more titles than this is a list pasted by mistake
MAX_TERM_LEN = 80


def _profile_path():
    from ml.resume.profile import PROFILE_FILENAME
    return PROJECT_ROOT / webapp.cfg().get("filters.profile_path", PROFILE_FILENAME)


def state() -> dict:
    """Where each first step stands. Never raises: a step that cannot be read shows as not done."""
    from backend.api import morning_pages
    from backend.config.envfile import read_env
    from data_engineering.db.models import Job
    from data_engineering.db.session import get_session
    from sqlalchemy import func
    from ml.resume.llm import PROVIDERS
    from ml.resume.profile import load_profile

    resume = webapp.resume_label()
    profile = load_profile(_profile_path()) if resume else None
    stored = read_env(webapp.env_path())
    provider = (stored.get("RESUME_PROVIDER") or "").strip().lower()
    threshold = float(webapp.cfg().get("resume.min_score", 0.45))
    try:
        with get_session() as session:
            jobs = session.query(Job).filter(Job.is_open.is_(True)).count()
            strong = session.query(Job).filter(Job.is_open.is_(True),
                                               Job.ats_match_score >= threshold).count()
            best = session.query(func.max(Job.ats_match_score)).filter(Job.is_open.is_(True)).scalar()
    except Exception:
        jobs = strong = 0
        best = None
    last = morning_pages.last_run()
    return {
        "resume": resume,
        "search": {
            "titles": list(profile.titles) if profile else [],
            "exclude_titles": list(profile.exclude_titles) if profile else [],
            "locations": list(profile.locations) if profile else [],
            "seniority": profile.seniority if profile else "",
            "years": profile.years_experience if profile else None,
            "families": [f.replace("_", " ") for f in profile.families] if profile else [],
        },
        "details": webapp.applicant_state(),
        "writer": {"chosen": bool(provider),
                   "label": PROVIDERS.get(provider, {}).get("label", "") if provider else ""},
        # The best score as well as the count over the line: a one-page resume covers less of a
        # posting than a long one, and "0 strong matches" read as nothing found.
        "jobs": {"open": jobs, "strong": strong, "best": round(best * 100) if best else None},
        "morning": {"running": bool(morning_pages.pipeline_pids()), "auto": not morning_pages.AUTO_OFF.exists(),
                    "last": last, "waiting": morning_pages.not_ready()},
        "folder": str(PROJECT_ROOT),
        # Installed by the Mac app (devops/mac/install.py writes installed.txt beside the app's
        # folder): the app itself is then how it is updated, repaired or removed.
        "app_installed": (PROJECT_ROOT.parent / "installed.txt").exists(),
    }


@app.get("/welcome")
def welcome_page():
    try:
        boards = webapp.board_count()
    except Exception:
        boards = 0
    return render_template("welcome.html", s=state(), boards=boards)


@app.get("/welcome/state")
def welcome_state():
    return jsonify(ok=True, **state())


def _terms(value) -> list[str]:
    """One term per line (or a list), trimmed, blank and repeated ones dropped."""
    items = value.splitlines() if isinstance(value, str) else list(value or [])
    seen, out = set(), []
    for item in items:
        term = " ".join(str(item).split())[:MAX_TERM_LEN]
        if term and term.lower() not in seen:
            seen.add(term.lower())
            out.append(term)
    return out[:MAX_TERMS]


@app.post("/search/save")
def search_save():
    """The person's own correction of the search made from their resume: the job titles to look
    for, the words that rule a title out, and the places they can work. Kept until the next
    resume upload, which makes a new search from the new resume."""
    from ml.resume.profile import load_profile, save_profile

    body = request.get_json(silent=True) or {}
    profile = load_profile(_profile_path())
    if profile is None:
        return jsonify(ok=False, error="Upload your resume first: the search is made from it."), 409
    titles = _terms(body.get("titles"))
    if not titles:
        return jsonify(ok=False, error="Keep at least one job title to look for."), 400
    profile.titles = titles
    profile.exclude_titles = _terms(body.get("exclude_titles"))
    profile.locations = _terms(body.get("locations"))
    profile.derived_from = "edited"            # by the person, on the Welcome page
    current = webapp.current_resume()
    profile.resume_path = str(current) if current else profile.resume_path
    save_profile(profile, _profile_path())
    return jsonify(ok=True, search=state()["search"])
