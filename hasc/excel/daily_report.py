"""Build the daily report "Báo cáo sản xuất ngày" (form HASC281224) for one production date.

Each shift block has one line per machine + customer + film type + product code, weighing the sum of
net_kg over the valid rows of valid photos. Pending goods, waste and the signature are left blank for
the office. A line that includes a sheet not yet Reviewed is marked `Chưa xác nhận`.

A row's machine is the one written in its Máy cell (form A rev 3 writes it on the first row and whenever the
machine changes), carried down the page; rows before any written machine use the machine picked at upload.

A Zalo sheet has no machine of its own: each row's Máy cell holds it (written, or the plan's, set by the worker).
Rows with no usable machine go under UNKNOWN_MACHINE, and a Zalo sheet's product details carry down within that
sheet only.
"""

from collections import defaultdict
from datetime import date
import re
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from hasc.db import get_photos_for_sheet, get_sheet_data, get_user_by_id, list_sheets
from hasc.excel import style
from hasc.intake import is_zalo_sheet

FORM_NO = "HASC281224"
TITLE = "BÁO CÁO SẢN XUẤT NGÀY"
SHIFTS = (1, 2)
MACHINES = ("T1", "T6", "T2", "T3", "M1", "M2", "M3")
HEADINGS = (
    "Máy",
    "Khách hàng",
    "Loại hàng, Màu",
    "Mã hàng (Khổ x Dày x Gh x dài)",
    "Khối lượng (kg)",
    "Hàng chờ xử lý (kg)",
    "Số cuộn sản xuất",
    "Phế phát sinh (kg)",
    "Ghi chú",
    "Ký xác nhận",
)
COLUMN_WIDTHS = (10, 24, 22, 34, 16, 18, 10, 18, 18, 18)
UNKNOWN_MACHINE = "chưa rõ máy"
PENDING_NOTE = "Chưa xác nhận"
UNREAD_NOTE = "Có khối lượng chưa đọc được, chưa cộng"
UNCLEAR_MACHINE_NOTE = "Máy ghi không rõ: {}"
NET_COLUMN = 5
ROLLS_COLUMN = 7
NOTE_COLUMN = 9
CARRIED_FIELDS = ("customer", "film_type", "thickness_mic", "width_mm", "length_mm")


def daily_report_filename(production_date: date) -> str:
    return f"HASC_BaoCaoNgay_{production_date.isoformat()}.xlsx"


def _text(cells: dict, field: str) -> str:
    value = cells.get(field, {}).get("value")
    return "" if value is None else str(value)


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", ".")) if text else None
    except ValueError:
        return None


def _written_machine(cells: dict) -> str:
    """The Máy cell as written, normalised so " t 6 " reads as T6."""
    return re.sub(r"[\s.\-]", "", _text(cells, "machine")).upper()


def _from_valid_photos(cells: dict, valid_photo_ids: set[int]) -> bool:
    sources = {cell.get("source_photo_id") for cell in cells.values() if cell.get("source_photo_id") is not None}
    return sources <= valid_photo_ids


def _collect(conn, production_date: str):
    """Return (lines, leaders). lines maps (shift, machine) to {(layout, customer, film_type, code): totals};
    leaders maps each shift to the ids of the users who uploaded its valid photos, first upload first."""
    lines = defaultdict(dict)
    leaders = defaultdict(list)
    carried_by_section = {}
    for sheet in sorted(list_sheets(conn, production_date=production_date), key=lambda s: (s["shift"], s["machine"], s["page_no"])):
        zalo = is_zalo_sheet(sheet)
        if not zalo and sheet["machine"] not in MACHINES:
            continue
        photos = [photo for photo in get_photos_for_sheet(conn, sheet["id"]) if not photo["invalid"]]
        valid_photo_ids = {photo["id"] for photo in photos}
        for photo in photos:
            if photo["uploaded_by"] is not None and photo["uploaded_by"] not in leaders[sheet["shift"]]:
                leaders[sheet["shift"]].append(photo["uploaded_by"])
        layout = sheet["layout"] or "form_a"
        machine = sheet["machine"]
        # Product details carry down by the machine picked at upload, so a row that only writes a new Máy
        # keeps the product above it.
        section_key = ("zalo", sheet["id"]) if zalo else (sheet["shift"], sheet["machine"], layout)
        carried = carried_by_section.setdefault(section_key, {})
        for row in get_sheet_data(conn, sheet["id"]):
            cells = row["cells"]
            if not _from_valid_photos(cells, valid_photo_ids):
                continue
            written = _written_machine(cells)
            if zalo:
                machine = written if written in MACHINES else UNKNOWN_MACHINE
            elif written in MACHINES:
                machine = written
            section = lines[(sheet["shift"], machine)]
            for field in CARRIED_FIELDS:
                val = cells.get(field, {}).get("value")
                if val is not None and str(val).strip():
                    carried[field] = str(val).strip()
                elif field in carried:
                    cells[field] = {"value": carried[field]}
            thickness, width = _text(cells, "thickness_mic"), _text(cells, "width_mm")
            if layout == "back":
                code = f"{thickness}mic x R{width}"
            else:
                code = f"{thickness}x{width}x{_text(cells, 'length_mm')}"
            key = (layout, _text(cells, "customer"), _text(cells, "film_type"), code)
            line = section.setdefault(key, {"net": 0.0, "rolls": 0, "unconfirmed": False, "unread": False,
                                            "unclear_machines": [], "zalo": False})
            line["zalo"] = line["zalo"] or zalo
            as_written = _text(cells, "machine").strip()
            if written and written not in MACHINES and as_written not in line["unclear_machines"]:
                line["unclear_machines"].append(as_written)
            net = _number(_text(cells, "net_kg"))
            line["net"] += net or 0
            unreadable = cells.get("net_kg", {}).get("flag") == "unreadable"
            line["unread"] = line["unread"] or unreadable or (net is None and bool(_text(cells, "net_kg")))
            line["rolls"] += 1
            line["unconfirmed"] = line["unconfirmed"] or sheet["status"] != "reviewed"
    return lines, leaders


def _write_values(ws, row: int, values, bold: bool = False) -> None:
    for column, value in enumerate(values, start=1):
        if value is None:
            continue
        cell = ws.cell(row=row, column=column, value=value)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        if bold:
            cell.font = Font(bold=True)


def build_daily_report(conn, production_date: date) -> Workbook:
    lines, leaders = _collect(conn, production_date.isoformat())
    wb = Workbook()
    ws = wb.active
    ws.title = "Báo cáo"
    last_column = len(HEADINGS)
    ws["A1"] = TITLE
    ws["A2"] = f"No: {FORM_NO}"
    ws["A3"] = f"Ngày sản xuất: {production_date.strftime('%d/%m/%Y')}"

    row = 5
    for shift in SHIFTS:
        names = ", ".join(get_user_by_id(conn, user_id)["username"] for user_id in leaders[shift])
        _write_values(ws, row, [f"Ca sản xuất: {shift}", None, None, f"Trưởng ca: {names}"], bold=True)
        _band(ws, row, last_column)
        row += 1
        _write_values(ws, row, HEADINGS, bold=True)
        style.shade_heading(ws, row, row, last_column)
        ws.row_dimensions[row].height = 32
        row += 1
        first_line = row
        for machine in MACHINES + ((UNKNOWN_MACHINE,) if lines.get((shift, UNKNOWN_MACHINE)) else ()):
            section = lines.get((shift, machine))
            if not section:
                _write_values(ws, row, [machine])
                row += 1
                continue
            for key in sorted(section, key=lambda k: (k[0] != "form_a", k[1], k[2], k[3])):
                line = section[key]
                notes = [PENDING_NOTE] * line["unconfirmed"] + [UNREAD_NOTE] * line["unread"]
                notes += [UNCLEAR_MACHINE_NOTE.format(value) for value in line["unclear_machines"]]
                note = "; ".join(notes) or None
                values = [machine, key[1], key[2], key[3], round(line["net"], 3), None, line["rolls"], None, note]
                _write_values(ws, row, values)
                row += 1
        _frame_lines(ws, first_line, row - 1, last_column)
        row += 1

    all_lines = [(key, line) for section in lines.values() for key, line in section.items()]
    form_a_kg = round(sum(line["net"] for (layout, *_), line in all_lines if layout == "form_a"), 3)
    back_kg = round(sum(line["net"] for (layout, *_), line in all_lines if layout == "back"), 3)
    rolls = sum(line["rolls"] for _, line in all_lines)
    for label, value, column in (
        ("TỔNG KHỐI CUỘN MÀNG (KG)", form_a_kg, NET_COLUMN),
        ("TỔNG SỐ CUỘN", rolls, ROLLS_COLUMN),
        ("TỔNG SẢN LƯỢNG TÚI (KG)", back_kg, NET_COLUMN),
    ):
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        ws.cell(row=row, column=column, value=value)
        _total_row(ws, row, column, ROLLS_COLUMN)
        row += 1

    for index, width in enumerate(COLUMN_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    _finish(ws, last_column)
    return wb


def _band(ws, row: int, last_column: int) -> None:
    """The shift's title band: shift on the left, shift leaders on the right."""
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
    ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=last_column)
    for column in range(1, last_column + 1):
        cell = ws.cell(row=row, column=column)
        cell.fill = style.BAND_FILL
        cell.font = style.font(cell, bold=True, color="FFFFFF", size=11)
        cell.alignment = style.MIDDLE
    ws.row_dimensions[row].height = 22


def _frame_lines(ws, first: int, last: int, last_column: int) -> None:
    style.frame(ws, first, last, last_column)
    for row in ws.iter_rows(min_row=first, max_row=last, max_col=last_column):
        note = str(row[NOTE_COLUMN - 1].value or "")
        lines = max(1, -(-len(note) // (COLUMN_WIDTHS[NOTE_COLUMN - 1] - 2)))  # the note wraps; show it whole
        ws.row_dimensions[row[0].row].height = style.ROW_HEIGHT * lines
        for cell in row:
            cell.font = style.font(cell, bold=cell.column == 1)
            cell.alignment = style.CENTRE if cell.column in (1, NET_COLUMN, ROLLS_COLUMN) else style.MIDDLE
        row[NET_COLUMN - 1].number_format = style.KG_FORMAT
        # A thicker line between machines, so each machine's lines read as one group.
        below = ws.cell(row=row[0].row + 1, column=1).value
        if row[0].row == last or (below and below != row[0].value):
            for cell in row:
                cell.border = style.Border(left=style.THIN, right=style.THIN, top=cell.border.top, bottom=style.MEDIUM)


def _total_row(ws, row: int, value_column: int, last_column: int) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NET_COLUMN - 1)
    for column in range(1, last_column + 1):
        cell = ws.cell(row=row, column=column)
        cell.fill = style.HEADING_FILL
        cell.border = style.GRID
        cell.font = style.font(cell, bold=True, color=style.ACCENT)
        cell.alignment = style.MIDDLE
    value = ws.cell(row=row, column=value_column)
    value.alignment = style.CENTRE
    value.number_format = style.KG_FORMAT if value_column == NET_COLUMN else "0"


def _finish(ws, last_column: int) -> None:
    style.use_house_font(ws)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_column)
    ws["A1"].font = style.font(ws["A1"], size=16, bold=True, color=style.ACCENT)
    ws["A1"].alignment = style.CENTRE
    ws.row_dimensions[1].height = 30
    for cell in (ws["A2"], ws["A3"]):
        cell.font = style.font(cell, bold=True)
    style.print_setup(ws)
