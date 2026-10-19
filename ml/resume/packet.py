"""One folder per application, holding everything needed to apply to it.

Tailoring already produces files, but they land loose in `ml/resume/output/` with
a shared stem, so preparing to apply means hunting for three filenames. A
packet gathers them under one directory named for the posting.

`job.md` is written first and unconditionally: it needs no model and no API
key, so a packet is useful the moment it exists — even before a resume has
been tailored or a letter drafted.
"""

from __future__ import annotations

import json

import logging
from dataclasses import dataclass, field
from pathlib import Path

from backend.config.loader import PROJECT_ROOT
from backend.core.jobage import age_label
from backend.core.jobfields import UNSTATED, experience_label, salary_label, workplace_label
from ml.resume.writer import output_filename

log = logging.getLogger(__name__)

JOB_SUMMARY = "job.md"
RESUME_FILE = "resume.docx"
LETTER_FILE = "cover-letter.md"
REVIEW_FILE = "review.txt"


@dataclass
class Packet:
    """Where a packet is and what it is still missing."""

    path: Path
    job_id: int
    company: str
    title: str
    has_resume: bool = False
    has_letter: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.has_resume and self.has_letter

    def missing(self) -> list[str]:
        gaps = []
        if not self.has_resume:
            gaps.append("tailored resume")
        if not self.has_letter:
            gaps.append("cover letter")
        return gaps


def packet_root(cfg) -> Path:
    root = Path(cfg.get("resume.output_dir", "ml/resume/output"))
    return root if root.is_absolute() else PROJECT_ROOT / root


# -- the name a file goes out under ------------------------------------------------------------

def file_name(person: str, company: str, kind: str, suffix: str) -> str:
    """"Jordan_Example_Resume_Acme.pdf": who, what, for whom -- the same in the job's folder and
    on the employer's form. The company keeps its own capitals and drops "Inc."/"LLC"; anything
    that is not a letter or a digit becomes one underscore, so the name survives every upload form."""
    import re
    clean = lambda text: re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")
    firm = clean(company_label(company))
    if len(firm) > 40:
        firm = firm[:40].rsplit("_", 1)[0]
    parts = [clean(person) or "Applicant", kind, firm]
    return "_".join(p for p in parts if p) + suffix


_SMALL = {"of", "and", "the", "for", "at", "in", "on", "de"}


def company_label(company: str) -> str:
    """A company as a person writes it: "ABBOTT POINT OF CARE INC. (AN ABBOTT COMPANY)" is
    "Abbott Point of Care", "ACCENTURE LLP" is "Accenture", "2k games" is "2k Games". Names written
    with their own capitals ("GitHub", "Hooli AI") are left as they are."""
    import re
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", company or "")
    suffix = re.compile(r"[,.]?\s*\b(inc|llc|llp|lp|ltd|limited|corp|corporation|co|plc|gmbh|l\.l\.c)\b\.?\s*$", re.I)
    while suffix.search(text.strip()):
        text = suffix.sub("", text.strip())
    words = text.replace(",", " ").split()
    if words and (text.isupper() or text.islower()):
        def word(i, w):
            if w.lower() in _SMALL and i:
                return w.lower()
            if text.isupper() and (len(w) <= 3 or re.search(r"[^A-Za-z]", w)):
                return w                                # IBM, AI, AT&T
            return w[:1].upper() + w[1:].lower()
        words = [word(i, w) for i, w in enumerate(words)]
    return " ".join(words)


def person_name() -> str:
    """The person's name as their profile gives it, or "" before there is a profile."""
    try:
        from backend.apply.profile import DEFAULT_PATH, load
        return load(str(PROJECT_ROOT / DEFAULT_PATH)).full_name
    except Exception:
        return ""


def company_in(directory: Path) -> str:
    """The company a job folder is for, as its job.md names it ("## Acme")."""
    try:
        for line in (directory / JOB_SUMMARY).read_text(encoding="utf-8").splitlines()[:4]:
            if line.startswith("## "):
                return line[3:].strip()
    except OSError:
        pass
    return ""


def resume_in(directory: Path, suffix: str = ".docx") -> Path | None:
    """The folder's tailored resume: under the person's name, else the old "resume.docx"."""
    if not directory or not directory.is_dir():
        return None
    named = sorted(directory.glob(f"*_Resume_*{suffix}")) + sorted(directory.glob(f"*_Resume{suffix}"))
    return next((p for p in [*named, directory / f"resume{suffix}"] if p.exists()), None)


def put_resume(directory: Path, docx: Path, pdf: Path | None = None, company: str | None = None) -> Path:
    """Put a tailored resume in the job's folder under the person's name; the copy it replaces,
    under any older name, is removed, so the folder holds one resume -- the one that is sent."""
    import shutil
    company = company_in(directory) if company is None else company
    stem = file_name(person_name(), company, "Resume", "")
    keep = {f"{stem}.docx", f"{stem}.pdf"}
    for old in [*directory.glob("*_Resume*.docx"), *directory.glob("*_Resume*.pdf"),
                directory / "resume.docx", directory / "resume.pdf"]:
        if old.exists() and old.name not in keep:
            old.unlink()
    target = directory / f"{stem}.docx"
    shutil.copy2(docx, target)
    if pdf is not None and Path(pdf).exists():
        shutil.copy2(pdf, directory / f"{stem}.pdf")
    return target


def name_resume(directory: Path, company: str | None = None) -> list[str]:
    """Rename a folder's "resume.docx"/"resume.pdf" to the person's name; returns the new names."""
    company = company_in(directory) if company is None else company
    stem = file_name(person_name(), company, "Resume", "")
    renamed = []
    for suffix in (".docx", ".pdf"):
        old, new = directory / f"resume{suffix}", directory / f"{stem}{suffix}"
        if old.exists() and not new.exists():
            old.rename(new)
            renamed.append(new.name)
    return renamed


def packet_dir(cfg, company: str, title: str, external_id: str = "",
               job_id: int | None = None) -> Path:
    """One directory per posting, named the way the resume file already is.

    Sharing `output_filename`'s stem keeps a packet traceable to exactly one
    posting, and means an existing tailored resume can be found beside it.
    Where the person chose to put a job's folder wins; then their default
    location; then the output directory.
    """
    places = load_places(cfg)
    if job_id is not None and str(job_id) in places["jobs"]:
        return Path(places["jobs"][str(job_id)])
    base = Path(places["default"]) if places.get("default") else packet_root(cfg)
    return base / packet_name(company, title, external_id)


def packet_name(company: str, title: str, external_id: str = "") -> str:
    return output_filename(company, title, external_id)[:-5]     # drop ".docx"


# -- where the person wants application folders ----------------------------------

def _places_file(cfg) -> Path:
    return packet_root(cfg) / ".folder-places.json"


def load_places(cfg) -> dict:
    """{"default": base folder or None, "jobs": {job id: that job's folder}}."""
    try:
        data = json.loads(_places_file(cfg).read_text())
        return {"default": data.get("default"), "jobs": dict(data.get("jobs") or {})}
    except (OSError, ValueError):
        return {"default": None, "jobs": {}}


def choose_place(cfg, job, base: Path, remember: bool = False) -> Path:
    """Put this job's folder in `base`; with `remember`, every new one too."""
    places = load_places(cfg)
    folder = Path(base) / packet_name(job.company, job.title, job.external_id or "")
    places["jobs"][str(job.id)] = str(folder)
    if remember:
        places["default"] = str(base)
    path = _places_file(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(places, indent=1))
    return folder


def _line(label: str, value: str) -> str:
    return f"- **{label}:** {value}\n"


def render_job_summary(job) -> str:
    """Everything about the posting worth having open while applying."""
    facts = [
        ("Workplace", workplace_label(job.workplace)),
        ("Experience", experience_label(job.experience_min_years,
                                        job.experience_max_years)),
        ("Salary", salary_label(job.salary_min, job.salary_max,
                                job.salary_currency, job.salary_period)),
        ("Posted", age_label(job) or UNSTATED),
        ("Location", job.location or UNSTATED),
        ("Board", job.source),
    ]

    out = [f"# {job.title}\n", f"## {job.company}\n\n"]
    if job.application_url:
        out.append(f"**[Apply]({job.application_url})**\n\n")
    for label, value in facts:
        out.append(_line(label, value))
    out.append(f"\n<!-- job id {job.id} · scored "
               f"{(job.ats_match_score or 0) * 100:.0f}% -->\n")

    if job.requirements:
        out.append("\n## What they ask for\n\n")
        out.append(job.requirements.strip() + "\n")

    # The full description last: useful to have, too long to lead with.
    if job.description:
        out.append("\n## Full posting\n\n")
        out.append(job.description.strip() + "\n")
    return "".join(out)


def build_packet(cfg, job, letter_text: str | None = None) -> Packet:
    """Create or refresh the packet folder for one job.

    Never deletes: a letter drafted earlier survives a rebuild, so re-running
    this to pick up a newly tailored resume cannot cost you work.
    """
    directory = packet_dir(cfg, job.company, job.title, job.external_id or "",
                           job_id=getattr(job, "id", None))
    directory.mkdir(parents=True, exist_ok=True)

    (directory / JOB_SUMMARY).write_text(render_job_summary(job), encoding="utf-8")

    if letter_text:
        (directory / LETTER_FILE).write_text(letter_text, encoding="utf-8")

    name_resume(directory, job.company)               # a folder from before files carried the person's name
    held = resume_in(directory)
    packet = Packet(
        path=directory,
        job_id=job.id,
        company=job.company,
        title=job.title,
        has_resume=held is not None,
        has_letter=(directory / LETTER_FILE).exists(),
    )

    # The tailored resume is written beside the folder under the posting's name (the one the job
    # page and Auto-apply use). Bring it in when the folder has none, or an older one: the folder
    # holds what is sent.
    legacy = packet_root(cfg) / output_filename(job.company, job.title, job.external_id or "")
    if legacy.exists() and (held is None or legacy.stat().st_mtime > held.stat().st_mtime + 1):
        put_resume(directory, legacy, legacy.with_suffix(".pdf"), job.company)
        if not packet.has_resume:
            packet.has_resume = True
            packet.notes.append(f"adopted the existing tailored resume ({legacy.name})")
            review = legacy.with_name(legacy.stem + "_review.txt")
            if review.exists():
                (directory / REVIEW_FILE).write_text(
                    review.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
                )
    return packet
