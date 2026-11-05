from datetime import date
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from hasc.db import (
    create_photo,
    create_row,
    create_sheet,
    create_user,
    get_connection,
    get_rows_for_sheet,
    mark_photo_invalid,
    upsert_cell,
)
from hasc.excel import build_daily_report, daily_report_filename
from hasc.excel.daily_report import HEADINGS, MACHINES, PENDING_NOTE, UNREAD_NOTE

DAY = date(2026, 12, 1)
FORM = "BMMT01-2022/HASC"


class DailyReport(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.conn = get_connection(self.root / "hasc.db")
        self.addCleanup(self.conn.close)
        self.an = create_user(self.conn, "an", "hash", "operator")
        self.binh = create_user(self.conn, "binh", "hash", "operator")
        self.photos = 0

    def sheet(self, machine, shift, uploader, status="reviewed", layout="form_a", page_no=1):
        sheet_id = create_sheet(self.conn, form=FORM, machine=machine, production_date=DAY.isoformat(),
                                shift=shift, page_no=page_no, status=status)
        self.conn.execute("UPDATE sheets SET layout = ? WHERE id = ?", (layout, sheet_id))
        self.photos += 1
        photo_id = create_photo(self.conn, sheet_id=sheet_id, path=f"p{self.photos}.jpg",
                                sha256=f"sha{self.photos}", uploaded_by=uploader)
        return sheet_id, photo_id

    def roll(self, sheet_id, photo_id, net, customer, film_type, thickness, width, length=None, invalid=False):
        position = len(get_rows_for_sheet(self.conn, sheet_id, include_invalid=True)) + 1
        row_id = create_row(self.conn, sheet_id, str(position), position, invalid=int(invalid))
        details = {"customer": customer, "film_type": film_type, "thickness_mic": thickness,
                   "width_mm": width, "length_mm": length, "net_kg": net}
        for field, value in details.items():
            if value is not None:
                upsert_cell(self.conn, row_id, field, str(value), source_photo_id=photo_id)

    def build(self):
        path = self.root / "report.xlsx"
        build_daily_report(self.conn, DAY).save(path)
        return load_workbook(path)["Báo cáo"]

    def block(self, ws, shift):
        """Rows from the shift's header down to the blank row that ends the block."""
        start = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == f"Ca sản xuất: {shift}")
        end = next(r for r in range(start + 1, ws.max_row + 2) if ws.cell(r, 1).value is None)
        return [[ws.cell(r, c).value for c in range(1, 11)] for r in range(start, end)]

    def seed(self):
        # Ca 1, T1, reviewed: two customers on one sheet; Alpha's four rolls sum to 345.
        t1, t1_photo = self.sheet("T1", 1, self.an)
        for net in (86.4, 86.6, 86, 86):
            self.roll(t1, t1_photo, net, "Alpha", "GT", 40, 500, 1500)
        self.roll(t1, t1_photo, 10.5, "Beta", "PE", 30, 600, 1200)
        self.roll(t1, t1_photo, 20.25, "Beta", "PE", 30, 600, 1200)
        # Ca 1, T3, back side, not reviewed.
        t3, t3_photo = self.sheet("T3", 1, self.an, status="needs_review", layout="back")
        self.roll(t3, t3_photo, 50, "Gamma", "TX", 30, 400)
        # Ca 2, T6, reviewed.
        t6, t6_photo = self.sheet("T6", 2, self.binh)
        self.roll(t6, t6_photo, 100, "Alpha", "GT", 40, 500, 1500)
        self.roll(t6, t6_photo, 25, "Alpha", "GT", 40, 500, 1500)

    def test_unreadable_net_weight_is_left_out_and_noted(self):
        t1, photo = self.sheet("T1", 1, self.an)
        self.roll(t1, photo, 86.4, "Alpha", "GT", 40, 500, 1500)
        self.roll(t1, photo, "8?6", "Alpha", "GT", 40, 500, 1500)
        line = next(r for r in self.block(self.build(), 1) if r[1] == "Alpha")
        self.assertEqual(line[4], 86.4)
        self.assertEqual(line[8], UNREAD_NOTE)

    def test_net_weight_marked_unreadable_by_the_reader_is_noted(self):
        t1, photo = self.sheet("T1", 1, self.an)
        self.roll(t1, photo, 86.4, "Alpha", "GT", 40, 500, 1500)
        self.roll(t1, photo, None, "Alpha", "GT", 40, 500, 1500)
        row_id = get_rows_for_sheet(self.conn, t1)[-1]["id"]
        upsert_cell(self.conn, row_id, "net_kg", None, source_photo_id=photo, flag="unreadable")
        line = next(r for r in self.block(self.build(), 1) if r[1] == "Alpha")
        self.assertEqual((line[4], line[8]), (86.4, UNREAD_NOTE))

    def test_a_long_note_gets_a_taller_row(self):
        t1, photo = self.sheet("T1", 1, self.an, status="needs_review")
        self.roll(t1, photo, "8?6", "Alpha", "GT", 40, 500, 1500)
        ws = self.build()
        row = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 2).value == "Alpha")
        self.assertGreater(ws.row_dimensions[row].height, 18)

    def test_title_date_and_shift_blocks(self):
        self.seed()
        ws = self.build()
        self.assertEqual(ws["A1"].value, "BÁO CÁO SẢN XUẤT NGÀY")
        self.assertEqual(ws["A2"].value, "No: HASC281224")
        self.assertEqual(ws["A3"].value, "Ngày sản xuất: 01/12/2026")
        ca1 = self.block(ws, 1)
        ca2 = self.block(ws, 2)
        self.assertEqual(ca1[0][0], "Ca sản xuất: 1")
        self.assertEqual(ca1[0][3], "Trưởng ca: an")
        self.assertEqual(ca2[0][3], "Trưởng ca: binh")
        self.assertEqual(ca1[1][:10], list(HEADINGS))

    def test_machine_sections_in_order_and_empty_ones_still_printed(self):
        self.seed()
        ws = self.build()
        machines = [line[0] for line in self.block(ws, 1)[2:]]
        self.assertEqual(machines, ["T1", "T1", "T6", "T2", "T3", "M1", "M2", "M3"])
        ca2_machines = [line[0] for line in self.block(ws, 2)[2:]]
        self.assertEqual(ca2_machines, list(MACHINES))

    def test_one_line_per_customer_and_product_with_summed_weight(self):
        self.seed()
        ws = self.build()
        ca1 = self.block(ws, 1)
        alpha = next(line for line in ca1 if line[1] == "Alpha")
        beta = next(line for line in ca1 if line[1] == "Beta")
        self.assertEqual(alpha[:4], ["T1", "Alpha", "GT", "40x500x1500"])
        self.assertEqual(alpha[4], 345)
        self.assertEqual(alpha[6], 4)
        self.assertEqual(beta[:4], ["T1", "Beta", "PE", "30x600x1200"])
        self.assertAlmostEqual(beta[4], 30.75)
        self.assertEqual(beta[6], 2)

    def test_back_layout_product_code_and_its_own_line(self):
        self.seed()
        ws = self.build()
        gamma = next(line for line in self.block(ws, 1) if line[1] == "Gamma")
        self.assertEqual(gamma[:4], ["T3", "Gamma", "TX", "30mic x R400"])
        self.assertEqual(gamma[4], 50)

    def test_unreviewed_sheet_is_marked_and_office_columns_stay_blank(self):
        self.seed()
        ws = self.build()
        ca1 = self.block(ws, 1)
        gamma = next(line for line in ca1 if line[1] == "Gamma")
        alpha = next(line for line in ca1 if line[1] == "Alpha")
        self.assertEqual(gamma[8], PENDING_NOTE)
        self.assertIsNone(alpha[8])
        for line in (alpha, gamma):
            self.assertIsNone(line[5])
            self.assertIsNone(line[7])
            self.assertIsNone(line[9])

    def test_numbers_are_real_numbers(self):
        self.seed()
        ws = self.build()
        for line in self.block(ws, 1)[2:] + self.block(ws, 2)[2:]:
            if line[4] is not None:
                self.assertIsInstance(line[4], int | float)
        self.assertIsInstance(ws.cell(row=ws.max_row, column=5).value, int | float)

    def test_totals_at_the_bottom(self):
        self.seed()
        ws = self.build()
        totals = {ws.cell(r, 1).value: r for r in range(1, ws.max_row + 1) if str(ws.cell(r, 1).value).startswith("TỔNG")}
        self.assertEqual(set(totals), {"TỔNG KHỐI CUỘN MÀNG (KG)", "TỔNG SỐ CUỘN", "TỔNG SẢN LƯỢNG TÚI (KG)"})
        # Form A kg: 345 (T1 Ca 1) + 30.75 (T1 Ca 1) + 125 (T6 Ca 2). Rolls: 4 + 2 + 2 form A, 1 back.
        # Back side kg: 50.
        self.assertAlmostEqual(ws.cell(totals["TỔNG KHỐI CUỘN MÀNG (KG)"], 5).value, 500.75)
        self.assertEqual(ws.cell(totals["TỔNG SỐ CUỘN"], 7).value, 9)
        self.assertEqual(ws.cell(totals["TỔNG SẢN LƯỢNG TÚI (KG)"], 5).value, 50)

    def test_invalid_rows_and_invalid_photos_are_left_out(self):
        self.seed()
        t1 = self.conn.execute("SELECT id FROM sheets WHERE machine = 'T1' AND shift = 1").fetchone()["id"]
        t1_photo = self.conn.execute("SELECT id FROM photos WHERE sheet_id = ?", (t1,)).fetchone()["id"]
        self.roll(t1, t1_photo, 999, "Alpha", "GT", 40, 500, 1500, invalid=True)
        bad, bad_photo = self.sheet("M2", 1, self.an)
        self.roll(bad, bad_photo, 500, "Delta", "GT", 20, 300)
        mark_photo_invalid(self.conn, bad_photo, self.an)
        ws = self.build()
        self.assertEqual(next(line for line in self.block(ws, 1) if line[1] == "Alpha")[4], 345)
        self.assertEqual([line for line in self.block(ws, 1) if line[0] == "M2"], [["M2"] + [None] * 9])

    def test_empty_day_prints_every_section_and_zero_totals(self):
        ws = self.build()
        self.assertEqual(ws.cell(row=ws.max_row - 2, column=5).value, 0)
        self.assertEqual(ws.cell(row=ws.max_row - 1, column=7).value, 0)
        self.assertEqual(ws.cell(row=ws.max_row, column=5).value, 0)
        self.assertEqual([line[0] for line in self.block(ws, 1)[2:]], list(MACHINES))

    def test_rolls_86_4_86_6_86_86_sum_to_345(self):
        sheet_id, photo_id = self.sheet("T1", 1, self.an)
        for net in (86.4, 86.6, 86, 86):
            self.roll(sheet_id, photo_id, net, "Alpha", "GT", 40, 500, 1500)
        ws = self.build()
        line = next(line for line in self.block(ws, 1) if line[1] == "Alpha")
        self.assertEqual(line[4], 345)
        self.assertEqual(line[6], 4)

    def test_continuation_rows_carry_product_details_and_sum_weights(self):
        sheet_id, photo_id = self.sheet("T1", 1, self.an)
        self.roll(sheet_id, photo_id, 10, "Alpha", "GT", 40, 500, 1500)
        self.roll(sheet_id, photo_id, 20, None, None, None, None, None)
        ws = self.build()
        lines = [line for line in self.block(ws, 1)[2:] if line[0] == "T1" and line[1] is not None]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0][:4], ["T1", "Alpha", "GT", "40x500x1500"])
        self.assertEqual(lines[0][4], 30)
        self.assertEqual(lines[0][6], 2)

    def test_continuation_rows_on_back_layout(self):
        sheet_id, photo_id = self.sheet("T3", 1, self.an, layout="back")
        self.roll(sheet_id, photo_id, 15, "Gamma", "TX", 30, 400)
        self.roll(sheet_id, photo_id, 25, None, None, None, None)
        ws = self.build()
        lines = [line for line in self.block(ws, 1)[2:] if line[0] == "T3" and line[1] is not None]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0][:4], ["T3", "Gamma", "TX", "30mic x R400"])
        self.assertEqual(lines[0][4], 40)
        self.assertEqual(lines[0][6], 2)

    def test_filename(self):
        self.assertEqual(daily_report_filename(DAY), "HASC_BaoCaoNgay_2026-12-01.xlsx")


if __name__ == "__main__":
    unittest.main()
