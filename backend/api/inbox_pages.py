"""Email about your applications: the updates it made, a check on demand, and the pop-up's data."""

from __future__ import annotations

import threading

from flask import jsonify, request

from backend.api.app import app, cfg


@app.get("/inbox/updates")
def inbox_updates():
    """What email changed on the Applied page; `?unseen=1` for the pop-up."""
    from backend.apply import inbox
    cfg()                                  # .env read, so whether a mailbox is set up is known
    unseen = request.args.get("unseen") in ("1", "true", "yes")
    st = inbox.state()
    return jsonify(ok=True, updates=inbox.updates(unseen_only=unseen)[:50], last_check=st.get("last_check"),
                   last_result=st.get("last_result"), running=inbox._busy.locked(),
                   ready=inbox.credentials() is not None)


@app.post("/inbox/updates/seen")
def inbox_seen():
    """The pop-up dismissed: these updates, or every one, are not shown again."""
    from backend.apply import inbox
    ids = (request.get_json(silent=True) or {}).get("ids")
    return jsonify(ok=True, dismissed=inbox.mark_seen(list(ids) if ids else None))


@app.post("/inbox/check")
def inbox_check():
    """Look through the mailbox now, on a thread of its own; the page asks /inbox/updates for the result."""
    from backend.apply import inbox
    cfg()                                  # .env into the environment: the mailbox's address and app password
    if inbox.credentials() is None:
        return jsonify(ok=False, error="No mailbox set up: add MAIL_ADDRESS and MAIL_APP_PASSWORD (a Google app "
                                       "password) to .env."), 400
    if inbox._busy.locked():
        return jsonify(ok=True, started=False, note="A check is already running.")
    threading.Thread(target=inbox.check, name="inbox-check-now", daemon=True).start()
    return jsonify(ok=True, started=True)
