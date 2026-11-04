"""Build one page's correctable .xlsx in the spec section 6 layout.

Cells come from the database shape: each cell has a value, an `inherited` flag and a
`flag` (none, doubt, rule, unreadable or disputed). Flags only colour cells; no value is
changed here. Numbers are written as Excel numbers, text as text.
"""

from datetime import date
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADING_ROW = 5
FIRST_DATA_ROW = 7
FREEZE_CELL = f"A{FIRST_DATA_ROW}"
TITLE = "BÁO CÁO CHI TIẾT MÁY THỔI T1,T6,T2,T3"
CHECK_HEADING = "Cần kiểm tra"
META_TAB = "_meta"
LAYOUT_VERSION = 1

FLAG_FILLS = {
    "doubt": "FFFF00",
    "disputed": "FFFF00",
    "rule": "FF0000",
    "unreadable": "BFBFBF",
}

LEGEND = (
    ("FFFF00", "vàng = AI chưa chắc"),
    ("FF0000", "đỏ = sai quy tắc"),
    ("BFBFBF", "xám = không đọc được"),
)

# (group heading or None, sub heading, field). Without a group the heading spans rows 5-6.
COLUMNS = (
    (None, "Máy", "machine"),
    (None, "Khách hàng", "customer"),
    (None, "Loại màng", "film_type"),
    (None, "Mã cuộn", "roll_code"),
    (None, "Công thức hạt", "pellet_formula"),
    (None, "Ca/ngày trộn hạt", "mixing_shift_date"),
    (None, "Dày/mic", "thickness_mic"),
    (None, "Khổ rộng/mm", "width_mm"),
    (None, "Dài/mm", "length_mm"),
    ("Khối lượng Kg", "Cả bì", "gross_kg"),
    ("Khối lượng Kg", "Lõi", "core_kg"),
    ("Khối lượng Kg", "Bìa", "cover_kg"),
    ("Khối lượng Kg", "Sau trừ bì", "net_kg"),
    ("Độ dày đo được", "1", "thickness_1"),
    ("Độ dày đo được", "2", "thickness_2"),
    ("Độ dày đo được", "3", "thickness_3"),
    ("Độ dày đo được", "4", "thickness_4"),
    ("Độ dày đo được", "5", "thickness_5"),
    ("Độ dày đo được", "6", "thickness_6"),
    ("Độ dày đo được", "Trung bình", "thickness_avg"),
    ("Đường kính cuộn", "Đầu thu 1(A)", "diameter_a"),
    ("Đường kính cuộn", "Đầu thu 2(B)", "diameter_b"),
)


NUMERIC_FIELDS = {
    "thickness_mic",
    "width_mm",
    "length_mm",
    "gross_kg",
    "core_kg",
    "cover_kg",
    "net_kg",
    "thickness_1",
    "thickness_2",
    "thickness_3",
    "thickness_4",
    "thickness_5",
    "thickness_6",
    "thickness_avg",
    "diameter_a",
    "diameter_b",
}


def page_filename(machine: str, production_date: date, shift: int, page_no: int) -> str:
    return f"HASC_{machine}_{production_date.isoformat()}_Ca{shift}_Trang{page_no}.xlsx"


def _fill(hex_color: str) -> PatternFill:
    return PatternFill(fill_type="solid", start_color=hex_color, end_color=hex_color)


def _write_value(cell, value, field=None) -> None:
    if value is None:
        return
    if isinstance(value, int | float) and not isinstance(value, bool):
        cell.value = value
        return
    if isinstance(value, str):
        if value.startswith("="):
            cell.value = value
            cell.data_type = "s"  # reader or hand text is never stored as a formula
            return
        if field in NUMERIC_FIELDS:
            try:
                cell.value = float(value) if "." in value else int(value)
                return
            except ValueError:
                pass
    cell.value = value


def _write_header(ws, sheet: dict) -> None:
    production_date = sheet["production_date"]
    ws["A1"] = f"{TITLE}    Mã số: {sheet['form']}"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = (
        f"Ngày: {production_date.day}   Tháng: {production_date.month}   "
        f"Năm: {production_date.year}   Ca: {sheet['shift']}   "
        f"Máy: {sheet['machine']}   Trang: {sheet['page_no']}"
    )
    ws["A3"] = f"Tên nhân viên trong ca: {sheet.get('crew', '')}"
    ws["A4"] = "Khóa cột:"
    for index, (color, text) in enumerate(LEGEND):
        cell = ws.cell(row=4, column=2 + index, value=text)
        cell.fill = _fill(color)


def _write_headings(ws) -> None:
    centre = Alignment(horizontal="center", vertical="center", wrap_text=True)
    bold = Font(bold=True)
    sub_row = HEADING_ROW + 1
    group_start = 1
    for index, (group, sub, _) in enumerate(COLUMNS, start=1):
        if group is None:
            ws.cell(row=HEADING_ROW, column=index, value=sub)
            ws.merge_cells(start_row=HEADING_ROW, start_column=index, end_row=sub_row, end_column=index)
            continue
        ws.cell(row=sub_row, column=index, value=sub)
        if index == 1 or COLUMNS[index - 2][0] != group:
            group_start = index
            ws.cell(row=HEADING_ROW, column=index, value=group)
        if index == len(COLUMNS) or COLUMNS[index][0] != group:
            ws.merge_cells(start_row=HEADING_ROW, start_column=group_start, end_row=HEADING_ROW, end_column=index)
    check_column = len(COLUMNS) + 1
    ws.cell(row=HEADING_ROW, column=check_column, value=CHECK_HEADING)
    ws.merge_cells(start_row=HEADING_ROW, start_column=check_column, end_row=sub_row, end_column=check_column)
    for row in ws.iter_rows(min_row=HEADING_ROW, max_row=sub_row, max_col=check_column):
        for cell in row:
            cell.font = bold
            cell.alignment = centre


def _write_rows(ws, rows: list[dict]) -> None:
    check_column = len(COLUMNS) + 1
    for offset, row in enumerate(rows):
        number = FIRST_DATA_ROW + offset
        cells = row["cells"]
        for index, (_, _, field) in enumerate(COLUMNS, start=1):
            cell_data = cells.get(field)
            if cell_data is None or cell_data.get("inherited"):
                continue
            cell = ws.cell(row=number, column=index)
            _write_value(cell, cell_data.get("value"), field=field)
            color = FLAG_FILLS.get(cell_data.get("flag"))
            if color:
                cell.fill = _fill(color)
        if row.get("check"):
            ws.cell(row=number, column=check_column, value=row["check"])


def _set_widths(ws, rows: list[dict]) -> None:
    headings = [sub for _, sub, _ in COLUMNS] + [CHECK_HEADING]
    for index, heading in enumerate(headings, start=1):
        width = max(len(heading), 10)
        for row in rows:
            cell_data = row["cells"].get(COLUMNS[index - 1][2]) if index <= len(COLUMNS) else None
            if cell_data and cell_data.get("value") is not None:
                width = max(width, len(str(cell_data["value"])))
        ws.column_dimensions[get_column_letter(index)].width = min(width + 2, 40)
    ws.column_dimensions[get_column_letter(len(COLUMNS) + 1)].width = 30


def _write_meta(wb: Workbook, sheet: dict, rows: list[dict]) -> None:
    meta = wb.create_sheet(META_TAB)
    meta.sheet_state = "hidden"
    meta.append(["key", "value"])
    meta.append(["sheet_id", sheet["sheet_id"]])
    meta.append(["version", sheet["version"]])
    meta.append(["layout_version", LAYOUT_VERSION])
    meta.append(["row_keys", ",".join(str(row["row_key"]) for row in rows)])


def build_page_workbook(sheet: dict, rows: list[dict]) -> Workbook:
    """Return the page workbook. `sheet` needs form, machine, production_date, shift, page_no,
    sheet_id and version; each row needs row_key, cells and an optional check text."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Báo cáo"
    _write_header(ws, sheet)
    _write_headings(ws)
    _write_rows(ws, rows)
    _set_widths(ws, rows)
    ws.freeze_panes = FREEZE_CELL
    _write_meta(wb, sheet, rows)
    return wb
