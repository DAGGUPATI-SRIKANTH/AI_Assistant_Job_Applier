# Job Applier for Mac

Job Applier finds jobs that fit your resume on the job boards of thousands of employers, scores
each one against your resume, and helps you apply — with a resume rewritten for each job, using
only what is already in yours. It runs on your Mac, in your browser.

## What you need

- A Mac with **macOS 13 (Ventura) or later**. Built and tested on Macs with Apple chips; Intel
  Macs have their own package list but have not been tried yet.
- An internet connection and about **1 GB** of free space.
- Your resume as a .docx, .pdf, .txt or .md file.
- Optional, for tailored resumes and cover letters: a Claude, ChatGPT or Gemini plan you already
  have, or an API key. Finding and scoring jobs works without one.

## Install

1. Download `Job-Applier-mac-<version>.zip` from the
   [Releases page](https://github.com/shyamsunderprogramer-design/AI_Assistant_Job_Applier/releases)
   and double-click it to unzip. You can drag **Job Applier** into Applications, but you don't
   have to.
2. Double-click **Job Applier**. The first time, macOS stops it: it was not made by a developer
   registered with Apple. Close that message (don't move the app to the Trash), then:
   - choose **Apple menu > System Settings**, click **Privacy & Security** in the sidebar and
     scroll down to **Security**;
   - click **Open Anyway** (it shows for about an hour after you tried to open the app) and type
     your Mac's login password.

   macOS remembers this; you won't be asked again.
3. Click **Set up**. A window shows each step while Job Applier installs itself in your Library
   folder. It takes a few minutes, longer on a slow connection.
4. Your browser opens the **Welcome** page. Follow its five steps:
   1. upload your resume — the jobs to look for are made from it;
   2. check the job titles and places it will look for, and change them if they're not right;
   3. fill in your details (name, email, phone, where you can work) — needed to apply;
   4. choose who writes your tailored resumes (optional);
   5. find your jobs. The first time takes about an hour; keep the Mac awake and the lid open.

## Every day

Job Applier keeps running in the background while you're logged in. At 6 AM it looks for new
jobs by itself: if the Mac is asleep then, it starts as soon as you open the lid, and if the Mac
was off, it starts when the Mac comes on. You can switch this off on the Welcome or Jobs page.

To open it, double-click **Job Applier**, or bookmark the page it opens
(usually <http://localhost:8770>).

## Update, repair or remove

- **Update:** download the newer zip and open the new **Job Applier**. It asks to update and
  keeps your jobs, resume, details and settings.
- **Repair or remove:** hold the **Option** key while you open Job Applier, then choose
  **Repair** (sets it up again over itself, keeping your things) or **Remove…**. Remove stops it
  and moves its folder, with your data in it, to the Trash: empty the Trash to delete it for good.

## Where your things are

Everything is in `~/Library/Application Support/Job Applier` on your Mac: the app, its Python,
your resume, details, jobs and settings. `install.log` there says what setup did; the app's own
log is `app/data/webapp.log`.

## Privacy

- Setting up downloads Python, the packages Job Applier needs (each one checked against its
  published checksum) and a browser. Nothing about you is sent.
- Finding jobs reads employers' public job boards.
- Your resume and a job's posting go to the AI you chose, and only when it writes something for
  you: a tailored resume or letter, including the ones the morning run prepares for your best
  matches.
- Nothing is ever sent to the people who make Job Applier.

## If something goes wrong

- **Setup could not finish:** click **Show details** to read `install.log`, then open Job Applier
  again — it carries on from where it stopped. Most failures are a dropped internet connection.
- **The page doesn't open:** open Job Applier again; it starts the app if it has stopped.
- **"Job Applier is damaged and can't be opened":** the download was changed on the way. Delete
  it and download the zip again.

---

## For developers

- `devops/mac/build_app.sh [REF] [OUT_DIR]` builds `Job Applier.app` and its zip from a commit
  (`git archive`, never the working folder, which holds your own resume and data).
- `devops/mac/install.sh` (in the app) fetches a pinned, checksummed
  [uv](https://github.com/astral-sh/uv) and Python 3.12; `devops/mac/install.py` does the rest.
  Both are what the app runs; nothing else is needed on the Mac.
- `devops/mac/make_locks.sh` regenerates `devops/mac/requirements-arm64.lock` and
  `requirements-x86_64.lock` from `requirements.txt` and the versions in `.venv`, with a sha256 for
  every file. Run it after changing requirements, and commit both.
- A second copy beside a real one, for testing:
  `open -n --env JOB_APPLIER_HOME=/tmp/ja-test --env JOB_APPLIER_LABEL=com.jobapplier.test "Job Applier.app"`.
  It gets its own folder, its own launchd agents and the next free port.
- The app is signed ad hoc, not notarized. Notarizing needs an Apple Developer account; it would
  remove the **Open Anyway** step.
