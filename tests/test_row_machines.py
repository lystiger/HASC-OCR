"""Row machines of a Zalo sheet (zalo-intake spec R6, criterion 10): made-up plans and rows, a fake reader."""
from datetime import datetime, timedelta, timezone
import gc
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from hasc import plans as plans_module
from hasc.db import create_photo, create_read_job, create_sheet, get_connection, get_sheet_data
from hasc.intake import ZALO_FORM
from hasc.plans import Plan, PlanLine, Plans, assign_row_machines
from hasc.worker import jobs
from tests.test_plans import pdf_bytes
from tests.test_zalo_hygiene import forbid_sends
from tests.test_worker import FakeReader, result, row

VN = timezone(timedelta(hours=7))
START = datetime(2026, 10, 21, 7, 0, tzinfo=VN)


def plans_of(*plans):
    return Plans(tuple(plans), {})


def plan(file, when, *lines):
    return Plan(file, when, tuple(PlanLine(m, c, t, w) for m, c, t, w in lines))


PLANS = plans_of(plan("KHSX 20.10.pdf", datetime(2026, 10, 20, 15, 0, tzinfo=VN),
                      ("T1", "VinFast", 45, 1200), ("T6", "Song Sao", 70, 740)))


def product(customer="VinFast", mic=45, width=1200, **more):
    return row(customer=customer, thickness_mic=mic, width_mm=width, **more)


def machines(rows):
    return [(r["cells"]["machine"]["value"], r["cells"]["machine"].get("doubt"), r["cells"]["machine"].get("note"))
            for r in rows]


class AssignRowMachines(unittest.TestCase):
    def test_a_written_machine_is_kept_and_carried_down(self):
        rows = [product(machine="T6"), product(), product(machine=" t 1 "), product()]
        assign_row_machines(rows, PLANS, START)
        self.assertEqual(rows[0]["cells"]["machine"]["value"], "T6")
        self.assertEqual(rows[1]["cells"]["machine"], {"value": "T6", "inherited": True, "doubt": False})
        self.assertEqual(rows[2]["cells"]["machine"]["value"], " t 1 ")
        self.assertEqual(rows[3]["cells"]["machine"]["value"], "T1")

    def test_a_matched_row_gets_the_plan_machine_with_doubt_note_and_file(self):
        rows = [product()]
        assign_row_machines(rows, PLANS, START)
        self.assertEqual(machines(rows), [("T1", True, "theo kế hoạch: KHSX 20.10.pdf")])

    def test_an_unmatched_row_after_a_matched_one_stays_blank_and_flagged(self):
        rows = [product(), product("Khách lạ", 40, 800), product()]
        assign_row_machines(rows, PLANS, START)
        self.assertEqual(machines(rows), [("T1", True, "theo kế hoạch: KHSX 20.10.pdf"), (None, True, None),
                                          ("T1", True, "theo kế hoạch: KHSX 20.10.pdf")])

    def test_a_plan_machine_is_never_carried_but_a_written_one_beats_the_plan_below_it(self):
        rows = [product(), product(machine="T6"), product()]
        assign_row_machines(rows, PLANS, START)
        self.assertEqual([m[0] for m in machines(rows)], ["T1", "T6", "T6"])
        self.assertTrue(rows[2]["cells"]["machine"]["inherited"])

    def test_an_unreadable_machine_cell_is_not_written(self):
        rows = [{"cells": {**product()["cells"], "machine": {"value": None, "unreadable": True}}}]
        assign_row_machines(rows, PLANS, START)
        self.assertEqual(rows[0]["cells"]["machine"]["value"], "T1")

    def test_a_written_machine_that_is_not_a_machine_stays_and_is_not_carried(self):
        rows = [product(machine="xx"), product()]
        assign_row_machines(rows, PLANS, START)
        self.assertEqual(rows[0]["cells"]["machine"]["value"], "xx")
        self.assertEqual(rows[1]["cells"]["machine"]["value"], "T1")

    def test_a_plan_updated_at_exactly_the_shift_start_does_not_count(self):
        late = plans_of(plan("KHSX 21.10.pdf", START, ("T1", "VinFast", 45, 1200)))
        rows = [product()]
        assign_row_machines(rows, late, START)
        self.assertEqual(machines(rows), [(None, True, None)])
        rows = [product()]
        assign_row_machines(rows, late, START + timedelta(minutes=1))
        self.assertEqual(rows[0]["cells"]["machine"]["value"], "T1")

    def test_a_row_without_thickness_or_width_stays_blank(self):
        rows = [row(customer="VinFast")]
        assign_row_machines(rows, PLANS, START)
        self.assertEqual(machines(rows), [(None, True, None)])

    def test_numbers_read_as_text_floats_or_commas_still_match(self):
        rows = [product(mic="45", width=1200.0), product(mic="45,0", width="1200")]
        assign_row_machines(rows, PLANS, START)
        self.assertEqual([m[0] for m in machines(rows)], ["T1", "T1"])


class ZaloSheetRead(unittest.TestCase):
    """The worker sets row machines for a Zalo sheet after the read, and leaves a website sheet alone."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.conn = get_connection(root / "hasc.db")
        self.addCleanup(gc.collect)
        self.addCleanup(self.conn.close)
        self.scratch = root / "scratch"
        self.scratch.mkdir()
        self.plan_dir = root / "plans"
        self.plan_dir.mkdir()
        self.root = root
        plans_module._cache.clear()
        self.addCleanup(plans_module._cache.clear)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        os.environ.pop("HASC_KNOWN_NAMES", None)
        os.environ.pop("HASC_WORKER_ENV", None)
        self.addCleanup(env.stop)
        forbid_sends(self)

    def queue(self, machine, form=ZALO_FORM):
        sheet_id = create_sheet(self.conn, form=form, machine=machine, production_date="2026-10-21", shift=1)
        out = io.BytesIO()
        Image.new("RGB", (200, 100), "white").save(out, "JPEG")
        path = self.root / f"photo{machine or 'z'}.jpg"
        path.write_bytes(out.getvalue())
        photo_id = create_photo(self.conn, sheet_id=sheet_id, path=str(path), sha256=f"hash{machine}")
        create_read_job(self.conn, photo_id=photo_id)
        return sheet_id

    def read(self, sheet_id, *rows, plan_dir="default"):
        reader = FakeReader(result(*rows))
        jobs.run_pending(self.conn, reader, self.scratch, plan_dir=self.plan_dir if plan_dir == "default" else plan_dir)
        return get_sheet_data(self.conn, sheet_id)

    def write_plan(self, name="KHSX 20.10.pdf", *lines):
        (self.plan_dir / name).write_bytes(pdf_bytes(["Ngay cap nhat: 20/10/2026 15:00", *lines]))

    def test_rows_of_a_zalo_sheet_get_their_machines_with_flag_and_note(self):
        self.write_plan("KHSX 20.10.pdf", "T1   VinFast   21.10   PE   thêm  45  1200   cuon")
        sheet_id = self.queue("")
        data = self.read(sheet_id, product(), product("Khách lạ", 40, 800), product(machine="T6"))
        cells = [r["cells"]["machine"] for r in data]
        self.assertEqual([c["value"] for c in cells], ["T1", None, "T6"])
        self.assertEqual(cells[0]["flag"], "doubt")
        self.assertEqual(cells[0]["note"], "theo kế hoạch: KHSX 20.10.pdf")
        self.assertEqual(cells[1]["flag"], "doubt")
        self.assertEqual(cells[2]["flag"], "none")

    def test_a_broken_newest_plan_leaves_the_older_plan_in_use_and_still_flagged(self):
        self.write_plan("KHSX 20.10.pdf", "T1   VinFast   21.10   PE   thêm  45  1200   cuon")
        (self.plan_dir / "KHSX 21.10.pdf").write_bytes(b"not a pdf")
        data = self.read(self.queue(""), product())
        cell = data[0]["cells"]["machine"]
        self.assertEqual((cell["value"], cell["flag"], cell["note"]), ("T1", "doubt", "theo kế hoạch: KHSX 20.10.pdf"))

    def test_no_plan_folder_leaves_every_row_blank_and_flagged(self):
        data = self.read(self.queue(""), product(), plan_dir=None)
        self.assertEqual((data[0]["cells"]["machine"]["value"], data[0]["cells"]["machine"]["flag"]), (None, "doubt"))

    def test_a_website_sheet_is_left_alone(self):
        self.write_plan("KHSX 20.10.pdf", "T1   VinFast   21.10   PE   thêm  45  1200   cuon")
        data = self.read(self.queue("T6", form=ZALO_FORM), product())
        self.assertNotIn("machine", data[0]["cells"])


if __name__ == "__main__":
    unittest.main()
