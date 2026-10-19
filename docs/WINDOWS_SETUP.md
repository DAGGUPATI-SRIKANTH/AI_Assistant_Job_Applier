# Setting up on Windows

The [README](../README.md) is written for macOS/Linux. This is the same setup on
Windows 11, plus what does and doesn't work there yet.

Verified on **2026-10-19**: Windows 11 Home, Python 3.12.6, Git 2.54, commit `c6d3b2c`.

## 1. Prerequisites

- **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/windows/).
  The `py` launcher that comes with it lets you pick a version (`py -0` lists them).
- **Git for Windows**, which also gives you Git Bash.

## 2. Clone and install

In PowerShell:

```powershell
git clone https://github.com/shyamsunderprogramer-design/AI_Assistant_Job_Applier.git
cd AI_Assistant_Job_Applier

py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
copy .env.example .env          # discovery and scoring need no API key
```

How this differs from the README:

| macOS / Linux | Windows |
|---|---|
| `.venv/bin/python` | `.venv\Scripts\python` |
| `cp` | `copy` |
| `python3` | `py -3.12` (or `python`) |

You can skip `ocrmac` and `keyring` in `requirements.txt`. They only install on
macOS (`sys_platform == "darwin"`), and pip leaves them out on Windows without
an error.

## 3. Turn on UTF-8 mode (important)

By default, Python on Windows reads and writes text files as `cp1252`, not
UTF-8. Resumes and postings contain characters like `–` and `’`, so without
UTF-8 mode some reads fail with `UnicodeDecodeError`. Turn it on for your user
account once:

```powershell
setx PYTHONUTF8 1
```

Then open a new terminal. To set it only for the current session:

```powershell
$env:PYTHONUTF8 = "1"          # PowerShell
set PYTHONUTF8=1               # cmd.exe
export PYTHONUTF8=1            # Git Bash
```

## 4. Run the tests

```powershell
.venv\Scripts\python -m pytest qa/tests/ -q
```

The full suite takes about 3 minutes. Results on the machine above:

| | Passed | Failed | Errors |
|---|---|---|---|
| Without `PYTHONUTF8` | 1404 | 19 | 9 |
| With `PYTHONUTF8=1` | **1415** | **17** | 0 |

The 17 failures with UTF-8 mode on come from features that are macOS/Unix-only.
They are not broken setup:

| Test file | Why it fails on Windows |
|---|---|
| `test_daily_pipeline_sh.py` (5) | Runs `devops/daily_pipeline.sh` with `bash`, which isn't on the test's PATH |
| `test_morning_run.py` (2) | Uses `os.getpgid` (Unix process groups) to stop a run |
| `test_tailoring_interrupted.py` (1) | Same process check, Unix-only |
| `test_envfile.py`, `test_setup_page.py::test_saving_creates_an_owner_only_file` | Expect file mode `0600`; Windows reports `0666` (438) because it has no Unix permissions |
| `test_setup_page.py` (2 others) | Provider readiness checks expect macOS tools and paths |
| `test_plans.py::test_node_tools_are_found_from_a_background_service` | Splits `PATH` on `:` instead of `os.pathsep`, which breaks on `C:\...` (a real bug, worth fixing) |
| `test_resume_templates.py` (1) | Expects a font that isn't installed, so it falls back to Helvetica |
| `test_auto_apply.py`, `test_daily.py`, `test_letter_edit.py` (1 each) | Not yet investigated |

Each one is a good first issue for making the project fully cross-platform.

## 5. Run the web app

```powershell
.venv\Scripts\python -m backend.api
```

Then open <http://localhost:8770>. Verified: the server starts and the home
page returns `200`.

## 6. Daily routine

```powershell
.venv\Scripts\python main.py daily
```

`devops/launchd/` (background service) and `devops/*.sh` are macOS/bash only.
To run the daily pipeline on a schedule on Windows, use **Task Scheduler** with
an action like this:

```
Program:   E:\path\to\AI_Assistant_Job_Applier\.venv\Scripts\python.exe
Arguments: main.py daily
Start in:  E:\path\to\AI_Assistant_Job_Applier
```

and set `PYTHONUTF8=1` for the user account (step 3).

## 7. Things that are macOS-only for now

- OCR for scanned resumes (`ocrmac`, macOS Vision)
- Storing job-site passwords in the Keychain (`keyring` is installed only on macOS)
- `devops/launchd/` and the `.sh` scripts in `devops/`
