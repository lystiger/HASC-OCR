"""Review screen (P154): the sheet page's table, history, and its save.

The page shows each row as the Excel file does: copied-down values filled in (grey), flagged cells coloured.
`Lưu` and `Xác nhận` send the whole table back, and `apply_values` applies it with the Excel path's rules,
so a value counts as changed exactly when it would in a re-uploaded file.
"""
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from hasc.db import get_sheet_by_id, get_sheet_data
from hasc.excel.correction import LayoutError, normalise
from hasc.excel.page import COLUMNS, NUMERIC_FIELDS, _prepare_rows
from hasc.web.auth import get_db
from hasc.web.corrections import _stale, apply_values
from hasc.web.errors import AppError
from hasc.web.roles import require_role

router = APIRouter()

FLAG_LABELS = {"doubt": "AI chưa chắc", "disputed": "AI chưa chắc", "rule": "Sai quy tắc", "unreadable": "Không đọc được"}
FLAG_CLASS = {"doubt": "doubt", "disputed": "doubt", "rule": "rule", "unreadable": "unreadable"}
PINNED = ("roll_code",)  # stays in view while the table scrolls sideways, with the row number
ACTION_LABELS = {
    "upload": "Tải ảnh lên",
    "reupload": "Tải lên bản sửa Excel",
    "web_save": "Lưu trên web",
    "web_confirm": "Xác nhận trên web",
    "confirm": "Xác nhận",
    "value_change": "Sửa ô",
    "value_carry": "Ô chép xuống theo dòng đầu",
    "pickup": "Nhận kiểm tra",
    "takeover": "Tiếp quản kiểm tra",
    "retry": "Đọc lại",
    "invalid": "Đánh dấu ảnh không hợp lệ",
    "admin_open_sheet": "Admin mở biểu mẫu",
}
HISTORY_LIMIT = 200


def heading(group: Optional[str], sub: str) -> str:
    return f"{group} · {sub}" if group else sub


FIELD_LABELS = {field: heading(group, sub) for group, sub, field in COLUMNS}


def review_table(conn: sqlite3.Connection, sheet_id: int) -> dict:
    """Columns and rows for the review table, plus how many cells are flagged."""
    rows = _prepare_rows(get_sheet_data(conn, sheet_id))
    columns = [{"field": field, "group": group, "sub": sub, "label": FIELD_LABELS[field],
                "numeric": field in NUMERIC_FIELDS, "pinned": field in PINNED} for group, sub, field in COLUMNS]
    # Two heading rows, as on the paper form: a column without a group spans both.
    top, bottom = [], []
    for column in columns:
        if not column["group"]:
            top.append({"text": column["sub"], "colspan": 1, "rowspan": 2, "pinned": column["pinned"]})
        elif top and top[-1]["rowspan"] == 1 and top[-1]["text"] == column["group"]:
            top[-1]["colspan"] += 1
            bottom.append(column)
        else:
            top.append({"text": column["group"], "colspan": 1, "rowspan": 1, "pinned": False})
            bottom.append(column)
    table, flagged = [], 0
    for number, row in enumerate(rows, start=1):
        cells = []
        for column in columns:
            cell = row["cells"].get(column["field"]) or {}
            flag = cell.get("flag") if cell.get("flag") in FLAG_CLASS else None
            flagged += flag is not None
            value = cell.get("value")
            cells.append({**column, "value": "" if value is None else str(value).replace(".", ",")
                          if column["numeric"] else str(value),
                          "inherited": bool(cell.get("inherited")), "flag": FLAG_CLASS.get(flag),
                          "flag_label": FLAG_LABELS.get(flag), "note": cell.get("note")})
        table.append({"number": number, "row_key": str(row["row_key"]), "cells": cells})
    return {"columns": columns, "top": top, "bottom": bottom, "rows": table, "flagged": flagged}


def review_history(conn: sqlite3.Connection, sheet_id: int) -> list[dict]:
    events = conn.execute(f"""
        SELECT e.at, e.action, e.field, e.old_value, e.new_value, e.row_id, u.username,
               (SELECT COUNT(*) FROM rows r2 WHERE r2.sheet_id = r.sheet_id AND r2.position <= r.position
                AND r2.invalid = 0) AS row_number
        FROM events e LEFT JOIN users u ON u.id = e.user_id LEFT JOIN rows r ON r.id = e.row_id
        WHERE e.sheet_id = ? ORDER BY e.id DESC LIMIT {HISTORY_LIMIT}
    """, (sheet_id,)).fetchall()
    return [{**dict(e), "label": ACTION_LABELS.get(e["action"], e["action"]),
             "field_label": FIELD_LABELS.get(e["field"], e["field"])} for e in events]


class ReviewRequest(BaseModel):
    version: int
    row_keys: list[str]
    rows: list[dict[str, Optional[str]]]
    confirm: bool = False


@router.post("/api/sheets/{sheet_id}/review")
def api_sheet_review(sheet_id: int, payload: ReviewRequest,
                     user: dict = Depends(require_role("review_sheet")),
                     conn: sqlite3.Connection = Depends(get_db)):
    sheet = get_sheet_by_id(conn, sheet_id)
    if not sheet:
        raise AppError("not_found", "Không tìm thấy biểu mẫu", 404)
    if payload.version != sheet["version"]:  # before the value checks: reloading is the fix, not retyping
        raise _stale()
    if payload.confirm and sheet["status"] not in ("needs_review", "in_review"):
        raise AppError(code="not_reviewable", message="Biểu mẫu này đã được xác nhận.", status_code=409)
    # Lưu may keep a value the page showed that is not a number (the AI's unreadable "?"), so a page can be
    # saved part-way; Xác nhận needs every number column to hold a number.
    shown = [] if payload.confirm else review_table(conn, sheet_id)["rows"]
    values = []
    for number, row in enumerate(payload.rows, start=1):
        if set(row) - set(FIELD_LABELS):
            raise AppError(code="bad_layout", message="Bảng gửi lên có cột lạ. Hãy tải lại trang.", status_code=422)
        before = {c["field"]: c["value"] for c in shown[number - 1]["cells"]} if number <= len(shown) else {}
        checked = {}
        for field in FIELD_LABELS:
            raw = row.get(field)
            try:
                checked[field] = normalise(field, raw, f"dòng {number} · {FIELD_LABELS[field]}")
            except LayoutError as error:
                if raw is None or before.get(field) != raw.strip():
                    raise AppError(code="bad_value", message=str(error), status_code=422) from None
                checked[field] = raw.strip()
        values.append(checked)
    return apply_values(conn, sheet, user, payload.version, payload.row_keys, values, finish=payload.confirm,
                        action="web_confirm" if payload.confirm else "web_save")
