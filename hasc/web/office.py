from datetime import date, datetime, timedelta, timezone
import hashlib
import io
from pathlib import Path
import sqlite3
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from hasc.db import (
    create_event,
    get_default_photos_dir,
    get_photo_by_id,
    get_photos_for_sheet,
    get_sheet_by_id,
    get_sheet_data,
    get_user_by_id,
    update_photo_small_copy_path,
    update_sheet_status,
)
from hasc.excel import build_page_workbook, page_filename
from hasc.images import display_copy
from hasc.web.auth import get_active_user, get_db
from hasc.web.errors import AppError
from hasc.web.roles import ROLE_PERMISSIONS, require_role

router = APIRouter()

STATUS_LABELS_VI = {
    "received": "Đã nhận",
    "reading": "Đang đọc",
    "needs_review": "Cần kiểm tra",
    "in_review": "Đang kiểm tra",
    "reviewed": "Đã kiểm tra",
    "failed": "Lỗi",
}

DEFAULT_STATUSES = ("needs_review", "in_review", "failed")


def parse_iso_datetime(dt_str: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime string, ensuring timezone-aware UTC datetime."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def is_sheet_overdue(
    status: str, status_since: str | None, now_dt: datetime | None = None
) -> bool:
    """A sheet is overdue 24 hours after entering Needs review or In review,
    weekends included (spec section 4 / acceptance criterion 45).
    """
    if status not in ("needs_review", "in_review"):
        return False
    dt = parse_iso_datetime(status_since)
    if not dt:
        return False
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    return (now_dt - dt).total_seconds() >= 24 * 3600


def format_time_waited(status_since: str | None, now_dt: datetime | None = None) -> str:
    """Format the waiting time into human-readable Vietnamese text."""
    if not status_since:
        return "-"
    dt = parse_iso_datetime(status_since)
    if not dt:
        return "-"
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    seconds = max(0, int((now_dt - dt).total_seconds()))
    if seconds < 60:
        return "Vừa xong"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} phút"
    hours = minutes // 60
    rem_minutes = minutes % 60
    if hours < 24:
        if rem_minutes > 0:
            return f"{hours} giờ {rem_minutes} phút"
        return f"{hours} giờ"
    days = hours // 24
    rem_hours = hours % 24
    if rem_hours > 0:
        return f"{days} ngày {rem_hours} giờ"
    return f"{days} ngày"


def get_office_sheets(
    conn: sqlite3.Connection,
    machine: str | None = None,
    production_date: str | None = None,
    shift: int | None = None,
    status: str | None = None,
    overdue_filter: bool | None = None,
    include_all_statuses: bool = False,
    now_dt: datetime | None = None,
) -> list[dict]:
    """Query sheets for office screens.
    Default view shows Needs review, In review and Failed sheets, overdue first.
    Includes machine, date, shift, page, status, uploading shift leader and time waited.
    """
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)

    query = """
        SELECT s.*,
               p.uploaded_by AS uploader_id,
               u.username AS uploader_username,
               p.uploaded_at AS first_uploaded_at,
               pu.username AS picked_up_by_username
        FROM sheets s
        LEFT JOIN (
            SELECT sheet_id, uploaded_by, uploaded_at
            FROM photos
            WHERE id IN (SELECT MIN(id) FROM photos GROUP BY sheet_id)
        ) p ON s.id = p.sheet_id
        LEFT JOIN users u ON p.uploaded_by = u.id
        LEFT JOIN users pu ON s.picked_up_by = pu.id
    """
    clauses: list[str] = []
    params: list[Any] = []

    if machine:
        clauses.append("s.machine = ?")
        params.append(machine)
    if production_date:
        clauses.append("s.production_date = ?")
        params.append(production_date)
    if shift is not None:
        clauses.append("s.shift = ?")
        params.append(shift)

    if status:
        if status != "all":
            clauses.append("s.status = ?")
            params.append(status)
    elif not include_all_statuses:
        clauses.append("s.status IN ('needs_review', 'in_review', 'failed')")

    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    cursor = conn.execute(query, params)
    rows = cursor.fetchall()

    results = []
    for r in rows:
        status_str = r["status"]
        status_since = r["status_since"] or r["first_uploaded_at"]
        overdue = is_sheet_overdue(status_str, status_since, now_dt)

        if overdue_filter is not None and overdue != overdue_filter:
            continue

        dt = parse_iso_datetime(status_since)
        wait_sec = (now_dt - dt).total_seconds() if dt else 0.0

        results.append({
            "id": r["id"],
            "form": r["form"],
            "machine": r["machine"],
            "production_date": r["production_date"],
            "shift": r["shift"],
            "page_no": r["page_no"],
            "status": status_str,
            "status_label": STATUS_LABELS_VI.get(status_str, status_str),
            "version": r["version"],
            "picked_up_by": r["picked_up_by"],
            "picked_up_by_username": r["picked_up_by_username"],
            "status_since": status_since,
            "uploader_id": r["uploader_id"],
            "uploader_username": r["uploader_username"],
            "is_overdue": overdue,
            "time_waited": format_time_waited(status_since, now_dt),
            "wait_seconds": wait_sec,
        })

    # Sort overdue first, then wait time longest first, then date/shift/page
    results.sort(
        key=lambda s: (
            0 if s["is_overdue"] else 1,
            -s["wait_seconds"],
            s["production_date"],
            s["shift"],
            s["page_no"],
        )
    )
    return results


def _shift_filter(shift: str | None) -> int | None:
    """An empty shift from the live-refresh URL means no filter; anything else must be a whole number."""
    if not shift:
        return None
    try:
        return int(shift)
    except ValueError:
        raise AppError("validation_error", "Dữ liệu không hợp lệ", 422) from None


class PickupRequest(BaseModel):
    takeover: bool = False


class ConfirmRequest(BaseModel):
    version: int


# -----------------------------------------------------------------------------
# HTML Endpoints & HTMX Live Refresh
# -----------------------------------------------------------------------------


def render_office_screen(
    request: Request,
    user: dict,
    conn: sqlite3.Connection,
    machine: str | None = None,
    date: str | None = None,
    shift: int | None = None,
    status: str | None = None,
) -> HTMLResponse:
    sheets = get_office_sheets(
        conn=conn,
        machine=machine,
        production_date=date,
        shift=shift,
        status=status,
        include_all_statuses=(status is not None),
    )
    templates = request.app.state.templates
    yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
    return templates.TemplateResponse(
        request=request,
        name="office.html",
        context={
            "user": user,
            "sheets": sheets,
            "yesterday": yesterday,
            "filters": {
                "machine": machine or "",
                "date": date or "",
                "shift": shift if shift is not None else "",
                "status": status or "",
            },
        },
    )


@router.get("/office", response_class=HTMLResponse)
@router.get("/office/sheets", response_class=HTMLResponse)
def office_sheet_list_page(
    request: Request,
    machine: Optional[str] = None,
    date: Optional[str] = None,
    shift: Optional[str] = None,
    status: Optional[str] = None,
    user: dict = Depends(require_role("view_all_sheets")),
    conn: sqlite3.Connection = Depends(get_db),
):
    return render_office_screen(
        request=request,
        user=user,
        conn=conn,
        machine=machine or None,
        date=date or None,
        shift=_shift_filter(shift),
        status=status or None,
    )


@router.get("/office/report")
def office_daily_report_form(
    date: Optional[str] = None,
    user: dict = Depends(require_role("employee", "boss", "admin")),
):
    """The office screen's date form; works without JavaScript (ADR 7)."""
    if not date:
        date = (datetime.now().date() - timedelta(days=1)).isoformat()
    return RedirectResponse(url=f"/api/days/{date}/report", status_code=303)


@router.get("/office/sheets/partial", response_class=HTMLResponse)
@router.get("/office/sheets-partial", response_class=HTMLResponse)
def office_sheets_partial(
    request: Request,
    machine: Optional[str] = None,
    date: Optional[str] = None,
    shift: Optional[str] = None,
    status: Optional[str] = None,
    user: dict = Depends(require_role("view_all_sheets")),
    conn: sqlite3.Connection = Depends(get_db),
):
    status = status or None  # the live-refresh URL sends empty filters
    sheets = get_office_sheets(
        conn=conn,
        machine=machine or None,
        production_date=date or None,
        shift=_shift_filter(shift),
        status=status,
        include_all_statuses=(status is not None),
    )
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="office_sheets_partial.html",
        context={
            "sheets": sheets,
        },
    )


@router.get("/office/sheets/{sheet_id}", response_class=HTMLResponse)
def office_sheet_detail_page(
    sheet_id: int,
    request: Request,
    confirmed: Optional[int] = None,
    pickup_success: Optional[int] = None,
    corrected: Optional[int] = None,
    upload_error: Optional[str] = None,
    user: dict = Depends(require_role("view_sheet_numbers")),
    conn: sqlite3.Connection = Depends(get_db),
):
    sheet = get_sheet_by_id(conn, sheet_id)
    if not sheet:
        raise AppError("not_found", "Không tìm thấy biểu mẫu", 404)

    if user.get("role") == "admin":
        create_event(
            conn=conn,
            user_id=user["id"],
            action="admin_open_sheet",
            sheet_id=sheet_id,
            detail="Admin opened sheet",
        )

    photos = [dict(p) for p in get_photos_for_sheet(conn, sheet_id)]
    photos_dir = getattr(request.app.state, "photos_dir", None)
    if not photos_dir:
        photos_dir = get_default_photos_dir(db_path=getattr(request.app.state, "db_path", None))
    for p in photos:
        display_path = ensure_display_copy(conn, p, photos_dir)
        if display_path:
            p["small_copy_path"] = str(display_path)
    uploader_username = None
    if photos:
        first_uploader_id = photos[0].get("uploaded_by")
        if first_uploader_id:
            uploader_user = get_user_by_id(conn, first_uploader_id)
            if uploader_user:
                uploader_username = uploader_user["username"]

    picked_up_by_username = None
    if sheet["picked_up_by"]:
        pu_user = get_user_by_id(conn, sheet["picked_up_by"])
        if pu_user:
            picked_up_by_username = pu_user["username"]

    status_since = sheet["status_since"]
    now_dt = datetime.now(timezone.utc)
    is_overdue = is_sheet_overdue(sheet["status"], status_since, now_dt)
    time_waited = format_time_waited(status_since, now_dt)
    status_label = STATUS_LABELS_VI.get(sheet["status"], sheet["status"])

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="office_detail.html",
        context={
            "user": user,
            "sheet": sheet,
            "status_label": status_label,
            "is_overdue": is_overdue,
            "time_waited": time_waited,
            "uploader_username": uploader_username,
            "picked_up_by_username": picked_up_by_username,
            "photos": photos,
            "can_review": user["role"] in ROLE_PERMISSIONS["review_sheet"],
            "confirmed": bool(confirmed),
            "pickup_success": bool(pickup_success),
            "corrected": corrected,
            "upload_error": upload_error[:300] if upload_error else None,
            "error": None,
        },
    )


@router.post("/office/sheets/{sheet_id}/pickup")
def office_sheet_pickup_html(
    sheet_id: int,
    takeover: bool = Form(False),
    user: dict = Depends(require_role("review_sheet")),
    conn: sqlite3.Connection = Depends(get_db),
):
    _perform_pickup(conn, sheet_id, user, takeover=takeover)
    return RedirectResponse(
        url=f"/office/sheets/{sheet_id}?pickup_success=1", status_code=303
    )


@router.post("/office/sheets/{sheet_id}/confirm")
def office_sheet_confirm_html(
    sheet_id: int,
    version: int = Form(...),
    user: dict = Depends(require_role("review_sheet")),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        _perform_confirm(conn, sheet_id, user, version=version)
    except AppError as error:
        if error.status_code != 409:
            raise
        # A colleague confirmed or corrected it since this page was opened: back to the sheet with the reason.
        sheet = get_sheet_by_id(conn, sheet_id)
        message = "Biểu mẫu này đã được xác nhận." if sheet["status"] == "reviewed" else error.message
        return RedirectResponse(url=f"/office/sheets/{sheet_id}?upload_error={quote(message)}", status_code=303)
    return RedirectResponse(url=f"/office/sheets/{sheet_id}?confirmed=1", status_code=303)


# -----------------------------------------------------------------------------
# API Endpoints
# -----------------------------------------------------------------------------


@router.get("/api/sheets")
def api_sheets(
    machine: Optional[str] = None,
    date: Optional[str] = None,
    shift: Optional[int] = None,
    status: Optional[str] = None,
    overdue: Optional[bool] = None,
    user: dict = Depends(require_role("view_all_sheets")),
    conn: sqlite3.Connection = Depends(get_db),
):
    """GET /api/sheets?machine=&date=&shift=&status=&overdue=
    Returns [{id, machine, production_date, shift, page_no, status, version, picked_up_by, overdue}]
    """
    sheets = get_office_sheets(
        conn=conn,
        machine=machine,
        production_date=date,
        shift=shift,
        status=status,
        overdue_filter=overdue,
        include_all_statuses=True,
    )
    return [
        {
            "id": s["id"],
            "machine": s["machine"],
            "production_date": s["production_date"],
            "shift": s["shift"],
            "page_no": s["page_no"],
            "status": s["status"],
            "version": s["version"],
            "picked_up_by": s["picked_up_by"],
            "overdue": s["is_overdue"],
        }
        for s in sheets
    ]


@router.get("/api/sheets/{sheet_id}")
def api_sheet_detail(
    sheet_id: int,
    request: Request,
    user: dict = Depends(require_role("view_sheet_numbers")),
    conn: sqlite3.Connection = Depends(get_db),
):
    sheet = get_sheet_by_id(conn, sheet_id)
    if not sheet:
        raise AppError("not_found", "Không tìm thấy biểu mẫu", 404)

    if user.get("role") == "admin":
        create_event(
            conn=conn,
            user_id=user["id"],
            action="admin_open_sheet",
            sheet_id=sheet_id,
            detail="Admin opened sheet",
        )

    photos = [dict(p) for p in get_photos_for_sheet(conn, sheet_id)]
    photos_dir = getattr(request.app.state, "photos_dir", None)
    if not photos_dir:
        photos_dir = get_default_photos_dir(db_path=getattr(request.app.state, "db_path", None))
    for p in photos:
        display_path = ensure_display_copy(conn, p, photos_dir)
        if display_path:
            p["small_copy_path"] = str(display_path)
    rows = get_sheet_data(conn, sheet_id)
    return {
        "id": sheet["id"],
        "form": sheet["form"],
        "machine": sheet["machine"],
        "production_date": sheet["production_date"],
        "shift": sheet["shift"],
        "page_no": sheet["page_no"],
        "status": sheet["status"],
        "version": sheet["version"],
        "picked_up_by": sheet["picked_up_by"],
        "status_since": sheet["status_since"],
        "photos": photos,
        "rows": rows,
    }


def _perform_pickup(
    conn: sqlite3.Connection,
    sheet_id: int,
    user: dict,
    takeover: bool = False,
) -> dict:
    sheet = get_sheet_by_id(conn, sheet_id)
    if not sheet:
        raise AppError("not_found", "Không tìm thấy biểu mẫu", 404)

    if sheet["picked_up_by"] and sheet["picked_up_by"] != user["id"]:
        if not takeover:
            raise AppError(
                code="already_picked_up",
                message="Bảng này đã có người kiểm tra",
                status_code=409,
            )
        action = "takeover"
    else:
        action = "pickup"

    # Preserve status_since if entering/in review
    status_since = sheet["status_since"] or datetime.now(timezone.utc).isoformat()
    update_sheet_status(
        conn=conn,
        sheet_id=sheet_id,
        status="in_review",
        picked_up_by=user["id"],
        status_since=status_since,
    )
    create_event(
        conn=conn,
        user_id=user["id"],
        action=action,
        sheet_id=sheet_id,
        detail=f"{action} by user {user['id']}",
    )
    return {"status": "in_review", "picked_up_by": user["id"]}


@router.post("/api/sheets/{sheet_id}/pickup")
def api_sheet_pickup(
    sheet_id: int,
    payload: Optional[PickupRequest] = None,
    user: dict = Depends(require_role("review_sheet")),
    conn: sqlite3.Connection = Depends(get_db),
):
    takeover = payload.takeover if payload else False
    return _perform_pickup(conn, sheet_id, user, takeover=takeover)


def _perform_confirm(
    conn: sqlite3.Connection,
    sheet_id: int,
    user: dict,
    version: int,
) -> dict:
    sheet = get_sheet_by_id(conn, sheet_id)
    if not sheet:
        raise AppError("not_found", "Không tìm thấy biểu mẫu", 404)

    if version != sheet["version"]:
        raise AppError(
            code="stale_version",
            message="Dữ liệu đã thay đổi kể từ khi tải xuống. Vui lòng tải lại tệp mới nhất.",
            status_code=409,
        )

    if sheet["status"] not in ("needs_review", "in_review"):
        raise AppError(
            code="not_reviewable",
            message="Chỉ xác nhận được bảng đang chờ hoặc đang kiểm tra",
            status_code=409,
        )

    old_status = sheet["status"]
    update_sheet_status(
        conn=conn,
        sheet_id=sheet_id,
        status="reviewed",
        picked_up_by=sheet["picked_up_by"] or user["id"],
    )
    create_event(
        conn=conn,
        user_id=user["id"],
        action="confirm",
        sheet_id=sheet_id,
        old_value=old_status,
        new_value="reviewed",
        detail=f"Confirmed version {version}",
    )
    return {"version": sheet["version"], "status": "reviewed"}


@router.post("/api/sheets/{sheet_id}/confirm")
def api_sheet_confirm(
    sheet_id: int,
    payload: ConfirmRequest,
    user: dict = Depends(require_role("review_sheet")),
    conn: sqlite3.Connection = Depends(get_db),
):
    return _perform_confirm(conn, sheet_id, user, version=payload.version)


@router.get("/api/sheets/{sheet_id}/excel")
@router.get("/office/sheets/{sheet_id}/excel")
def sheet_excel_download(
    sheet_id: int,
    user: dict = Depends(require_role("review_sheet")),
    conn: sqlite3.Connection = Depends(get_db),
):
    sheet = get_sheet_by_id(conn, sheet_id)
    if not sheet:
        raise AppError("not_found", "Không tìm thấy biểu mẫu", 404)

    prod_date = sheet["production_date"]
    if isinstance(prod_date, str):
        prod_date = date.fromisoformat(prod_date)

    sheet_dict = {
        "sheet_id": sheet["id"],
        "version": sheet["version"],
        "form": sheet["form"],
        "machine": sheet["machine"],
        "production_date": prod_date,
        "shift": sheet["shift"],
        "page_no": sheet["page_no"],
    }
    rows = get_sheet_data(conn, sheet["id"])
    wb = build_page_workbook(sheet_dict, rows)
    buf = io.BytesIO()
    wb.save(buf)
    excel_bytes = buf.getvalue()
    filename = page_filename(sheet["machine"], prod_date, sheet["shift"], sheet["page_no"])

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Sheet-Version": str(sheet["version"]),
        },
    )


# -----------------------------------------------------------------------------
# Photo Serving
# -----------------------------------------------------------------------------


def resolve_photo_file(photo_path_str: str | None, photos_dir: Path | str | None) -> Path | None:
    if not photo_path_str:
        return None
    p = Path(photo_path_str)
    if p.is_file():
        return p
    if photos_dir:
        photos_dir = Path(photos_dir)
        if not p.is_absolute():
            candidate = photos_dir / p
            if candidate.is_file():
                return candidate
        candidate_name = photos_dir / p.name
        if candidate_name.is_file():
            return candidate_name
    return None


def ensure_display_copy(
    conn: sqlite3.Connection,
    photo: sqlite3.Row | dict,
    photos_dir: Path | str | None,
) -> Path | None:
    small_path_str = photo.get("small_copy_path") if isinstance(photo, dict) else photo["small_copy_path"]
    existing = resolve_photo_file(small_path_str, photos_dir)
    if existing is not None:
        return existing

    orig_path_str = photo.get("path") if isinstance(photo, dict) else photo["path"]
    orig_file = resolve_photo_file(orig_path_str, photos_dir)
    if orig_file is None:
        return None

    try:
        raw_bytes = orig_file.read_bytes()
        upright_bytes = display_copy(raw_bytes)
    except Exception:
        return None  # the page then shows no picture rather than the sideways original

    target_dir = Path(photos_dir).resolve() if photos_dir else orig_file.parent.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    sha = photo.get("sha256") if isinstance(photo, dict) else photo["sha256"]
    if not sha:
        sha = hashlib.sha256(raw_bytes).hexdigest()

    upright_file = (target_dir / f"{sha}_display.jpg").resolve()
    upright_file.write_bytes(upright_bytes)

    photo_id = photo.get("id") if isinstance(photo, dict) else photo["id"]
    update_photo_small_copy_path(conn, photo_id, str(upright_file))

    return upright_file


@router.get("/api/photos/{photo_id}")
@router.get("/photos/{photo_id}")
def view_photo(
    photo_id: int,
    request: Request,
    user: dict = Depends(require_role("view_all_sheets")),
    conn: sqlite3.Connection = Depends(get_db),
):
    photo = get_photo_by_id(conn, photo_id)
    if not photo:
        raise AppError("not_found", "Không tìm thấy ảnh", 404)

    photos_dir = getattr(request.app.state, "photos_dir", None)
    if not photos_dir:
        photos_dir = get_default_photos_dir(db_path=getattr(request.app.state, "db_path", None))

    file_path = resolve_photo_file(photo["path"], photos_dir)
    if not file_path or not file_path.is_file():
        raise AppError("not_found", "Tệp ảnh không tồn tại trên đĩa", 404)

    return FileResponse(path=str(file_path))


@router.get("/api/photos/{photo_id}/display")
@router.get("/photos/{photo_id}/display")
def view_photo_display(
    photo_id: int,
    request: Request,
    user: dict = Depends(require_role("view_all_sheets")),
    conn: sqlite3.Connection = Depends(get_db),
):
    photo = get_photo_by_id(conn, photo_id)
    if not photo:
        raise AppError("not_found", "Không tìm thấy ảnh", 404)

    photos_dir = getattr(request.app.state, "photos_dir", None)
    if not photos_dir:
        photos_dir = get_default_photos_dir(db_path=getattr(request.app.state, "db_path", None))

    display_file = ensure_display_copy(conn, photo, photos_dir)
    if not display_file or not display_file.is_file():
        raise AppError("not_found", "Tệp ảnh không tồn tại trên đĩa", 404)

    return FileResponse(path=str(display_file), media_type="image/jpeg")
