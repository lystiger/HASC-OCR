"""Read a corrected page file back (spec criteria 33-36).

The file must be one this system made: the "Báo cáo" tab with the section 6 headings and the hidden
"_meta" tab. Rows can't be added or removed. Formulas are refused, never executed or stored. The result
is plain values per row and field, ready to compare with the database.
"""
from datetime import date, datetime, time
import io
import math
import zipfile

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from hasc.excel.page import (
    CHECK_HEADING,
    COLUMNS,
    FIRST_DATA_ROW,
    HEADING_ROW,
    LAYOUT_VERSION,
    META_TAB,
    NUMERIC_FIELDS,
)

MAX_FILE_BYTES = 5 * 1024 * 1024
# A zip that expands far beyond its size is refused before openpyxl reads it.
MAX_UNZIPPED_BYTES = 50 * 1024 * 1024


class LayoutError(ValueError):
    """The file isn't a page file this system made, or was changed in structure."""


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _plain_text(value, where: str) -> str:
    # Excel turns typed dates and TRUE/FALSE into other types; the database keeps text as written.
    if isinstance(value, bool | date | datetime | time):
        raise LayoutError(f"Ô {where} phải là chữ. Hãy định dạng ô là Text rồi nhập lại.")
    return _text(value)


def _number(value, where: str):
    if isinstance(value, bool):
        raise LayoutError(f"Ô {where} phải là số.")
    try:
        number = float(value) if isinstance(value, int | float) else float(str(value).strip().replace(",", "."))
    except ValueError:
        raise LayoutError(f"Ô {where} phải là số, đang có \"{value}\".") from None
    if not math.isfinite(number):
        raise LayoutError(f"Ô {where} phải là số, đang có \"{value}\".")
    return number


def normalise(field: str, value, where: str = ""):
    """None for an empty cell, a float for a number column, stripped text otherwise."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if field in NUMERIC_FIELDS:
        return _number(value, where or field)
    return _plain_text(value, where or field)


def stored_text(value) -> str | None:
    """How a normalised value is kept in the database: 14 not 14.0, 13.6 as typed."""
    if value is None:
        return None
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return value


def _check_zip(data: bytes) -> None:
    if len(data) > MAX_FILE_BYTES:
        raise LayoutError("Tệp quá lớn (tối đa 5 MB).")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_UNZIPPED_BYTES:
                raise LayoutError("Tệp không hợp lệ.")
    except zipfile.BadZipFile:
        raise LayoutError("Đây không phải tệp Excel (.xlsx).") from None


def _check_headings(ws) -> None:
    sub_row = HEADING_ROW + 1
    for index, (group, sub, _) in enumerate(COLUMNS, start=1):
        found = ws.cell(row=HEADING_ROW if group is None else sub_row, column=index).value
        if _text(found) != sub:
            raise LayoutError(f"Cấu trúc tệp đã bị thay đổi (cột {get_column_letter(index)}). Hãy tải tệp mới nhất và chỉ sửa giá trị.")
    if _text(ws.cell(row=HEADING_ROW, column=len(COLUMNS) + 1).value) != CHECK_HEADING:
        raise LayoutError("Cấu trúc tệp đã bị thay đổi (cột Cần kiểm tra). Hãy tải tệp mới nhất và chỉ sửa giá trị.")


def _read_meta(wb) -> dict:
    if META_TAB not in wb.sheetnames:
        raise LayoutError("Tệp thiếu thông tin ẩn (_meta). Hãy tải tệp mới nhất từ hệ thống.")
    meta = {_text(k): v for k, v in wb[META_TAB].iter_rows(min_row=2, max_col=2, values_only=True) if k}
    try:
        sheet_id, version, layout = int(meta["sheet_id"]), int(meta["version"]), int(meta["layout_version"])
    except (KeyError, TypeError, ValueError):
        raise LayoutError("Thông tin ẩn (_meta) của tệp bị hỏng. Hãy tải tệp mới nhất từ hệ thống.") from None
    if layout != LAYOUT_VERSION:
        raise LayoutError("Tệp dùng mẫu cũ. Hãy tải tệp mới nhất từ hệ thống.")
    keys = _text(meta.get("row_keys"))
    return {"sheet_id": sheet_id, "version": version, "row_keys": keys.split(",") if keys else []}


def read_correction(data: bytes) -> dict:
    """Return {sheet_id, version, row_keys, rows: [{field: normalised value}]} or raise LayoutError."""
    _check_zip(data)
    try:
        wb = load_workbook(io.BytesIO(data), data_only=False)
    except Exception:  # openpyxl raises many types for a damaged file
        raise LayoutError("Không đọc được tệp Excel.") from None
    if "Báo cáo" not in wb.sheetnames:
        raise LayoutError("Tệp thiếu trang \"Báo cáo\". Hãy tải tệp mới nhất từ hệ thống.")
    ws = wb["Báo cáo"]
    meta = _read_meta(wb)
    _check_headings(ws)

    count = len(meta["row_keys"])
    rows = []
    last_row = max(ws.max_row, FIRST_DATA_ROW + count - 1)
    for number in range(FIRST_DATA_ROW, last_row + 1):
        values = {}
        for index, (_, _, field) in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=number, column=index)
            where = f"{get_column_letter(index)}{number}"
            if cell.data_type == "f":
                raise LayoutError(f"Ô {where} chứa công thức. Hãy nhập giá trị, không dùng công thức.")
            values[field] = normalise(field, cell.value, where)
        if number - FIRST_DATA_ROW >= count:
            if any(v is not None for v in values.values()):
                raise LayoutError(f"Dòng {number} được thêm vào. Không thêm hay xóa dòng trong tệp sửa.")
            continue
        rows.append(values)
    return {**meta, "rows": rows}
