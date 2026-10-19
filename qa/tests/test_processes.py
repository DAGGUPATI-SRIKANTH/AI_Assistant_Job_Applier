"""Asking whether a process runs must never end it -- on Windows os.kill(pid, 0) does."""

import os
import subprocess
import sys

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


@pytest.mark.skipif(os.name != "nt", reason="the /bin/sh wrapper is used elsewhere")
def test_a_page_task_on_windows_runs_and_writes_its_exit_code(tmp_path):
    from backend.api import app as webapp
    exit_path = tmp_path / "task.exit"
    with (tmp_path / "task.log").open("w") as handle:
        process = webapp._start_task_windows(["--help"], exit_path, handle)
        probe(process.pid)                               # the page's check leaves it running
        assert process.wait(timeout=60) == 0
    assert exit_path.read_text().strip() == "0" and "usage" in (tmp_path / "task.log").read_text()
