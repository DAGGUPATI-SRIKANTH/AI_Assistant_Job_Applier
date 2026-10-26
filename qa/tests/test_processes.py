"""Asking whether a process runs must never end it -- on Windows os.kill(pid, 0) does."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.core.processes import probe


def test_a_running_process_is_found_and_left_running():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        probe(child.pid)
        probe(child.pid)
        assert child.poll() is None                      # asked twice, still running
    finally:
        child.kill()
        child.wait()


def test_a_process_that_has_exited_is_not_found():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    with pytest.raises(ProcessLookupError):
        probe(child.pid)


def test_this_process_is_found():
    probe(os.getpid())


# pythonw.exe is how devops/windows/ runs the app: no console, so a child given no explicit
# handles writes nowhere, and the page read an empty log.
@pytest.mark.skipif(os.name != "nt", reason="the /bin/sh wrapper is used elsewhere")
@pytest.mark.parametrize("interpreter", ["python.exe", "pythonw.exe"])
def test_a_page_task_on_windows_runs_and_writes_its_exit_code_and_log(tmp_path, monkeypatch, interpreter):
    from backend.api import app as webapp
    monkeypatch.setattr(sys, "executable", str(Path(sys.executable).with_name(interpreter)))
    exit_path, log_path = tmp_path / "task.exit", tmp_path / "task.log"
    log_path.write_text("")
    process = webapp._start_task_windows(["--help"], exit_path, log_path)
    probe(process.pid)                                   # the page's check leaves it running
    assert process.wait(timeout=60) == 0
    assert exit_path.read_text().strip() == "0" and "usage" in log_path.read_text()
