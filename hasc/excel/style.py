"""Shared look of the .xlsx files: one font, framed tables, a shaded heading, A4 landscape on one page wide.

Styling never changes a value or a flag colour: cell fills stay reserved for flags (spec section 6), so the
tables are framed with borders and only the headings are shaded.
"""

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.page import PageMargins

FONT_NAME = "Arial"
FONT_SIZE = 10
ACCENT = "516572"  # the website's slate accent
HEADING_FILL = PatternFill(fill_type="solid", start_color="E4E9EC", end_color="E4E9EC")
BAND_FILL = PatternFill(fill_type="solid", start_color=ACCENT, end_color=ACCENT)
THIN = Side(style="thin", color="A6A6A6")
MEDIUM = Side(style="medium", color=ACCENT)
GRID = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTRE = Alignment(horizontal="center", vertical="center", wrap_text=True)
MIDDLE = Alignment(vertical="center", wrap_text=True, indent=1)
ROW_HEIGHT = 18
KG_FORMAT = "#,##0.0"


def font(cell, **changes) -> Font:
    """The cell's font in the house face, keeping its colour and weight unless changed."""
    current = cell.font
    values = {"name": FONT_NAME, "size": FONT_SIZE, "bold": current.bold, "color": current.color}
    values.update(changes)
    return Font(**values)


def use_house_font(ws) -> None:
    for row in ws.iter_rows():
        for cell in row:
            if cell.font.name != FONT_NAME or cell.font.size != FONT_SIZE:
                cell.font = font(cell, size=cell.font.size if cell.font.size and cell.font.size > FONT_SIZE + 1 else FONT_SIZE)


def frame(ws, min_row: int, max_row: int, max_col: int, min_col: int = 1) -> None:
    for row in ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
        for cell in row:
            cell.border = GRID


def shade_heading(ws, min_row: int, max_row: int, max_col: int, min_col: int = 1) -> None:
    for row in ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
        for cell in row:
            cell.fill = HEADING_FILL
            cell.font = font(cell, bold=True, color=ACCENT)
            cell.alignment = CENTRE
            cell.border = GRID


def print_setup(ws, title_rows: str | None = None) -> None:
    """A4 landscape, fitted to one page wide, headings repeated on every printed page."""
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins = PageMargins(left=0.4, right=0.4, top=0.5, bottom=0.5)
    ws.print_options.horizontalCentered = True
    if title_rows:
        ws.print_title_rows = title_rows
    ws.sheet_view.showGridLines = False
