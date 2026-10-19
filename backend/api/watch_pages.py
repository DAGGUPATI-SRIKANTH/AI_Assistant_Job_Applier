"""The Watch list page: companies the person follows, and jobs they saved for later."""

from __future__ import annotations

import threading

from flask import jsonify, render_template, request

from backend.api.app import app, cfg


@app.get("/watchlist")
def watchlist_page():
    return render_template("watchlist.html")


@app.get("/watchlist/data")
def watchlist_data():
    """Both tabs' contents: every watched company with its open roles, and every saved job."""
    from backend.core import watchlist
    cfg()                                        # the database the views read
    return jsonify(ok=True, companies=watchlist.companies_view(), jobs=watchlist.jobs_view())


@app.post("/watchlist/companies")
def watchlist_add_company():
    """Watch a company by name or by a link to its job board.

    A board link is put on the daily fetch and read now. A name with no board the fetch reads
    sends the careers finder looking for one, on a thread of its own; the page follows along.
    """
    from backend.core import watchlist
    config = cfg()
    text = str((request.get_json(silent=True) or {}).get("name") or "")
    try:
        entry = watchlist.add_company(text, on_board=lambda e: watchlist.read_board_now(config, e))
    except watchlist.NotWatched as exc:
        return jsonify(ok=False, error=str(exc)), 400
    if not entry.get("lookup"):
        view = next((c for c in watchlist.companies_view() if c["key"] == entry["key"]), None)
        if view is not None and not view["boards"]:
            threading.Thread(target=watchlist.find_board, args=(config, entry["key"]), daemon=True).start()
    return jsonify(ok=True, entry=entry)


@app.post("/watchlist/companies/<key>/remove")
def watchlist_remove_company(key: str):
    from backend.core import watchlist
    return jsonify(ok=watchlist.remove_company(key))


@app.post("/watchlist/companies/<key>/seen")
def watchlist_seen(key: str):
    from backend.core import watchlist
    watchlist.mark_seen(key)
    return jsonify(ok=True)


@app.post("/watchlist/companies/<key>/find")
def watchlist_find(key: str):
    """Look again for the company's careers page and board (the first look failed, or found none)."""
    from backend.core import watchlist
    if key not in watchlist.watched_keys():
        return jsonify(ok=False, error="That company is not on your watch list."), 404
    threading.Thread(target=watchlist.find_board, args=(cfg(), key), daemon=True).start()
    return jsonify(ok=True)


@app.get("/watchlist/names")
def watchlist_names():
    """Company names to pick from while typing: the boards the fetch reads, best match first."""
    from data_engineering.db.models import Company
    from data_engineering.db.session import get_session
    q = " ".join(str(request.args.get("q") or "").split()).lower()
    if len(q) < 2:
        return jsonify(ok=True, names=[])
    cfg()
    with get_session() as session:
        rows = (session.query(Company.name).filter(Company.active.is_(True), Company.name.ilike(f"%{q}%"))
                .distinct().limit(200).all())
    names = sorted({r[0] for r in rows if r[0]}, key=lambda n: (not n.lower().startswith(q), len(n), n.lower()))
    return jsonify(ok=True, names=names[:12])


@app.post("/job/<int:job_id>/watch")
def job_watch(job_id: int):
    """Save the job to the watch list, or take it off."""
    from backend.core import watchlist
    on = bool((request.get_json(silent=True) or {}).get("on", True))
    return jsonify(ok=True, on=watchlist.star(job_id, on))
