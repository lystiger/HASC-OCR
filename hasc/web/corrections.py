"""Apply a re-uploaded, corrected page file (spec criteria 33-36, P148 follow-up for AI accuracy).

Each changed value becomes a person's value and gets a "value_change" event with who, when, old and new.
The event's detail keeps where the old value came from and its flag ("ai|doubt"), so AI accuracy can
be counted later. A changed first row of a run updates the copied-down values below it (criterion 34).
"""
import sqlite3
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse

from hasc.db import get_sheet_by_id, get_sheet_data
from hasc.excel.correction import MAX_FILE_BYTES, LayoutError, normalise, read_correction, stored_text
from hasc.excel.page import CARRIED_FIELDS, COLUMNS, _prepare_rows
from hasc.web import security
from hasc.web.auth import get_db
from hasc.web.errors import AppError
from hasc.web.roles import require_role

router = APIRouter()

CORRECTABLE = ("needs_review", "in_review", "reviewed")


def _stale() -> AppError:
    return AppError(code="stale_version",
                    message="Dữ liệu đã thay đổi kể từ khi tải xuống. Vui lòng tải lại tệp mới nhất.",
                    status_code=409)


def _stored(field: str, value):
    """A database value in the form the file is read into. Text the reader kept in a number column
    (like "?") stays text, so any number typed over it counts as a change."""
    try:
        return normalise(field, value)
    except LayoutError:
        return value.strip() if isinstance(value, str) else value


def apply_correction(conn: sqlite3.Connection, sheet_id: int, user: dict, data: bytes) -> dict:
    """Validate the file, then apply every change in one transaction. Returns {version, status, changed}."""
    sheet = get_sheet_by_id(conn, sheet_id)
    if not sheet:
        raise AppError("not_found", "Không tìm thấy biểu mẫu", 404)
    try:
        parsed = read_correction(data)
    except LayoutError as error:
        raise AppError(code="bad_layout", message=str(error), status_code=422) from None
    if parsed["sheet_id"] != sheet_id:
        raise AppError(code="bad_layout", message="Tệp này thuộc một biểu mẫu khác.", status_code=422)
    if parsed["version"] != sheet["version"]:
        raise _stale()
    if sheet["status"] not in CORRECTABLE:
        raise AppError(code="not_reviewable", message="Biểu mẫu này chưa có dữ liệu để sửa.", status_code=409)

    rows = get_sheet_data(conn, sheet_id)
    if [str(r["row_key"]) for r in rows] != parsed["row_keys"]:
        raise _stale()
    shown = _prepare_rows(rows)  # what the downloaded file showed, copied-down values included

    now = security.now().isoformat()
    changes, updates = [], []
    run = {}  # carried field -> the new value of the run's written row above
    for row, display, values in zip(rows, shown, parsed["rows"]):
        if all(values[f] is None for _, _, f in COLUMNS) and any(
                c.get("value") not in (None, "") for c in row["cells"].values()):
            raise AppError(code="bad_layout", status_code=422,
                           message="Một dòng đã bị xóa hết. Không thêm hay xóa dòng trong tệp sửa.")
        for _, _, field in COLUMNS:
            new = values[field]
            cell = row["cells"].get(field)
            seen = display["cells"].get(field)
            seen_value = _stored(field, seen.get("value")) if seen else None
            # The AI's own blank cell (an unreadable mark) is shown with the value above; whatever the
            # person enters there, kept or typed, is their reading of the AI's value.
            own = cell is not None and not cell.get("inherited")
            if field in CARRIED_FIELDS and seen and seen.get("inherited") and (not own or new is None):
                # Untouched or cleared copied-down value: it follows the run's (possibly corrected)
                # first row, as a blank carry-down does on every download (spec 17).
                if new == seen_value or new is None:
                    if (cell is not None and cell.get("inherited")
                            and run.get(field) != _stored(field, cell.get("value"))):
                        updates.append({"row_id": row["id"], "field": field, "new": stored_text(run.get(field)),
                                        "old": cell.get("value")})
                    continue
            elif new == seen_value and not (own and seen and seen.get("inherited")):
                if field in CARRIED_FIELDS and new is not None:
                    run[field] = new
                continue
            changes.append({
                "row_id": row["id"], "field": field, "new": stored_text(new),
                # A value the file showed but the database never stored was copied down for display.
                "old": cell.get("value") if own else seen.get("value") if seen else None,
                "detail": f"{cell['source'] if own else 'carried' if seen and seen.get('inherited') else cell['source'] if cell else 'none'}|{cell['flag'] if cell else 'none'}",
            })
            if field in CARRIED_FIELDS and new is not None:
                run[field] = new

    with conn:
        # Checked again inside the write: a reader or another upload that changed the sheet since
        # it was read above has raised its version, so this upload is stale and nothing is written.
        version = sheet["version"] + (1 if changes or updates else 0)
        claimed = conn.execute("""
            UPDATE sheets SET version = ?, status = 'reviewed', status_since = ?,
                picked_up_by = COALESCE(picked_up_by, ?) WHERE id = ? AND version = ?
        """, (version, now, user["id"], sheet_id, sheet["version"]))
        if claimed.rowcount != 1:
            raise _stale()
        for c in changes:
            conn.execute("""
                INSERT INTO cells (row_id, field, value, inherited, source, flag, note)
                VALUES (?, ?, ?, 0, 'person', 'none', NULL)
                ON CONFLICT(row_id, field) DO UPDATE SET
                    value = excluded.value, inherited = 0, source = 'person', flag = 'none', note = NULL
            """, (c["row_id"], c["field"], c["new"]))
            conn.execute("""
                INSERT INTO events (at, user_id, action, sheet_id, row_id, field, old_value, new_value, detail)
                VALUES (?, ?, 'value_change', ?, ?, ?, ?, ?, ?)
            """, (now, user["id"], sheet_id, c["row_id"], c["field"], c["old"], c["new"], c["detail"]))
        for u in updates:
            # A copied-down value now carries a person's first-row value. Logged as "value_carry", not
            # "value_change": the AI's one error is counted once, on the row where it was written.
            conn.execute("""UPDATE cells SET value = ?, source = 'person', flag = 'none'
                            WHERE row_id = ? AND field = ? AND inherited = 1""", (u["new"], u["row_id"], u["field"]))
            conn.execute("""
                INSERT INTO events (at, user_id, action, sheet_id, row_id, field, old_value, new_value, detail)
                VALUES (?, ?, 'value_carry', ?, ?, ?, ?, ?, 'carried')
            """, (now, user["id"], sheet_id, u["row_id"], u["field"], u["old"], u["new"]))
        conn.execute("UPDATE rows SET reviewed = 1 WHERE sheet_id = ? AND invalid = 0", (sheet_id,))
        conn.execute("""
            INSERT INTO events (at, user_id, action, sheet_id, old_value, new_value, detail)
            VALUES (?, ?, 'reupload', ?, ?, ?, ?)
        """, (now, user["id"], sheet_id, str(sheet["version"]), str(version), f"changed={len(changes)}"))
    return {"version": version, "status": "reviewed", "changed": len(changes)}


def _read_upload(file: UploadFile) -> bytes:
    data = file.file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise AppError(code="bad_layout", message="Tệp quá lớn (tối đa 5 MB).", status_code=422)
    return data


@router.post("/api/sheets/{sheet_id}/excel")
def api_sheet_correction(sheet_id: int, file: UploadFile = File(...),
                               user: dict = Depends(require_role("review_sheet")),
                               conn: sqlite3.Connection = Depends(get_db)):
    return apply_correction(conn, sheet_id, user, _read_upload(file))


@router.post("/office/sheets/{sheet_id}/excel")
def office_sheet_correction(sheet_id: int, request: Request, file: Optional[UploadFile] = File(None),
                                  user: dict = Depends(require_role("review_sheet")),
                                  conn: sqlite3.Connection = Depends(get_db)):
    if file is None or not file.filename:  # the button pressed with no file chosen
        return RedirectResponse(url=f"/office/sheets/{sheet_id}?upload_error={quote('Chưa chọn tệp Excel đã sửa.')}", status_code=303)
    try:
        result = apply_correction(conn, sheet_id, user, _read_upload(file))
    except AppError as error:
        if error.status_code == 404:
            raise
        return RedirectResponse(url=f"/office/sheets/{sheet_id}?upload_error={quote(error.message)}", status_code=303)
    return RedirectResponse(url=f"/office/sheets/{sheet_id}?corrected={result['changed']}", status_code=303)
