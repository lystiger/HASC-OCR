"""The AI page for bosses and admins (P149): reads, time, accuracy, daily use, cost in VND, budget alert.

Accuracy comes from people's corrections (criteria 33-36): each value_change event keeps where the old
value came from and its flag ("ai|doubt"). Over reviewed sheets:
- AI values: values the AI wrote itself (not copied down), including ones a person corrected
- corrected: AI values a person changed
- accuracy = 1 - corrected / AI values; flag precision = flagged and corrected / flagged
- false alarms = flagged but not corrected; missed = corrected but not flagged
"""
from datetime import date, datetime, timedelta, timezone
import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from hasc.db import get_connection
from hasc.web import security
from hasc.web.auth import get_db
from hasc.web.errors import AppError
from hasc.web.roles import require_role

router = APIRouter()

VN = timezone(timedelta(hours=7))
DEFAULT_RATE = 26000  # VND per USD until an admin sets one
ALERT_AT = 0.8
HEATMAP_WEEKS = 26
# Days are counted in Vietnam time; stored times are UTC ISO text.
LOCAL_DAY = "date(substr({col}, 1, 19), '+7 hours')"


def get_settings(conn: sqlite3.Connection) -> dict:
    rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM app_settings")}
    return {"budget_vnd": int(rows.get("ai_budget_vnd", 0)), "rate": int(rows.get("usd_vnd_rate", DEFAULT_RATE))}


def month_window(today: date) -> tuple[str, str]:
    """The Vietnam calendar month holding `today`, as UTC ISO bounds [start, end)."""
    start = datetime(today.year, today.month, 1, tzinfo=VN)
    end = datetime(today.year + (today.month == 12), today.month % 12 + 1, 1, tzinfo=VN)
    return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


def month_cost_usd(conn: sqlite3.Connection, today: date) -> float:
    start, end = month_window(today)
    row = conn.execute("""
        SELECT COALESCE(SUM(cost_usd), 0) AS usd FROM read_jobs
        WHERE COALESCE(finished_at, started_at) >= ? AND COALESCE(finished_at, started_at) < ?
    """, (start, end)).fetchone()
    return float(row["usd"])


def budget_status(conn: sqlite3.Connection, today: date) -> dict:
    settings = get_settings(conn)
    spent_vnd = round(month_cost_usd(conn, today) * settings["rate"])
    budget = settings["budget_vnd"]
    share = spent_vnd / budget if budget else 0.0
    level = "over" if budget and share >= 1 else "near" if budget and share >= ALERT_AT else None
    return {"spent_vnd": spent_vnd, "budget_vnd": budget, "share": share, "level": level, "rate": settings["rate"]}


def accuracy(conn: sqlite3.Connection, start_day: str, end_day: str) -> dict:
    """Counts over reviewed sheets produced in [start_day, end_day)."""
    sheets = "SELECT id FROM sheets WHERE status = 'reviewed' AND production_date >= ? AND production_date < ?"
    live = f"""
        SELECT c.flag FROM cells c JOIN rows r ON r.id = c.row_id
        WHERE r.invalid = 0 AND r.sheet_id IN ({sheets}) AND c.source = 'ai' AND c.inherited = 0
    """
    # The first correction of each value says what the AI had written and whether it was flagged.
    fixed = f"""
        SELECT e.detail FROM events e
        WHERE e.action = 'value_change' AND e.detail LIKE 'ai|%' AND e.sheet_id IN ({sheets})
          AND e.id = (SELECT MIN(id) FROM events f WHERE f.action = 'value_change'
                      AND f.row_id = e.row_id AND f.field = e.field)
    """
    kept = conn.execute(f"SELECT COUNT(*) AS n, SUM(flag != 'none') AS flagged FROM ({live})",
                        (start_day, end_day)).fetchone()
    corrected = conn.execute(f"SELECT COUNT(*) AS n, SUM(detail != 'ai|none') AS flagged FROM ({fixed})",
                             (start_day, end_day)).fetchone()
    reviewed = conn.execute(f"SELECT COUNT(*) AS n FROM ({sheets})", (start_day, end_day)).fetchone()["n"]
    n_kept, n_fixed = kept["n"] or 0, corrected["n"] or 0
    flagged_kept, flagged_fixed = kept["flagged"] or 0, corrected["flagged"] or 0
    total = n_kept + n_fixed
    flagged = flagged_kept + flagged_fixed
    return {
        "sheets": reviewed, "values": total, "corrected": n_fixed, "flagged": flagged,
        "accuracy": 1 - n_fixed / total if total else None,
        "precision": flagged_fixed / flagged if flagged else None,
        "false_alarms": flagged_kept, "missed": n_fixed - flagged_fixed,
        "missed_share": (n_fixed - flagged_fixed) / total if total else None,
    }


def daily(conn: sqlite3.Connection, first_day: date, last_day: date) -> dict[str, dict]:
    """{YYYY-MM-DD: {reads, failed, cost_usd, read_s}} per Vietnam day, for days with any read."""
    day = LOCAL_DAY.format(col="COALESCE(finished_at, started_at)")
    rows = conn.execute(f"""
        SELECT {day} AS day,
               SUM(status = 'done') AS reads, SUM(status = 'failed') AS failed,
               COALESCE(SUM(cost_usd), 0) AS cost_usd,
               AVG(CASE WHEN status = 'done' THEN (julianday(finished_at) - julianday(started_at)) * 86400 END) AS read_s
        FROM read_jobs
        WHERE COALESCE(finished_at, started_at) IS NOT NULL AND {day} BETWEEN ? AND ?
        GROUP BY day
    """, (first_day.isoformat(), last_day.isoformat())).fetchall()
    return {r["day"]: dict(r) for r in rows}


def heatmap(per_day: dict[str, dict], today: date, rate: int) -> list[list[dict]]:
    """Weeks (Monday first) of {day, reads, cost_vnd, level 0-4}, ending with this week."""
    start = today - timedelta(days=today.weekday() + 7 * (HEATMAP_WEEKS - 1))
    weeks = []
    for w in range(HEATMAP_WEEKS):
        week = []
        for d in range(7):
            day = start + timedelta(days=7 * w + d)
            info = per_day.get(day.isoformat(), {})
            reads = int(info.get("reads") or 0) + int(info.get("failed") or 0)
            level = 0 if not reads else 1 if reads <= 2 else 2 if reads <= 5 else 3 if reads <= 9 else 4
            week.append({"day": day, "reads": reads, "future": day > today, "level": level,
                         "cost_vnd": round(float(info.get("cost_usd") or 0) * rate)})
        weeks.append(week)
    return weeks


def _day_row(day: date, info: dict, rate: int) -> dict:
    return {"day": day, "reads": int(info.get("reads") or 0), "failed": int(info.get("failed") or 0),
            "read_s": info.get("read_s"), "cost_vnd": round(float(info.get("cost_usd") or 0) * rate)}


def ai_overview(conn: sqlite3.Connection, today: date) -> dict:
    start, end = month_window(today)
    month = conn.execute("""
        SELECT SUM(status = 'done') AS reads, SUM(status = 'failed') AS failed,
               SUM(status IN ('queued', 'running', 'retry_wait')) AS waiting,
               COALESCE(SUM(cost_usd), 0) AS cost_usd,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               AVG(CASE WHEN status = 'done' THEN (julianday(finished_at) - julianday(started_at)) * 86400 END) AS read_s,
               AVG(CASE WHEN status = 'done' THEN (julianday(finished_at) - julianday(queued_at)) * 86400 END) AS total_s
        FROM read_jobs
        WHERE COALESCE(finished_at, started_at, queued_at) >= ? AND COALESCE(finished_at, started_at, queued_at) < ?
    """, (start, end)).fetchone()
    budget = budget_status(conn, today)
    first = today.replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    heat_start = today - timedelta(days=today.weekday() + 7 * (HEATMAP_WEEKS - 1))
    per_day = daily(conn, heat_start, today)
    reads = int(month["reads"] or 0)
    cost_vnd = round(float(month["cost_usd"]) * budget["rate"])
    recent = [(today - timedelta(days=i)) for i in range(14)]
    return {
        "today": today, "month_label": f"{today.month:02d}/{today.year}",
        "reads": reads, "failed": int(month["failed"] or 0), "waiting": int(month["waiting"] or 0),
        "read_s": month["read_s"], "total_s": month["total_s"],
        "tokens": int(month["prompt_tokens"]) + int(month["completion_tokens"]),
        "cost_vnd": cost_vnd, "cost_per_read_vnd": round(cost_vnd / reads) if reads else None,
        "budget": budget,
        "quality": accuracy(conn, first.isoformat(), next_month.isoformat()),
        "weeks": heatmap(per_day, today, budget["rate"]),
        "recent": [_day_row(d, per_day.get(d.isoformat(), {}), budget["rate"]) for d in recent],
    }


def vnd(value) -> str:
    """12345678 -> "12.345.678 ₫" (Vietnamese grouping)."""
    if value is None:
        return "—"
    return f"{int(round(value)):,}".replace(",", ".") + " ₫"


def budget_alert(request: Request) -> dict | None:
    """For the office frame: the month's budget level, or None. Never breaks a page."""
    try:
        conn = get_connection(request.app.state.db_path, auto_migrate=False)
        try:
            status = budget_status(conn, datetime.now(VN).date())
        finally:
            conn.close()
    except Exception:
        return None
    return status if status["level"] else None


def _render(request, conn, user, name, **extra):
    return request.app.state.templates.TemplateResponse(
        request=request, name=name,
        context={"user": user, "ai": ai_overview(conn, datetime.now(VN).date()),
                 "settings": get_settings(conn), **extra})


@router.get("/ai", response_class=HTMLResponse)
def ai_page(request: Request, user: dict = Depends(require_role("view_ai")),
            conn: sqlite3.Connection = Depends(get_db)):
    return _render(request, conn, user, "ai.html", saved=request.query_params.get("saved") == "1")


@router.get("/ai/partial", response_class=HTMLResponse)
def ai_partial(request: Request, user: dict = Depends(require_role("view_ai")),
               conn: sqlite3.Connection = Depends(get_db)):
    return _render(request, conn, user, "ai_stats_partial.html")


@router.get("/api/ai/stats")
def api_ai_stats(user: dict = Depends(require_role("view_ai")), conn: sqlite3.Connection = Depends(get_db)):
    ai = ai_overview(conn, datetime.now(VN).date())
    ai.pop("weeks")
    for day in ai["recent"]:
        day["day"] = day["day"].isoformat()
    ai["today"] = ai["today"].isoformat()
    return ai


@router.post("/ai/settings")
def ai_settings(budget_vnd: int = Form(...), rate: int = Form(...),
                user: dict = Depends(require_role("manage_ai_budget")),
                conn: sqlite3.Connection = Depends(get_db)):
    if budget_vnd < 0 or not 1000 <= rate <= 1_000_000:
        raise AppError(code="invalid_setting", message="Ngân sách hoặc tỷ giá không hợp lệ", status_code=400)
    now = security.now().isoformat()
    with conn:
        for key, value in (("ai_budget_vnd", budget_vnd), ("usd_vnd_rate", rate)):
            old = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            conn.execute("""
                INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
            """, (key, str(value), user["id"], now))
            if not old or old["value"] != str(value):
                conn.execute("""
                    INSERT INTO events (at, user_id, action, field, old_value, new_value)
                    VALUES (?, ?, 'setting_change', ?, ?, ?)
                """, (now, user["id"], key, old["value"] if old else None, str(value)))
    return RedirectResponse(url="/ai?saved=1", status_code=303)
