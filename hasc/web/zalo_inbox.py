"""Zalo Bot section of the AI page: receiver health, the inbox, and pilot usage kept apart from staging.

The receiver (python -m hasc.zalo) writes zalo_receiver and zalo_inbox; this page only reads them.
A quiet day is not a failure: health comes from the receiver's own heartbeat, and a quiet day
shows zero photos. Workers send photos to the bot in a private chat; the staging card shows whether
a test photo made it end to end. The bot sends nothing, so outgoing messages are always 0.
Signed photo URLs and the token never reach a page.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, RedirectResponse

from hasc.db import create_event
from hasc.web import security
from hasc.web.ai_stats import LOCAL_DAY, month_window
from hasc.web.auth import get_db
from hasc.web.errors import AppError
from hasc.web.roles import require_role

router = APIRouter()

STALE_SECONDS = 120  # a healthy receiver beats at least every ~40 s (30 s long poll + timeout margin)
RECENT = 20
COUNTS = """
    COUNT(*) AS photo_events, COALESCE(SUM(redeliveries), 0) AS redeliveries,
    COALESCE(SUM(status = 'delivered'), 0) AS delivered, COALESCE(SUM(status = 'duplicate'), 0) AS duplicate,
    COALESCE(SUM(status = 'rejected'), 0) AS rejected, COALESCE(SUM(status = 'failed'), 0) AS failed,
    COALESCE(SUM(status = 'received'), 0) AS waiting
"""


def receiver_health(conn: sqlite3.Connection, now: datetime) -> dict:
    """never | stale | error | ok, with the receiver row. Says nothing about photos Zalo never sent."""
    row = conn.execute("SELECT * FROM zalo_receiver WHERE id = 1").fetchone()
    if not row or not row["heartbeat_at"]:
        return {"state": "never", "row": None}
    beat = datetime.fromisoformat(row["heartbeat_at"])
    if (now - beat).total_seconds() > STALE_SECONDS:
        state = "stale"
    elif row["last_error_at"] and (not row["last_ok_at"] or row["last_error_at"] > row["last_ok_at"]):
        state = "error"
    else:
        state = "ok"
    return {"state": state, "row": dict(row)}


def _counts(row) -> dict:
    counts = {k: int(row[k] or 0) for k in row.keys() if k not in ("day", "staging")}
    counts["events"] = counts["photo_events"] + counts["redeliveries"]  # every image event Zalo delivered
    return counts


def usage(conn: sqlite3.Connection, today: date) -> dict:
    """Month-to-date and per-day counts for this Vietnam month; pilot excludes the staging group."""
    start, end = month_window(today)
    day = LOCAL_DAY.format(col="received_at")
    rows = conn.execute(f"""
        SELECT {day} AS day, staging, {COUNTS} FROM zalo_inbox
        WHERE received_at >= ? AND received_at < ? GROUP BY day, staging
    """, (start, end)).fetchall()
    per_day = {(r["day"], r["staging"]): _counts(r) for r in rows}
    empty = dict.fromkeys(("events", "photo_events", "redeliveries", "delivered", "duplicate", "rejected",
                           "failed", "waiting"), 0)
    days = []
    for n in range(today.day, 0, -1):  # newest first; a day with no photo shows zeros, not a gap
        d = today.replace(day=n)
        days.append({"day": d, "pilot": per_day.get((d.isoformat(), 0), empty),
                     "staging": per_day.get((d.isoformat(), 1), empty)})
    month = {}
    for kind, flag in (("pilot", 0), ("staging", 1)):
        total = conn.execute(f"SELECT {COUNTS} FROM zalo_inbox WHERE staging = ? AND received_at >= ? AND received_at < ?",
                             (flag, start, end)).fetchone()
        month[kind] = _counts(total)
    return {"month": month, "days": days}


def zalo_overview(conn: sqlite3.Connection, now: datetime) -> dict:
    today = now.astimezone(timezone(timedelta(hours=7))).date()
    test = conn.execute("""
        SELECT COUNT(*) AS saved, MAX(delivered_at) AS last_at
        FROM zalo_inbox WHERE staging = 1 AND status IN ('delivered', 'duplicate')
    """).fetchone()
    last = conn.execute("SELECT MAX(received_at) AS at FROM zalo_inbox").fetchone()["at"]
    recent = conn.execute(f"""
        SELECT id, chat_type, sender_id, chat_id, sender_name, staging, command, received_at, status, attempts, last_error,
               path IS NOT NULL AS has_photo
        FROM zalo_inbox ORDER BY id DESC LIMIT {RECENT}
    """).fetchall()
    return {
        "health": receiver_health(conn, now), "last_photo_at": last,
        "staging_test": {"saved": test["saved"], "last_at": test["last_at"]},
        "usage": usage(conn, today), "recent": [dict(r) for r in recent],
        "outgoing": 0,  # no send call exists in hasc.zalo
    }


@router.get("/ai/zalo/{item_id}/photo")
def zalo_photo(item_id: int, user: dict = Depends(require_role("view_ai")),
               conn: sqlite3.Connection = Depends(get_db)):
    item = conn.execute("SELECT path FROM zalo_inbox WHERE id = ?", (item_id,)).fetchone()
    if not item or not item["path"] or not Path(item["path"]).is_file():
        raise AppError("not_found", "Không tìm thấy ảnh", 404)
    return FileResponse(item["path"])


@router.post("/ai/zalo/{item_id}/retry")
def zalo_retry(item_id: int, user: dict = Depends(require_role("manage_zalo")),
               conn: sqlite3.Connection = Depends(get_db)):
    """Put a failed download back in the queue; the receiver tries it again on its next poll."""
    with conn:
        done = conn.execute("""
            UPDATE zalo_inbox SET status = 'received', attempts = 0, last_error = NULL
            WHERE id = ? AND status = 'failed'
        """, (item_id,)).rowcount
    if not done:
        raise AppError("not_failed", "Ảnh này không ở trạng thái lỗi", 409)
    create_event(conn, user["id"], "zalo_retry", detail=f"zalo_inbox {item_id}", at=security.now().isoformat())
    return RedirectResponse(url="/ai", status_code=303)
