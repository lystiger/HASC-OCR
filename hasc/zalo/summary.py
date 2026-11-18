"""Daily summary (P151): at 9:00 Vietnam time the bot posts yesterday's numbers to the office group.

Yesterday is the production date that ended at 7am today: Ca 1 (7am-7pm) and Ca 2 (7pm-7am), both
finished. The numbers are the daily report's (`hasc.excel.daily_report`): kg and rolls per shift and
machine. Sheets the office has not confirmed yet are sent anyway, marked `chưa xác nhận`.

A day is sent at most once. Its row is written as `sending` before the call, so a crash, a timeout or
a reply cut off mid-send leaves it `failed` for a person to check, never sent twice. Only a failure
known not to have delivered (no DNS, connection refused, or Zalo answered with an error) waits RETRY_MINUTES and tries again, up to MAX_ATTEMPTS. A PC that is off at 9:00 sends
when it is back, until 23:59; after that the day is skipped and the next morning reports the next day.
"""
from datetime import date, datetime, time, timedelta, timezone

from hasc.db import list_not_running, list_sheets
from hasc.excel.daily_report import MACHINES, SHIFTS, _collect
from hasc.web.operator import ALLOWED_MACHINES
from hasc.zalo.receiver import ZaloError, ZaloUncertain, call

VN = timezone(timedelta(hours=7))  # Vietnam has no daylight saving
SEND_AT = time(9, 0)
MAX_ATTEMPTS = 3
RETRY_MINUTES = 10


def _kg(value: float) -> str:
    return f"{round(value, 1):g}".replace(".", ",")


def summary_text(conn, production_date: date) -> str:
    day = production_date.strftime("%d/%m/%Y")
    iso = production_date.isoformat()
    sheets = [s for s in list_sheets(conn, production_date=iso) if s["machine"] in MACHINES]
    if not sheets:
        return f"[HASC] Không có số liệu ngày {day}."
    lines, _ = _collect(conn, iso)
    text = [f"[HASC] Số liệu ngày {day} (Ca 1 + Ca 2)"]
    film_kg = bag_kg = 0.0
    rolls = 0
    for shift in SHIFTS:
        text.append(f"Ca {shift}:")
        shown = False
        for machine in MACHINES:
            section = lines.get((shift, machine))
            if not section:
                continue
            film = sum(line["net"] for key, line in section.items() if key[0] != "back")
            bag = sum(line["net"] for key, line in section.items() if key[0] == "back")
            count = sum(line["rolls"] for line in section.values())
            film_kg, bag_kg, rolls = film_kg + film, bag_kg + bag, rolls + count
            parts = [f"{_kg(film)} kg"] if any(key[0] != "back" for key in section) else []
            if any(key[0] == "back" for key in section):
                parts.append(f"túi {_kg(bag)} kg")
            parts.append(f"{count} cuộn")
            if any(line["unconfirmed"] for line in section.values()):
                parts.append("chưa xác nhận")
            if any(line["unread"] for line in section.values()):
                parts.append("có số chưa đọc được")
            text.append(f"- {machine}: " + ", ".join(parts))
            shown = True
        if not shown:
            text.append("- Không có số liệu")
    total = f"Tổng: {_kg(film_kg)} kg màng, {rolls} cuộn"
    text.append(total + (f"; túi {_kg(bag_kg)} kg" if bag_kg else ""))
    # Only a form A page counts as the shift's sheet; a back page alone still leaves form A missing.
    received = {(s["machine"], s["shift"]) for s in sheets if (s["layout"] or "form_a") == "form_a"}
    idle = {(r["machine"], r["shift"]) for r in list_not_running(conn, iso)}
    missing = [f"{m} Ca {s}" for s in SHIFTS for m in ALLOWED_MACHINES if (m, s) not in received | idle]
    if missing:
        text.append("Chưa nhận tờ: " + ", ".join(missing))
    pending = sum(s["status"] != "reviewed" for s in sheets)
    if pending:
        text.append(f"Chưa xác nhận: {pending} tờ")
    return "\n".join(text)


def due_date(now: datetime) -> date | None:
    """The production date to send now, or None before 9:00 Vietnam time."""
    local = now.astimezone(VN)
    if local.time() < SEND_AT:
        return None
    return local.date() - timedelta(days=1)


def send_due_summary(conn, token, chat_id, now: datetime, call=call) -> str | None:
    """Send yesterday's summary if it is due and not yet sent. Returns the new status, or None."""
    production_date = due_date(now)
    if production_date is None:
        return None
    iso = production_date.isoformat()
    row = conn.execute("SELECT * FROM zalo_summary WHERE production_date = ?", (iso,)).fetchone()
    if row and row["status"] in ("sent", "failed"):
        return None
    if row and row["status"] == "sending":  # an earlier run stopped mid-send: it may have arrived
        _save(conn, iso, "failed", row["attempts"], "dừng giữa lúc gửi; xem nhóm Zalo xem tin đã đến chưa")
        return "failed"
    if row and now - datetime.fromisoformat(row["last_try_at"]) < timedelta(minutes=RETRY_MINUTES):
        return None
    attempts = (row["attempts"] if row else 0) + 1
    stamp = now.astimezone(timezone.utc).isoformat()
    try:
        text = summary_text(conn, production_date)
    except Exception as error:  # e.g. a database error: nothing was sent, so it may try again
        _record(conn, iso, chat_id, "failed" if attempts >= MAX_ATTEMPTS else "waiting", attempts, stamp,
                f"lỗi nội bộ: {type(error).__name__}")
        return "failed" if attempts >= MAX_ATTEMPTS else "waiting"
    _record(conn, iso, chat_id, "sending", attempts, stamp, None)
    try:
        call(token, "sendMessage", {"chat_id": chat_id, "text": text})
    except ZaloUncertain as error:  # the message may have gone out; never risk a second one
        _save(conn, iso, "failed", attempts, f"{error}; có thể tin đã đến, xem nhóm Zalo")
        return "failed"
    except ZaloError as error:
        status = "failed" if attempts >= MAX_ATTEMPTS else "waiting"
        _save(conn, iso, status, attempts, str(error))
        return status
    _save(conn, iso, "sent", attempts, None, sent_at=stamp)
    return "sent"


def _record(conn, iso, chat_id, status, attempts, stamp, error):
    with conn:
        conn.execute("""
            INSERT INTO zalo_summary (production_date, chat_id, status, attempts, last_try_at, last_error)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (production_date) DO UPDATE SET status = excluded.status, attempts = excluded.attempts,
                last_try_at = excluded.last_try_at, last_error = excluded.last_error
        """, (iso, chat_id, status, attempts, stamp, error))


def _save(conn, iso, status, attempts, error, sent_at=None):
    with conn:
        conn.execute("""
            UPDATE zalo_summary SET status = ?, attempts = ?, last_error = ?, sent_at = COALESCE(?, sent_at)
            WHERE production_date = ?
        """, (status, attempts, error, sent_at, iso))
