from datetime import date
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from hasc.excel import build_page_workbook, page_filename
from hasc.excel.page import FIRST_DATA_ROW, META_TAB

SHEET = {
    "sheet_id": 7,
    "version": 3,
    "form": "TEST01-0000/HASC",
    "machine": "T9",
    "production_date": date(2026, 1, 2),
    "shift": 1,
    "page_no": 2,
}


def row(row_key, net, check=None, **extra):
    cells = {
        "machine": {"value": "T9"},
        "roll_code": {"value": row_key},
        "gross_kg": {"value": net + 1.5},
        "core_kg": {"value": 1.0},
        "net_kg": {"value": net},
    }
    cells.update(extra)
    return {"row_key": row_key, "cells": cells, "check": check}


class PageWorkbook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        rows = [
            row("1", 10.5, customer={"value": "Alpha", "inherited": True}),
            row("2", 20.25, core_kg={"value": 1.0, "flag": "doubt"}),
            row("3", 7.0, net_kg={"value": 7.0, "flag": "rule"}, gross_kg={"value": 9.0, "flag": "unreadable"}),
            row("4", 4.25, check="Lệch: 4.5"),
        ]
        rows[3]["cells"]["roll_code"]["flag"] = "disputed"
        rows[0]["cells"]["roll_code"]["value"] = "=SUM(A1:A2)"
        path = Path(self.tmp.name) / page_filename("T9", SHEET["production_date"], 1, 2)
        build_page_workbook(SHEET, rows).save(path)
        self.path = path
        self.wb = load_workbook(path)
        self.ws = self.wb["Báo cáo"]

    def data_cell(self, data_row, column):
        return self.ws.cell(row=FIRST_DATA_ROW + data_row, column=column)

    def test_filename_matches_section_6(self):
        self.assertEqual(self.path.name, "HASC_T9_2026-01-02_Ca1_Trang2.xlsx")

    def test_net_weight_sums_to_the_written_values(self):
        net_column = 13
        total = sum(self.data_cell(i, net_column).value for i in range(4))
        self.assertAlmostEqual(total, 10.5 + 20.25 + 7.0 + 4.25)

    def test_numbers_are_numbers_and_text_is_text(self):
        for i in range(4):
            self.assertIsInstance(self.data_cell(i, 13).value, int | float)
            self.assertIsInstance(self.data_cell(i, 10).value, int | float)
        self.assertIsInstance(self.data_cell(1, 4).value, str)
        self.assertEqual(self.data_cell(1, 4).value, "2")

    def test_formula_looking_text_stays_text(self):
        cell = self.data_cell(0, 4)
        self.assertEqual(cell.value, "=SUM(A1:A2)")
        self.assertEqual(cell.data_type, "s")

    def test_flags_colour_cells(self):
        self.assertEqual(self.data_cell(1, 11).fill.start_color.rgb, "00FFFF00")
        self.assertEqual(self.data_cell(2, 13).fill.start_color.rgb, "00FF0000")
        self.assertEqual(self.data_cell(2, 10).fill.start_color.rgb, "00BFBFBF")
        self.assertEqual(self.data_cell(3, 4).fill.start_color.rgb, "00FFFF00")
        self.assertIsNone(self.data_cell(0, 13).fill.fill_type)

    def test_key_legend_is_coloured_and_labelled(self):
        self.assertEqual(self.ws["A4"].value, "Kí hiệu:")
        self.assertEqual(self.ws["B4"].value, "vàng = AI chưa chắc")
        self.assertEqual(self.ws["B4"].fill.start_color.rgb, "00FFFF00")
        self.assertEqual(self.ws["C4"].value, "đỏ = sai quy tắc/công thức/tiêu chuẩn")
        self.assertEqual(self.ws["C4"].fill.start_color.rgb, "00FF0000")
        self.assertEqual(self.ws["D4"].value, "xám = không đọc được")
        self.assertEqual(self.ws["D4"].fill.start_color.rgb, "00BFBFBF")

    def test_prints_on_a4_landscape_one_page_wide_with_repeated_headings(self):
        setup = self.ws.page_setup
        self.assertEqual((setup.orientation, setup.fitToWidth, setup.fitToHeight), ("landscape", 1, 0))
        self.assertEqual(self.ws.print_title_rows, "$5:$6")
        self.assertEqual(self.ws["A5"].font.name, "Arial")
        self.assertEqual(self.data_cell(0, 13).border.left.style, "thin")

    def test_column_widths(self):
        self.assertEqual(self.ws.column_dimensions["B"].width, 22)
        self.assertEqual(self.ws.column_dimensions["C"].width, 36)
        self.assertEqual(self.ws.column_dimensions["D"].width, 22)

    def test_inherited_cell_shows_value_in_grey_font_and_check_text_written(self):
        self.assertEqual(self.data_cell(0, 2).value, "Alpha")
        self.assertEqual(self.data_cell(0, 2).font.color.rgb, "00808080")
        self.assertEqual(self.data_cell(1, 2).value, "Alpha")
        self.assertEqual(self.data_cell(1, 2).font.color.rgb, "00808080")
        self.assertEqual(self.data_cell(3, 23).value, "Lệch: 4.5")

    def test_paper_values_have_black_font(self):
        self.assertEqual(self.data_cell(0, 1).font.color.rgb, "00000000")
        self.assertEqual(self.data_cell(1, 4).font.color.rgb, "00000000")
        self.assertEqual(self.data_cell(1, 13).font.color.rgb, "00000000")

    def test_all_seven_carried_fields_shown_on_every_row_with_numeric_types(self):
        carried_rows = [
            row(
                "101",
                15.0,
                customer={"value": "Cty Alpha"},
                film_type={"value": "Màng PE trắng"},
                pellet_formula={"value": "CT-A1"},
                mixing_shift_date={"value": "1/19-10"},
                thickness_mic={"value": 50},
                width_mm={"value": 650},
                length_mm={"value": 1500},
            ),
            row("102", 16.5),
            row(
                "103",
                14.0,
                customer={"value": "Cty Beta"},
                length_mm={"value": 2000},
            ),
            row("104", 17.0),
        ]
        wb = build_page_workbook(SHEET, carried_rows)
        ws = wb["Báo cáo"]

        def cell(r, c):
            return ws.cell(row=FIRST_DATA_ROW + r, column=c)

        # Row 0: paper values in black
        self.assertEqual(cell(0, 2).value, "Cty Alpha")
        self.assertEqual(cell(0, 2).font.color.rgb, "00000000")
        self.assertEqual(cell(0, 3).value, "Màng PE trắng")
        self.assertEqual(cell(0, 3).font.color.rgb, "00000000")
        self.assertEqual(cell(0, 5).value, "CT-A1")
        self.assertEqual(cell(0, 5).font.color.rgb, "00000000")
        self.assertEqual(cell(0, 6).value, "1/19-10")
        self.assertEqual(cell(0, 6).font.color.rgb, "00000000")
        self.assertEqual(cell(0, 7).value, 50)
        self.assertIsInstance(cell(0, 7).value, int | float)
        self.assertEqual(cell(0, 7).font.color.rgb, "00000000")
        self.assertEqual(cell(0, 8).value, 650)
        self.assertIsInstance(cell(0, 8).value, int | float)
        self.assertEqual(cell(0, 8).font.color.rgb, "00000000")
        self.assertEqual(cell(0, 9).value, 1500)
        self.assertIsInstance(cell(0, 9).value, int | float)
        self.assertEqual(cell(0, 9).font.color.rgb, "00000000")

        # Row 1: all 7 carried fields inherited from row 0 in grey
        self.assertEqual(cell(1, 2).value, "Cty Alpha")
        self.assertEqual(cell(1, 2).font.color.rgb, "00808080")
        self.assertEqual(cell(1, 3).value, "Màng PE trắng")
        self.assertEqual(cell(1, 3).font.color.rgb, "00808080")
        self.assertEqual(cell(1, 5).value, "CT-A1")
        self.assertEqual(cell(1, 5).font.color.rgb, "00808080")
        self.assertEqual(cell(1, 6).value, "1/19-10")
        self.assertEqual(cell(1, 6).font.color.rgb, "00808080")
        self.assertEqual(cell(1, 7).value, 50)
        self.assertIsInstance(cell(1, 7).value, int | float)
        self.assertEqual(cell(1, 7).font.color.rgb, "00808080")
        self.assertEqual(cell(1, 8).value, 650)
        self.assertIsInstance(cell(1, 8).value, int | float)
        self.assertEqual(cell(1, 8).font.color.rgb, "00808080")
        self.assertEqual(cell(1, 9).value, 1500)
        self.assertIsInstance(cell(1, 9).value, int | float)
        self.assertEqual(cell(1, 9).font.color.rgb, "00808080")

        # Row 2: customer & length are paper (black); others inherited (grey)
        self.assertEqual(cell(2, 2).value, "Cty Beta")
        self.assertEqual(cell(2, 2).font.color.rgb, "00000000")
        self.assertEqual(cell(2, 3).value, "Màng PE trắng")
        self.assertEqual(cell(2, 3).font.color.rgb, "00808080")
        self.assertEqual(cell(2, 5).value, "CT-A1")
        self.assertEqual(cell(2, 5).font.color.rgb, "00808080")
        self.assertEqual(cell(2, 6).value, "1/19-10")
        self.assertEqual(cell(2, 6).font.color.rgb, "00808080")
        self.assertEqual(cell(2, 7).value, 50)
        self.assertIsInstance(cell(2, 7).value, int | float)
        self.assertEqual(cell(2, 7).font.color.rgb, "00808080")
        self.assertEqual(cell(2, 8).value, 650)
        self.assertIsInstance(cell(2, 8).value, int | float)
        self.assertEqual(cell(2, 8).font.color.rgb, "00808080")
        self.assertEqual(cell(2, 9).value, 2000)
        self.assertIsInstance(cell(2, 9).value, int | float)
        self.assertEqual(cell(2, 9).font.color.rgb, "00000000")

        # Row 3: customer & length inherited from row 2; width from row 0
        self.assertEqual(cell(3, 2).value, "Cty Beta")
        self.assertEqual(cell(3, 2).font.color.rgb, "00808080")
        self.assertEqual(cell(3, 9).value, 2000)
        self.assertIsInstance(cell(3, 9).value, int | float)
        self.assertEqual(cell(3, 9).font.color.rgb, "00808080")
        self.assertEqual(cell(3, 8).value, 650)
        self.assertIsInstance(cell(3, 8).value, int | float)
        self.assertEqual(cell(3, 8).font.color.rgb, "00808080")

    def test_heading_row_is_frozen(self):
        self.assertEqual(self.ws.freeze_panes, "A7")

    def test_headings_and_group_merges(self):
        self.assertEqual(self.ws["J5"].value, "Khối lượng Kg")
        self.assertEqual(self.ws["M6"].value, "Sau trừ bì")
        self.assertIn("J5:M5", [str(r) for r in self.ws.merged_cells.ranges])
        self.assertEqual(self.ws["A5"].value, "Máy")

    def test_meta_tab_is_hidden_and_holds_ids(self):
        meta = self.wb[META_TAB]
        self.assertEqual(meta.sheet_state, "hidden")
        values = {meta.cell(row=r, column=1).value: meta.cell(row=r, column=2).value for r in range(2, 6)}
        self.assertEqual(values["sheet_id"], 7)
        self.assertEqual(values["version"], 3)
        self.assertEqual(values["row_keys"], "1,2,3,4")


if __name__ == "__main__":
    unittest.main()
