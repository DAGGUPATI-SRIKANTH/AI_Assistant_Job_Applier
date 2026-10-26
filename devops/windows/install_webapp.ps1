<#
.SYNOPSIS
  Keep the web app running on Windows: started at logon, restarted if it stops.

.DESCRIPTION
  The Windows counterpart of devops/launchd/com.AI_Assistant_Job_Applier.webapp.plist.
  Registers a Task Scheduler task for the current user (no administrator needed) that
  runs devops/windows/run_webapp.py with pythonw.exe, so there is no console window.
  Output goes to data/webapp.log. The app is then at http://localhost:8770.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File devops\windows\install_webapp.ps1
  powershell -ExecutionPolicy Bypass -File devops\windows\install_webapp.ps1 -Uninstall
#>
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$TaskName = "AI_Assistant_Job_Applier web app"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed the old '$TaskName' task."
}
if ($Uninstall) { return }

if (-not (Test-Path $Pythonw)) {
    throw "No $Pythonw. Create the venv first (docs/WINDOWS_SETUP.md, step 2)."
}

# -X utf8: UTF-8 mode, which a task cannot get from PYTHONUTF8 (docs/WINDOWS_SETUP.md, step 3).
$action = New-ScheduledTaskAction -Execute $Pythonw `
    -Argument "-X utf8 `"$(Join-Path $Root 'devops\windows\run_webapp.py')`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
# run_webapp.py restarts the app if it stops (launchd's KeepAlive); these restart
# run_webapp.py itself if it fails to start, and never time it out.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Web app for $Root, at http://localhost:8770" | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed '$TaskName': running now and at every logon. Log: $Root\data\webapp.log"
