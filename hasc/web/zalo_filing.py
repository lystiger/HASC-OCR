"""Waiting Zalo photos and one-step filing (zalo-intake spec R8).

The gate holds a photo it cannot file safely, with a Vietnamese reason. Here an employee or admin looks at the
photo and files it as a front (picking date and shift) or as a back (picking the paper). The filing rules are
hasc.intake's; this page only collects the person's choice. Filing makes no gate call and sends no message.
"""
from datetime import date, datetime, timezone
import sqlite3
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from hasc import intake
from hasc.db import get_default_photos_dir
from hasc.web.auth import get_db
from hasc.web.errors import AppError
from hasc.web.roles import require_role
from hasc.zalo.receiver import mask

router = APIRouter()

PAGE_SIZE = 50
WAITING = ("waiting", "not_form")
WHERE = "staging = 0 AND status IN ('waiting', 'not_form')"
FILED_MESSAGES = {
    "new": "Đã xếp thành biểu mẫu mới.",
    "resend": "Ảnh này là bản gửi lại, đã gắn vào biểu mẫu có sẵn.",
    "back": "Đã xếp thành mặt sau.",
    "noop": "Ảnh này đã được xếp trước đó.",
}


def waiting_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM zalo_inbox WHERE staging = 0 AND status = 'waiting'").fetchone()[0]


def _photos_dir(request: Request):
    return getattr(request.app.state, "photos_dir", None) or get_default_photos_dir(
        db_path=getattr(request.app.state, "db_path", None))


def _row_view(row, photos_dir) -> dict:
    sent = intake.send_time(row)
    day, shift = intake.shift_of(sent)
    return {
        "id": row["id"],
        "status": row["status"],
        "reason": row["reason"] or "chưa rõ lý do",
        "sender": f"{row['sender_name'] or 'Không rõ tên'} {mask(row['sender_id'])}",
        "sent_at": sent.isoformat(),
        "has_photo": intake.inbox_file(row, photos_dir) is not None,
        "suggest_date": day.isoformat(),
        "suggest_shift": shift,
    }


@router.get("/zalo/cho-xep", response_class=HTMLResponse)
def waiting_page(
    request: Request,
    page: int = 1,
    done: Optional[str] = None,
    error: Optional[str] = None,
    user: dict = Depends(require_role("file_zalo_photo")),
    conn: sqlite3.Connection = Depends(get_db),
):
    page = max(page, 1)
    total = conn.execute(f"SELECT COUNT(*) FROM zalo_inbox WHERE {WHERE}").fetchone()[0]
    rows = conn.execute(f"""
        SELECT * FROM zalo_inbox WHERE {WHERE}
        ORDER BY COALESCE(sent_at, received_at), id LIMIT ? OFFSET ?
    """, (PAGE_SIZE, (page - 1) * PAGE_SIZE)).fetchall()
    photos_dir = _photos_dir(request)
    return request.app.state.templates.TemplateResponse(
        request=request, name="zalo_waiting.html",
        context={"user": user, "rows": [_row_view(r, photos_dir) for r in rows], "total": total, "page": page,
                 "pages": max(1, -(-total // PAGE_SIZE)), "done": done, "error": error[:300] if error else None})


@router.get("/zalo/cho-xep/{item_id}/photo")
def waiting_photo(
    item_id: int,
    request: Request,
    user: dict = Depends(require_role("file_zalo_photo")),
    conn: sqlite3.Connection = Depends(get_db),
):
    row = conn.execute(f"SELECT * FROM zalo_inbox WHERE id = ? AND {WHERE}", (item_id,)).fetchone()
    path = intake.inbox_file(row, _photos_dir(request)) if row else None
    if path is None:
        raise AppError("not_found", "Không tìm thấy ảnh", 404)
    return FileResponse(path)


def _back(page: int, **message) -> RedirectResponse:
    query = "&".join(f"{k}={quote(str(v))}" for k, v in {"page": page, **message}.items())
    return RedirectResponse(url=f"/zalo/cho-xep?{query}", status_code=303)


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise intake.Refused("ngày không hợp lệ") from None


@router.post("/zalo/cho-xep/{item_id}/mat-truoc")
def file_front(
    item_id: int,
    request: Request,
    production_date: str = Form(...),
    shift: int = Form(...),
    page: int = Form(1),
    user: dict = Depends(require_role("file_zalo_photo")),
    conn: sqlite3.Connection = Depends(get_db),
):
    return _file(conn, request, item_id, page, lambda: intake.file_front_by_person(
        conn, item_id, _day(production_date), shift, user["id"], _photos_dir(request), datetime.now(timezone.utc)))


@router.post("/zalo/cho-xep/{item_id}/mat-sau")
def file_back(
    item_id: int,
    request: Request,
    production_date: str = Form(...),
    shift: int = Form(...),
    paper_no: int = Form(...),
    page: int = Form(1),
    user: dict = Depends(require_role("file_zalo_photo")),
    conn: sqlite3.Connection = Depends(get_db),
):
    return _file(conn, request, item_id, page, lambda: intake.file_back_by_person(
        conn, item_id, _day(production_date), shift, paper_no, user["id"], _photos_dir(request),
        datetime.now(timezone.utc)))


def _file(conn, request, item_id, page, do):
    row = conn.execute("SELECT staging, status FROM zalo_inbox WHERE id = ?", (item_id,)).fetchone()
    if row is None or row["staging"]:
        raise AppError("not_found", "Không tìm thấy ảnh", 404)
    try:
        outcome = do()
    except intake.Refused as refused:
        return _back(page, error=str(refused))
    return _back(page, done=FILED_MESSAGES[outcome])
