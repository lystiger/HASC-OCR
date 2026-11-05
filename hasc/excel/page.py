"""Build one page's correctable .xlsx in the spec section 6 layout.

Cells come from the database shape: each cell has a value, an `inherited` flag and a
`flag` (none, doubt, rule, unreadable or disputed). Flags only colour cells; no value is
changed here. Numbers are written as Excel numbers, text as text.
"""

from datetime import date
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from hasc.excel import style

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
    ("FF0000", "đỏ = sai quy tắc/công thức/tiêu chuẩn"),
    ("BFBFBF", "xám = không đọc được"),
)

GREY_FONT_COLOR = "808080"
BLACK_FONT_COLOR = "000000"

INHERITED_FONT = Font(color=GREY_FONT_COLOR)
PAPER_FONT = Font(color=BLACK_FONT_COLOR)

CARRIED_FIELDS = (
    "customer",
    "film_type",
    "pellet_formula",
    "mixing_shift_date",
    "thickness_mic",
    "width_mm",
    "length_mm",
)

COLUMN_WIDTHS = {
    "customer": 22,
    "film_type": 36,
    "roll_code": 22,
}

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
    ws["A4"] = "Kí hiệu:"
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


def _prepare_rows(rows: list[dict]) -> list[dict]:
    last_carried = {}
    prepared = []
    for row in rows:
        cells = dict(row.get("cells", {}))
        for field in CARRIED_FIELDS:
            cell_data = cells.get(field)
            if cell_data is not None and cell_data.get("value") not in (None, ""):
                last_carried[field] = cell_data
            elif field in last_carried:
                inherited_cell = dict(last_carried[field])
                inherited_cell["inherited"] = True
                cells[field] = inherited_cell
        prepared.append({**row, "cells": cells})
    return prepared


def _write_rows(ws, rows: list[dict]) -> None:
    check_column = len(COLUMNS) + 1
    for offset, row in enumerate(rows):
        number = FIRST_DATA_ROW + offset
        cells = row.get("cells", {})
        for index, (_, _, field) in enumerate(COLUMNS, start=1):
            cell_data = cells.get(field)
            if cell_data is None:
                continue
            cell = ws.cell(row=number, column=index)
            value = cell_data.get("value")
            if value is not None:
                _write_value(cell, value, field=field)
                if cell_data.get("inherited"):
                    cell.font = INHERITED_FONT
                else:
                    cell.font = PAPER_FONT
            color = FLAG_FILLS.get(cell_data.get("flag"))
            if color:
                cell.fill = _fill(color)
        if row.get("check"):
            check_cell = ws.cell(row=number, column=check_column, value=row["check"])
            check_cell.font = PAPER_FONT


def _set_widths(ws, rows: list[dict]) -> None:
    headings = [sub for _, sub, _ in COLUMNS] + [CHECK_HEADING]
    for index, heading in enumerate(headings, start=1):
        field = COLUMNS[index - 1][2] if index <= len(COLUMNS) else None
        # Number columns stay narrow and their headings wrap, so the page prints readable on A4.
        base = COLUMN_WIDTHS.get(field, 7 if field in NUMERIC_FIELDS else 10)
        width = base if field in NUMERIC_FIELDS else max(len(heading), 10)
        for row in rows:
            cell_data = row.get("cells", {}).get(field) if field else None
            if cell_data and cell_data.get("value") is not None:
                width = max(width, len(str(cell_data["value"])))
        ws.column_dimensions[get_column_letter(index)].width = max(base, min(width + 2, 40))
    ws.column_dimensions[get_column_letter(len(COLUMNS) + 1)].width = 24


def _style(ws, row_count: int) -> None:
    last_column = len(COLUMNS) + 1
    last_row = FIRST_DATA_ROW + max(row_count, 1) - 1
    style.use_house_font(ws)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_column)
    ws["A1"].font = style.font(ws["A1"], size=14, bold=True, color=style.ACCENT)
    ws["A1"].alignment = style.CENTRE
    ws.row_dimensions[1].height = 26
    ws["A4"].font = style.font(ws["A4"], bold=True)
    for column in range(2, 2 + len(LEGEND)):
        cell = ws.cell(row=4, column=column)
        cell.alignment = style.CENTRE
        cell.border = style.GRID
    style.shade_heading(ws, HEADING_ROW, HEADING_ROW + 1, last_column)
    ws.row_dimensions[HEADING_ROW].height = 22
    ws.row_dimensions[HEADING_ROW + 1].height = 30
    style.frame(ws, FIRST_DATA_ROW, last_row, last_column)
    for row in ws.iter_rows(min_row=FIRST_DATA_ROW, max_row=last_row, max_col=last_column):
        ws.row_dimensions[row[0].row].height = style.ROW_HEIGHT
        for cell in row:
            field = COLUMNS[cell.column - 1][2] if cell.column <= len(COLUMNS) else None
            cell.alignment = style.CENTRE if field in NUMERIC_FIELDS or field in ("machine", "roll_code") else style.MIDDLE
            if field and field.endswith("_kg"):
                cell.number_format = style.KG_FORMAT
    style.print_setup(ws, f"{HEADING_ROW}:{HEADING_ROW + 1}")


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
    prepared_rows = _prepare_rows(rows)
    _write_header(ws, sheet)
    _write_headings(ws)
    _write_rows(ws, prepared_rows)
    _set_widths(ws, prepared_rows)
    _style(ws, len(prepared_rows))
    ws.freeze_panes = FREEZE_CELL
    _write_meta(wb, sheet, rows)
    return wb
