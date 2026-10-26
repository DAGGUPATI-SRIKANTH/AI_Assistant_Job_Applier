# Windows background service

The Windows counterpart of [`devops/launchd/`](../launchd/): it keeps the web app
running, so http://localhost:8770 is there whenever you are logged in.

| File | What it does |
|---|---|
| `install_webapp.ps1` | Registers a Task Scheduler task that starts the app at logon and restarts it if it stops |
| `run_webapp.py` | Runs the app with no console window, starts it again 10 s after it stops, and writes its output to `data/webapp.log` |

## Install

From the project folder, after the venv is set up ([docs/WINDOWS_SETUP.md](../../docs/WINDOWS_SETUP.md)):

```powershell
powershell -ExecutionPolicy Bypass -File devops\windows\install_webapp.ps1
```

It needs no administrator rights, starts the app straight away, and starts it
again at every logon. Running it again replaces the task, which is safe.

## Check, stop, remove

```powershell
Get-ScheduledTask "AI_Assistant_Job_Applier web app"      # State: Running
Get-Content data\webapp.log -Tail 20                         # the app's log
Stop-ScheduledTask "AI_Assistant_Job_Applier web app"     # stop until next logon
powershell -ExecutionPolicy Bypass -File devops\windows\install_webapp.ps1 -Uninstall
```

You can also find the task in Task Scheduler under its name.
