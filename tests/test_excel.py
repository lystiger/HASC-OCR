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

    def test_key_legend_is_coloured(self):
        self.assertEqual(self.ws["B4"].fill.start_color.rgb, "00FFFF00")
        self.assertEqual(self.ws["C4"].fill.start_color.rgb, "00FF0000")
        self.assertEqual(self.ws["D4"].fill.start_color.rgb, "00BFBFBF")

    def test_inherited_cell_is_blank_and_check_text_written(self):
        self.assertIsNone(self.data_cell(0, 2).value)
        self.assertEqual(self.data_cell(3, 23).value, "Lệch: 4.5")

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
