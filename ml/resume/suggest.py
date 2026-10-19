"""What a posting asks for that the resume does not show, with a line drafted for each.

The job page lists them before tailoring: each one ticked, with the job it
would go under and a line the person can edit. They untick anything that is
not true for them and press "Add all"; what they add becomes part of their
resume (ml/resume/additions.py) for this job and every later one, so the same
skill is asked about once, not once per posting.

Nothing is added without that press. Suggestions are kept per job in
data/suggestions/<job id>.json and made again only when the resume or the
posting changes; the daily run prepares them for the top new jobs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
from pathlib import Path

from backend.config.loader import PROJECT_ROOT

log = logging.getLogger(__name__)

FOLDER = PROJECT_ROOT / "data" / "suggestions"
MAX_ITEMS = 12

SYSTEM = """You draft resume bullet points. For each skill below, write ONE bullet the
person could add under one of their jobs, so the resume shows that skill.

Rules:
- Pick the job where the skill fits the work best (by its index).
- Match that job's existing bullets in subject and tone; it must read as the same person's work.
- Plain, natural English, like a person wrote it: start with a past-tense verb, 12-24 words,
  one sentence. No buzzwords (spearheaded, leveraged, utilized, robust, seamless, cutting-edge).
- Name the skill exactly as given, once.
- No percentages, money, headcounts or other numbers.
- No employer or product names other than the skill itself.

Reply with JSON only: {"items": [{"skill": "...", "job": <index>, "line": "..."}]}"""


def _path(job_id: int) -> Path:
    return FOLDER / f"{job_id}.json"


def _key(resume_text: str, jd_text: str) -> str:
    return hashlib.sha256((resume_text + "\x1f" + jd_text).encode()).hexdigest()[:16]


def role_blocks(resume, limit: int = 4) -> list[tuple[str, list[str]]]:
    """Each job line with a few of its bullets, most recent first."""
    from ml.resume.additions import roles
    from ml.resume.parser import strip_bullet
    from ml.resume.writer import _is_role_line
    wanted = roles(resume)
    blocks: dict[str, list[str]] = {r: [] for r in wanted}
    for section in resume.sections:
        current = None
        for line in section.lines:
            text = line.strip()
            if text in blocks:
                current = text
            elif current and text and not _is_role_line(text):
                blocks[current].append(strip_bullet(text))
            elif _is_role_line(text):
                current = None
    return [(r, blocks[r][:limit]) for r in wanted]


def missing(resume, jd_text: str) -> list[dict]:
    """Skills the posting asks for that the resume does not show in its work.

    By rules, no model: what the matching calls Not found, and what it calls
    Unclear because it sits only in a skills list. Required ones first.
    """
    from ml.tailoring import matching, requirements
    from ml.tailoring.reader import read_document
    from ml.tailoring.structure import build

    reqs, _ = requirements.extract(jd_text, None, use_model=False)
    with tempfile.TemporaryDirectory() as tmp:
        text_file = Path(tmp) / "resume.txt"
        text_file.write_text(resume.text(), encoding="utf-8")
        model = build(read_document(text_file))
    out, seen = [], set()
    for req in reqs:
        if req.category not in ("tool", "skill", "certification"):
            continue
        status = (matching.rule_match(req, model) or ("not_found", [], ""))[0]
        if status not in ("not_found", "unclear"):
            continue
        name = req.original.strip()
        if name.lower() in seen or len(name.split()) > 4:
            continue                       # a sentence is a duty, not a skill to add
        seen.add(name.lower())
        out.append({"skill": name, "required": req.priority == "required",
                    "why": "only in your skills list" if status == "unclear" else "not on your resume"})
    out.sort(key=lambda m: not m["required"])
    return out[:MAX_ITEMS]


def from_report(job_id: int) -> list[dict]:
    """The gaps the job's full requirement check found: what it calls Not found, and short items it
    calls Unclear. The rules above read the posting's sentences whole, so "MDM" and "device
    enrollment" inside "Deep experience securing and managing Linux fleets with configuration
    management, MDM, device enrollment…" were never offered; the full check lists them one by one."""
    from ml.tailoring.pipeline import folder_of, job_best
    best, _ = job_best(job_id)
    folder = folder_of(best) if best else None
    try:
        report = json.loads((folder / "result.json").read_text())["report"] if folder else {}
    except (OSError, ValueError, KeyError):
        return []
    out = []
    for match in report.get("matches") or []:
        req = match.get("requirement") or {}
        name = (req.get("original") or "").strip()
        if match.get("status") not in ("not_found", "unclear") or not name or len(name.split()) > 4:
            continue
        if req.get("category") in ("soft_skill", "trait", "education") or name.lower() in _SOFT:
            continue
        out.append({"skill": name, "required": req.get("priority") == "required",
                    "why": "not on your resume" if match.get("status") == "not_found"
                    else "not shown clearly on your resume"})
    return out


# Qualities a line of work cannot honestly be drafted for: a person shows them, an interview tests them.
_SOFT = {"initiative", "analytical judgment", "attention to detail", "stakeholder communication",
         "cross-functional collaboration", "communication", "teamwork", "leadership"}


def draft(items: list[dict], blocks: list[tuple[str, list[str]]], cfg=None) -> list[dict]:
    """A line under a job for each item. Without a model, a plain starting line to edit."""
    if not items or not blocks:
        return []
    jobs = "\n".join(f"[{i}] {role}\n" + "\n".join(f"    - {b}" for b in bullets)
                     for i, (role, bullets) in enumerate(blocks))
    user = f"The person's jobs:\n{jobs}\n\nSkills to show:\n" + "\n".join(f"- {m['skill']}" for m in items)
    drafted: dict[str, dict] = {}
    try:
        from ml.resume.llm import complete
        reply = complete(SYSTEM, user, cfg, max_tokens=3000).text
        body = json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
        for d in body.get("items", []):
            if isinstance(d, dict) and d.get("skill") and d.get("line"):
                drafted[str(d["skill"]).lower()] = d
    except Exception:
        drafted = {}
    out = []
    for m in items:
        d = drafted.get(m["skill"].lower(), {})
        index = d.get("job") if isinstance(d.get("job"), int) and 0 <= d.get("job") < len(blocks) else 0
        line = re.sub(r"\s+", " ", str(d.get("line") or "")).strip()
        if m["skill"].lower() not in line.lower():
            line = f"Used {m['skill']} in day-to-day work on this team."     # a start; edit it
            drafted_by_model = False
        else:
            drafted_by_model = True
        out.append({**m, "role": blocks[index][0], "line": line, "drafted": drafted_by_model})
    return out


# -- "I've used this": the line written for the person, from their own lines at that job ----------

USED_SYSTEM = """You write resume bullet points. The person has said they used the skill below in each
job listed. For each job write ONE bullet that shows it, as that job's own bullets would.

Rules:
- Build it from that job's existing bullets: the same systems, kind of work and setting, so it
  reads as the same person's work there. Frame it the way the posting uses the skill, but claim
  only work that fits what that job's bullets already describe.
- Add no scale, places, teams, users or results that the job's bullets do not state ("across
  regions", "company-wide", "for thousands of users" only if they say so).
- Name the skill exactly as given, once. Name other tools only if that job's bullets already do.
- Plain, natural English, like a person wrote it: start with a past-tense verb, 12-24 words,
  one sentence, no "I", "my", "we" or "our". No buzzwords (spearheaded, leveraged, utilized,
  robust, seamless, cutting-edge).
- No percentages, money, headcounts or other numbers. No employer, client or product names that
  are not in that job's bullets.

Reply with JSON only: {"lines": [{"job": <index>, "line": "..."}]}"""

# Written lines, kept while the app runs: reopening the form, or ticking a job again, costs no
# second model call. Keyed by the resume and the skill, so a changed resume is written for afresh.
_written: dict[tuple[str, str], str] = {}


def posting_lines(jd_text: str, skill: str, limit: int = 3) -> list[str]:
    """The posting's own sentences that name the skill: what the employer wants done with it."""
    from ml.resume.additions import mentions
    out: list[str] = []
    for raw in re.split(r"(?<=[.!?])\s+|\n+", jd_text or ""):
        text = raw.strip(" \t-•*·")
        if len(text.split()) >= 3 and mentions(text, skill) and text[:300] not in out:
            out.append(text[:300])
            if len(out) == limit:
                break
    return out


def _fits(line: str, skill: str, bullets: list[str]) -> bool:
    """A written line is used only if it names the skill, reads as a resume line (no "I", "my" or
    "we": "for the clusters I managed" does not), and has no number the job's own lines do not
    have -- "cut login failures by 40%" would be made up."""
    from ml.resume.additions import MAX_LINE, mentions
    if not mentions(line, skill) or not 5 <= len(line.split()) <= 40 or len(line) > MAX_LINE:
        return False
    rest = re.sub(re.escape(skill), " ", line, flags=re.I)
    if re.search(r"\bI\b", rest) or re.search(r"\b(my|we|our)\b", rest, flags=re.I):
        return False
    known = " ".join(bullets)
    return all(n in known for n in re.findall(r"(?<![\w.])\d[\d,.]*%?(?![\w])", rest))


def draft_used(cfg, job, skill: str, roles: list[str], again: bool = False) -> list[dict]:
    """A line for each job the person says they used the skill in: written from that job's own
    lines and framed by what the posting asks, for them to check before it goes on the resume.

    Returns [{"role", "line", "drafted"}] in the order asked, each role as it was sent. A line
    the model could not write, or wrote wrong, comes back empty for the person to write.
    """
    from ml.resume.pipeline import load_base_resume
    resume = load_base_resume(cfg)                       # with the lines added before
    plain = lambda text: re.sub(r"\s+", " ", text or "").strip()   # noqa: E731
    blocks = {plain(role): bullets for role, bullets in role_blocks(resume, limit=10)}
    picked = [(role, blocks[plain(role)]) for role in roles if plain(role) in blocks]
    key = _key(resume.text(), skill.strip().lower())
    todo = [(role, bullets) for role, bullets in picked if again or (key, plain(role)) not in _written]
    if todo:
        asked = posting_lines(f"{job.title}\n\n{job.description or ''}", skill)
        jobs = "\n".join(f"[{i}] {role}\n" + "\n".join(f"    - {b}" for b in bullets)
                         for i, (role, bullets) in enumerate(todo))
        user = (f"Skill: {skill}\n\nWhat the posting ({job.title}) says about it:\n"
                + ("\n".join(f"- {a}" for a in asked) or "- (it is only named, in a list)")
                + f"\n\nThe jobs:\n{jobs}")
        try:
            from ml.resume.llm import complete
            reply = complete(USED_SYSTEM, user, cfg, max_tokens=2000).text
            body = json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
            for item in body.get("lines") or []:
                index = item.get("job") if isinstance(item, dict) else None
                if not isinstance(index, int) or not 0 <= index < len(todo):
                    continue
                line = re.sub(r"\s+", " ", str(item.get("line") or "")).strip().lstrip("-•* ").strip()
                if _fits(line, skill, todo[index][1]):
                    _written[(key, plain(todo[index][0]))] = line
        except Exception as exc:                          # no model, or a reply that is not JSON
            log.warning("could not write a line for %s: %s", skill, exc)
    return [{"role": role, "line": _written.get((key, plain(role)), ""),
             "drafted": (key, plain(role)) in _written} for role, _ in picked]


def for_job(cfg, job, refresh: bool = False) -> dict:
    """The suggestions for one job, made or loaded."""
    from ml.resume.pipeline import load_base_resume
    resume = load_base_resume(cfg)                       # with what was added before
    jd_text = f"{job.title}\n\n{job.description or ''}"
    key = _key(resume.text(), jd_text)
    path = _path(job.id)
    if not refresh:
        try:
            saved = json.loads(path.read_text())
            if saved.get("key") == key:
                return saved
        except (OSError, ValueError):
            pass
    blocks = role_blocks(resume)
    found, seen = [], set()
    for item in from_report(job.id) + missing(resume, jd_text):      # the full check first, then the rules
        if item["skill"].lower() not in seen:
            seen.add(item["skill"].lower())
            found.append(item)
    found.sort(key=lambda m: not m["required"])
    items = draft(found[:MAX_ITEMS], blocks, cfg)
    result = {"key": key, "items": items, "roles": [r for r, _ in blocks]}
    FOLDER.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1))
    return result


def accept(cfg, job_id: int, chosen: list[dict]) -> tuple[int, list[str]]:
    """Add the lines the person kept. Returns how many, and why any were not."""
    from ml.resume.additions import NotAdded, add
    from ml.resume.pipeline import load_base_resume
    resume = load_base_resume(cfg)
    added, problems = 0, []
    for item in chosen:
        try:
            add(resume, str(item.get("role") or ""), str(item.get("line") or ""),
                str(item.get("skill") or ""), confirmed=True)
            added += 1
        except NotAdded as exc:
            problems.append(f"{item.get('skill')}: {exc}")
    _path(job_id).unlink(missing_ok=True)                # the resume changed; ask again fresh
    return added, problems
