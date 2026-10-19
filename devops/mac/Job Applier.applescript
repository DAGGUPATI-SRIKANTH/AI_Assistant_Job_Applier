-- Job Applier, the Mac app (built into "Job Applier.app" by devops/mac/build_app.sh).
--
-- Opened for the first time, it sets Job Applier up in the person's Library folder -- install.sh,
-- then devops/mac/install.py -- with a progress window, and opens the Welcome page. After that it
-- opens the app in the browser. A newer copy of this app offers to update the installed one.
-- Holding Option while opening it offers Open, Repair and Remove.
--
-- JOB_APPLIER_HOME and JOB_APPLIER_LABEL, when set (open --env ...), put a second copy beside
-- the first: that is how it is tested without touching a real install.

use AppleScript version "2.4"
use scripting additions
use framework "Foundation"
use framework "AppKit"

property appTitle : "Job Applier"

on run
	set res to (POSIX path of (path to me)) & "Contents/Resources/"
	set homeDir to my homeFolder()
	set wanted to do shell script "cat " & quoted form of (res & "version.txt")
	if my installing(homeDir) then
		my follow(homeDir, "Setting up Job Applier")
		return
	end if
	set info to my installedInfo(homeDir)
	if info is missing value then
		display dialog "Job Applier will be set up on this Mac, for you." & return & return & ¬
			"It needs an internet connection, about 1 GB of space and a few minutes (longer on a slow connection). It goes in your Library folder, and setting up only downloads what it needs: nothing about you is sent." buttons {"Cancel", "Set up"} ¬
			default button "Set up" cancel button "Cancel" with title appTitle with icon note
		my startInstall(res, homeDir)
		my follow(homeDir, "Setting up Job Applier")
		return
	end if
	if my optionHeld() then
		set choice to button returned of (display dialog "Job Applier " & (item 1 of info) & " is installed on this Mac." & return & return & ¬
			"Repair sets it up again over itself and keeps your jobs, resume and settings. Remove takes it off this Mac." buttons {"Remove…", "Repair", "Open"} ¬
			default button "Open" with title appTitle with icon note)
		if choice is "Remove…" then
			my removeApp(homeDir, info)
			return
		else if choice is "Repair" then
			my startInstall(res, homeDir)
			my follow(homeDir, "Repairing Job Applier")
			return
		end if
	else if (item 1 of info) is not wanted then
		considering numeric strings
			set newer to wanted > (item 1 of info)
		end considering
		if newer then
			set choice to button returned of (display dialog "Update Job Applier from version " & (item 1 of info) & " to " & wanted & "?" & return & return & ¬
				"Your jobs, resume, details and settings are kept." buttons {"Not now", "Update"} default button "Update" with title appTitle with icon note)
			if choice is "Update" then
				my startInstall(res, homeDir)
				my follow(homeDir, "Updating Job Applier")
				return
			end if
		end if
	end if
	my openApp(item 2 of info, item 3 of info, "/")
end run

on homeFolder()
	set chosen to system attribute "JOB_APPLIER_HOME"
	if chosen is not "" then return chosen
	return (POSIX path of (path to library folder from user domain)) & "Application Support/Job Applier"
end homeFolder

-- {version, port, label} of the installed copy, from installed.txt; missing value when there is none.
on installedInfo(homeDir)
	set f to quoted form of (homeDir & "/installed.txt")
	try
		set v to do shell script "sed -n 's/^version=//p' " & f
		set p to do shell script "sed -n 's/^port=//p' " & f
		set l to do shell script "sed -n 's/^label=//p' " & f
	on error
		return missing value
	end try
	if v is "" or p is "" or l is "" then return missing value
	return {v, p, l}
end installedInfo

on installing(homeDir)
	set pidFile to quoted form of (homeDir & "/install.lock/pid")
	return (do shell script "kill -0 \"$(cat " & pidFile & " 2>/dev/null)\" 2>/dev/null && echo yes || echo no") is "yes"
end installing

on optionHeld()
	set flags to (current application's NSEvent's modifierFlags()) as integer
	return ((flags div 524288) mod 2) is 1 -- NSEventModifierFlagOption
end optionHeld

on statusValue(homeDir, key)
	return do shell script "sed -n 's/^" & key & "=//p' " & quoted form of (homeDir & "/status.txt") & " 2>/dev/null | head -1; true"
end statusValue

on startInstall(res, homeDir)
	do shell script "mkdir -p " & quoted form of homeDir & " && rm -f " & quoted form of (homeDir & "/status.txt")
	do shell script "/bin/bash " & quoted form of (res & "install.sh") & " >/dev/null 2>&1 &"
end startInstall

-- The progress window, until the install says done or failed. Stop in the window ends it.
on follow(homeDir, title)
	set progress total steps to 6
	set progress completed steps to 0
	set progress description to title
	set progress additional description to "Starting…"
	set quiet to 0
	try
		repeat
			delay 0.5
			set runState to my statusValue(homeDir, "state")
			if runState is "done" then exit repeat
			if runState is "failed" then error (my statusValue(homeDir, "error")) number 1001
			set stepNo to my statusValue(homeDir, "step")
			set stepText to my statusValue(homeDir, "text")
			if stepNo is not "" then set progress completed steps to (stepNo as integer) - 1
			if stepText is not "" then set progress additional description to stepText
			if not my installing(homeDir) then
				set quiet to quiet + 1
				if quiet > 20 then error "Setup stopped before it finished. Open Job Applier again to carry on." number 1002
			else
				set quiet to 0
			end if
		end repeat
	on error msg number n
		if n is -128 then
			my stopInstall(homeDir)
			display dialog "Setup was stopped. Open Job Applier again to finish it: it carries on from where it got to." buttons {"OK"} ¬
				default button "OK" with title appTitle with icon note
			return
		end if
		set choice to button returned of (display dialog "Job Applier could not be set up." & return & return & msg buttons {"Show details", "OK"} ¬
			default button "OK" with title appTitle with icon caution)
		if choice is "Show details" then do shell script "open -a TextEdit " & quoted form of (homeDir & "/install.log")
		return
	end try
	set progress completed steps to 6
	set progress additional description to "Ready."
	set target to my statusValue(homeDir, "url")
	if target is not "" then do shell script "open " & quoted form of target
end follow

on stopInstall(homeDir)
	set pgidFile to quoted form of (homeDir & "/install.pgid")
	set pidFile to quoted form of (homeDir & "/install.lock/pid")
	do shell script "g=$(cat " & pgidFile & " 2>/dev/null); [ -n \"$g\" ] && kill -TERM -\"$g\" 2>/dev/null; " & ¬
		"p=$(cat " & pidFile & " 2>/dev/null); [ -n \"$p\" ] && { pkill -TERM -P \"$p\"; kill -TERM \"$p\"; } 2>/dev/null; true"
	do shell script "printf 'state=failed\\nerror=Setup was stopped.\\n' > " & quoted form of (homeDir & "/status.txt")
end stopInstall

on answering(base)
	try
		do shell script "curl -s -o /dev/null -m 2 " & quoted form of (base & "/")
		return true
	on error
		return false
	end try
end answering

on openApp(port, label, pagePath)
	set base to "http://localhost:" & port
	if not my answering(base) then
		set agent to quoted form of (label & ".webapp")
		do shell script "launchctl kickstart \"gui/$(id -u)/\"" & agent & " >/dev/null 2>&1 || launchctl bootstrap \"gui/$(id -u)\" ~/Library/LaunchAgents/" & agent & ".plist >/dev/null 2>&1; true"
		repeat 60 times
			delay 0.5
			if my answering(base) then exit repeat
		end repeat
	end if
	do shell script "open " & quoted form of (base & pagePath)
end openApp

on removeApp(homeDir, info)
	display dialog "Remove Job Applier from this Mac?" & return & return & ¬
		"Its folder goes to the Trash with your jobs, resume copies and settings in it: empty the Trash to delete them for good. The morning run stops." buttons {"Cancel", "Remove"} ¬
		default button "Cancel" cancel button "Cancel" with title appTitle with icon caution
	set label to item 3 of info
	try
		do shell script quoted form of (homeDir & "/app/.venv/bin/python") & " -I " & quoted form of (homeDir & "/app/devops/mac/install.py") & ¬
			" --uninstall --home " & quoted form of homeDir & " --label " & quoted form of label
	on error
		-- A copy too damaged to run its own uninstaller: take the agents out directly.
		do shell script "for a in webapp daily; do launchctl bootout \"gui/$(id -u)/\"" & quoted form of label & ".$a 2>/dev/null; " & ¬
			"rm -f ~/Library/LaunchAgents/" & quoted form of label & ".$a.plist; done; true"
	end try
	set fileManager to current application's NSFileManager's defaultManager()
	set {moved, failure} to fileManager's trashItemAtURL:(current application's |NSURL|'s fileURLWithPath:homeDir) resultingItemURL:(missing value) |error|:(reference)
	if moved as boolean then
		display dialog "Job Applier is removed, and its folder is in the Trash." & return & return & "You can drag this app to the Trash too." buttons {"OK"} ¬
			default button "OK" with title appTitle with icon note
	else
		display dialog "Job Applier is stopped, but its folder could not be moved to the Trash (" & (failure's localizedDescription() as text) & "):" & return & return & homeDir buttons {"OK"} ¬
			default button "OK" with title appTitle with icon caution
	end if
end removeApp
