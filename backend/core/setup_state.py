"""Whether this copy is set up enough for a morning run: a resume, and the search made from it.

A morning run without them has nothing to look for. On a new Mac it would read every board against
the example search in config.yaml (software titles) and score the postings against nobody -- and it
would start before the person had seen the app at all, since a 6 AM start the Mac missed is caught
up a minute after the app first opens (backend/api/morning_pages.py). So the scheduled run, the
catch-up and Start all ask here first.

    python -m backend.core.setup_state why     # prints why not, or nothing when a run may start
"""

from __future__ import annotations

import sys
from pathlib import Path

from backend.config.loader import PROJECT_ROOT, load_config


def resume_file(cfg) -> Path | None:
    """The resume the search is made from: the same file the web app shows."""
    from ml.resume.parser import find_base_resume
    configured = Path(cfg.get("resume.base_path") or "ml/resume")
    path = configured if configured.is_absolute() else PROJECT_ROOT / configured
    return find_base_resume(path if path.is_dir() else path.parent)


def search_titles(cfg) -> list[str]:
    """The job titles searched for, made from the resume; empty when none were made."""
    from ml.resume.profile import PROFILE_FILENAME, load_profile
    try:
        profile = load_profile(PROJECT_ROOT / cfg.get("filters.profile_path", PROFILE_FILENAME))
    except Exception:
        return []
    return list(getattr(profile, "titles", None) or []) if profile else []


def why_not_ready(cfg) -> str | None:
    """Why a morning run should not start yet, or None when it may. A person who switched the
    search made from the resume off (filters.use_derived_profile) searches with config.yaml's
    own keywords, and needs only the resume to score against."""
    if resume_file(cfg) is None:
        return "there is no resume yet — upload one on the Welcome page"
    if cfg.get("filters.use_derived_profile", True) and not search_titles(cfg):
        return "no job search could be made from the resume yet — see the Welcome page"
    return None


def main(argv: list[str]) -> int:
    if argv[:1] == ["why"]:
        reason = why_not_ready(load_config())
        if reason:
            print(reason)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
