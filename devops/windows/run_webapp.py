"""Keep the web app running on Windows, for the logon task (install_webapp.ps1).

Task Scheduler restarts a task only when it fails to start, not when it stops
later, so launchd's KeepAlive is done here: the app runs as a child, and is
started again ten seconds after it stops. The child is held in a job object
that ends it when this process ends, so stopping the task never leaves an
orphan holding port 8770.

pythonw.exe has no console, so output goes to data/webapp.log, the same file
the macOS launchd job writes (devops/launchd/com.AI_Assistant_Job_Applier.webapp.plist).
"""

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9


class _Limits(ctypes.Structure):
    # JOBOBJECT_EXTENDED_LIMIT_INFORMATION; only LimitFlags is set, the rest stays zero.
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD), ("IoCounters", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _job() -> int:
    """A job object whose processes all end when this process does (its handle closes)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    limits = _Limits(LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                                     ctypes.byref(limits), ctypes.sizeof(limits))
    return job


def main() -> None:
    (ROOT / "data").mkdir(exist_ok=True)
    log = open(ROOT / "data" / "webapp.log", "a", encoding="utf-8", buffering=1)
    sys.stderr = log                       # under pythonw a traceback here would vanish
    # This process joins the job, so every process it starts is in it from the first instruction
    # (the venv's pythonw.exe is a launcher that starts the real interpreter as its own child).
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject(_job(), kernel32.GetCurrentProcess())
    while True:
        code = subprocess.call([sys.executable, "-X", "utf8", "-m", "backend.api"], cwd=ROOT,
                               stdout=log, stderr=log, stdin=subprocess.DEVNULL)
        log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  web app stopped (exit {code}); "
                  "starting it again in 10 s\n")
        time.sleep(10)


if __name__ == "__main__":
    main()
