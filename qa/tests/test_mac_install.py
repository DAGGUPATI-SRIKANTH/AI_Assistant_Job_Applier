"""The Mac installer (devops/mac/install.py): an update replaces only what the last version put
there, the port and agents a copy is given, and what counts as running from a copy."""

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "devops" / "mac"))
import install  # noqa: E402


def write(path: Path, text: str, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def test_an_update_keeps_everything_the_person_made(tmp_path):
    payload, app = tmp_path / "payload", tmp_path / "app"
    write(payload / "main.py", "v1")
    write(payload / "devops" / "daily_pipeline.sh", "#!/bin/bash\necho v1\n", 0o755)
    write(payload / "old_module.py", "goes in v2")
    first = install.sync(payload, app, [])
    assert first == ["devops/daily_pipeline.sh", "main.py", "old_module.py"]
    assert (app / "devops" / "daily_pipeline.sh").stat().st_mode & 0o777 == 0o755

    # What the person made: none of it is in any payload.
    mine = [write(app / ".env", "RESUME_PROVIDER=claude-code\n"), write(app / "data" / "jobs.db", "db"),
            write(app / "ml" / "resume" / "base_resume.docx", "resume"),
            write(app / "backend" / "config" / "applicant.yaml", "name: me")]

    (payload / "old_module.py").unlink()
    write(payload / "main.py", "v2")
    write(payload / "new_page.py", "new in v2")
    second = install.sync(payload, app, first)
    assert second == ["devops/daily_pipeline.sh", "main.py", "new_page.py"]
    assert (app / "main.py").read_text() == "v2" and (app / "new_page.py").exists()
    assert not (app / "old_module.py").exists()                    # the old version's own file, gone
    assert all(p.exists() for p in mine)                           # the person's, untouched
    assert not list(app.rglob(".*.installing"))


def test_a_running_script_keeps_reading_the_copy_it_started_with(tmp_path):
    """Each file is written beside its place and renamed over it: a new file, never the open one
    rewritten (bash reads a script as it runs, and an edit in place changes what it runs next)."""
    payload, app = tmp_path / "payload", tmp_path / "app"
    write(payload / "run.sh", "echo one\n", 0o755)
    install.sync(payload, app, [])
    with (app / "run.sh").open() as running:
        write(payload / "run.sh", "echo two\n", 0o755)
        install.sync(payload, app, ["run.sh"])
        assert running.read() == "echo one\n"
    assert (app / "run.sh").read_text() == "echo two\n"


def test_a_file_the_person_made_is_never_removed_even_if_named_like_old_code(tmp_path):
    payload, app = tmp_path / "payload", tmp_path / "app"
    write(payload / "main.py", "v1")
    write(app / "notes.py", "the person's own")
    install.sync(payload, app, ["main.py"])                        # notes.py was never installed
    assert (app / "notes.py").exists()


def test_the_port_given_before_is_kept_and_a_new_one_is_the_first_free(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    taken = {8770, 8771}
    free = lambda port: port not in taken                           # noqa: E731
    assert install.choose_port(app, {}, free) == 8772               # 8770 is this Mac's other copy
    assert install.choose_port(app, {"port": 8790}, free) == 8790   # an update keeps its port
    write(app / ".env", "OTHER=1\nJOB_APPLIER_PORT=8775\n")
    assert install.choose_port(app, {"port": 8790}, free) == 8775   # .env says it last
    with pytest.raises(install.Stop):
        install.choose_port(tmp_path, {}, lambda port: False)


def test_the_port_is_written_to_env_without_losing_what_is_there(tmp_path):
    app = tmp_path / "app"
    write(app / ".env", "# my keys\nANTHROPIC_API_KEY=sk-test\n")
    sys.path.insert(0, str(ROOT))
    install.remember_port(app, 8771)
    text = (app / ".env").read_text()
    assert "ANTHROPIC_API_KEY=sk-test" in text and "# my keys" in text and "JOB_APPLIER_PORT=8771" in text
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    install.remember_port(fresh, 8770)
    assert not (fresh / ".env").exists()                            # the usual port needs no line


def test_the_agents_run_this_copy_and_are_valid_plists(tmp_path):
    app = tmp_path / "Application Support" / "Job Applier" / "app"
    specs = install.agents(app, "com.jobapplier.test")
    web, daily = specs["com.jobapplier.test.webapp"], specs["com.jobapplier.test.daily"]
    assert web["ProgramArguments"] == [str(app / ".venv" / "bin" / "python"), "-m", "backend.api"]
    assert web["KeepAlive"] and web["RunAtLoad"] and web["WorkingDirectory"] == str(app)
    # The morning run exactly as the web app recognises it: /bin/bash <script> daily.
    assert daily["ProgramArguments"] == ["/bin/bash", str(app / "devops" / "daily_pipeline.sh"), "daily"]
    assert daily["StartCalendarInterval"] == [{"Hour": 6, "Minute": 0}] and daily["RunAtLoad"] is False
    for spec in specs.values():
        path = tmp_path / f"{spec['Label']}.plist"
        path.write_bytes(plistlib.dumps(spec))
        assert subprocess.run(["plutil", "-lint", str(path)], capture_output=True).returncode == 0


def test_only_processes_from_this_copy_count(tmp_path):
    app = tmp_path / "Job Applier" / "app"
    ps = "\n".join([
        f"101 {app}/.venv/bin/python -m backend.api",
        f"102 /bin/bash {app}/devops/daily_pipeline.sh daily",
        f"103 {tmp_path}/Job Applier/app2/.venv/bin/python -m backend.api",   # a copy beside it
        "104 /usr/bin/python3 -m http.server",
    ])
    assert [pid for pid, _ in install.processes_under(app, ps)] == [101, 102]


def test_status_is_one_value_per_line(tmp_path):
    install.report(tmp_path, step=3, text="Installing the parts\nit needs…")
    lines = (tmp_path / "status.txt").read_text().splitlines()
    assert lines == ["state=running", "step=3", "total=6", "text=Installing the parts it needs…"]


def test_both_chips_have_a_locked_package_list():
    for chip in ("arm64", "x86_64"):
        lock = (ROOT / "devops" / "mac" / f"requirements-{chip}.lock").read_text()
        pins = [line for line in lock.splitlines() if line and line[0].isalpha()]
        assert len(pins) > 50 and all("==" in p for p in pins)
        assert lock.count("--hash=sha256:") >= len(pins)          # every package checked
        assert "/Users/" not in lock and "/Volumes/" not in lock and "/tmp" not in lock
