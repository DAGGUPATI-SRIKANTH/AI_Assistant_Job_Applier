from backend.api.app import cfg, main
from backend.api.morning_pages import watch_mornings
from backend.apply.inbox import watch

# Email about applications, looked at every half hour while the app runs (backend/apply/inbox.py).
# Started here, not on import, so tests and one-off scripts never open the mailbox.
watch(cfg)
# A morning run the Mac missed while it was off, started once it is on (backend/api/morning_pages.py).
watch_mornings()

raise SystemExit(main())
