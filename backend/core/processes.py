"""Is a process still running? Asked the same way on macOS, Linux and Windows.

On POSIX, os.kill(pid, 0) sends no signal and only asks. On Windows there is no
signal 0: os.kill(pid, 0) calls TerminateProcess, so every "is the fetch still
going?" from the Jobs page ended the fetch it asked about. probe() is os.kill(pid, 0)
where that is safe, and an OpenProcess query where it is not.
"""

from __future__ import annotations

import os

_STILL_ACTIVE = 259                        # GetExitCodeProcess, for a process that has not exited
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5


def probe(pid: int) -> None:
    """os.kill(pid, 0) without the Windows side effect: returns if the process is running,
    raises ProcessLookupError if it is not, PermissionError if it is but is not ours to ask."""
    if os.name != "nt":
        os.kill(pid, 0)
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        if ctypes.get_last_error() == _ERROR_ACCESS_DENIED:
            raise PermissionError(f"pid {pid} is not ours to ask about")
        raise ProcessLookupError(f"no process {pid}")
    try:
        code = wintypes.DWORD()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != _STILL_ACTIVE:
            raise ProcessLookupError(f"process {pid} has exited")
    finally:
        kernel32.CloseHandle(handle)
