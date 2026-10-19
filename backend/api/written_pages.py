"""Your written answers on the Questions page: read, copy, or let go of one."""

from __future__ import annotations

from flask import jsonify

from backend.api.app import app


@app.get("/apply/written.json")
def written_list():
    from backend.apply import written
    items = sorted(written.load(), key=lambda i: i.get("date", ""), reverse=True)
    return jsonify(ok=True, items=items)


@app.post("/apply/written/<entry_id>/delete")
def written_delete(entry_id: str):
    from backend.apply import written
    return jsonify(ok=written.remove(entry_id))
