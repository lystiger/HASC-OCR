"""Production-plan parser (R6): made-up plans only, no real files, no network."""
import json
import logging
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from hasc import plans as plans_module
from hasc.plans import PlanMatch, load_plans, machine_for, plan_match

VN = timezone(timedelta(hours=7))
NAMES = {"customers": {"VinFast": ["VINFAST", "Vin Fast"], "Song Sao": ["SS"]}, "products": {}}


def shift(day, hour=6, minute=0, month=10):
    return datetime(2026, month, day, hour, minute, tzinfo=VN)


def plan_text(updated, *lines):
    head = f"Ngay cap nhat: {updated}\n" if updated else ""
    return head + "\n".join(lines)


def line(machine, customer, mic, width, core="thêm", received="14.10"):
    return f"{machine}     {customer}      {received}     PE trong   {core}   {mic}   {width}    cuộn   ghi chu"


def pdf_bytes(lines):
    """A one-page PDF in Helvetica with WinAnsiEncoding; one text line per Td move."""
    stream = "BT /F1 9 Tf 20 800 Td 11 TL\n" + "T*\n".join(
        "(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj\n" for text in lines) + "ET"
    body = stream.encode("cp1252")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)


class PlanFiles(unittest.TestCase):
    def setUp(self):
        plans_module._cache.clear()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.texts = {}
        patcher = mock.patch.object(plans_module, "_pdf_text", self.fake_text)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_text(self, path):
        value = self.texts[Path(path).name]
        if isinstance(value, Exception):
            raise value
        return value

    def add(self, name, text, mtime=None):
        self.texts[name] = text
        path = self.root / name
        path.write_bytes(b"%PDF-fake")
        if mtime:
            os.utime(path, ns=(mtime, mtime))
        return path

    def load(self):
        return load_plans(self.root, NAMES)

    def machine(self, when, customer="VinFast", mic=45, width=1200):
        return machine_for(self.load(), when, customer, mic, width)


class TestRealPdf(unittest.TestCase):
    def setUp(self):
        plans_module._cache.clear()
        self.addCleanup(plans_module._cache.clear)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)

    def test_pypdf_reads_a_generated_pdf_and_the_machine_resolves(self):
        (self.root / "KHSX 14.10.pdf").write_bytes(pdf_bytes([
            "Ngay cap nhat: 13/10/2026 15:00",
            "T1   VINFAST   14.10   PE   thêm  45  1200   cuon",
            "T2   Khac Hang   14.10   PE   thêm  50  900   cuon",
        ]))
        (self.root / "KHSX SONG SAO 14.10.pdf").write_bytes(pdf_bytes([
            "Cap nhat: 13/10/2026 16:30",
            "T6  CT0123  70  740",
            "T6  CT0456  70  900",
        ]))
        plans = load_plans(self.root, NAMES)
        self.assertEqual(len(plans.plans), 2)
        start = shift(14)
        self.assertEqual(machine_for(plans, start, "VinFast", 45, 1200), "T1")
        self.assertEqual(machine_for(plans, start, "Song Sao", 70, 740), "T6")
        self.assertEqual(machine_for(plans, start, "Song Sao", 70, 900), "T6")
        self.assertEqual(plan_match(plans, start, "Song Sao", 70, 740), PlanMatch("T6", "KHSX SONG SAO 14.10.pdf"))
        self.assertEqual(machine_for(plans, start, "VinFast", 45, 1000), None)
        self.assertEqual(plans.plans[0].time, datetime(2026, 10, 13, 15, 0, tzinfo=VN))


class TestPlanRules(PlanFiles):
    def test_line_shape_customer_variants_and_core_words(self):
        self.add("KHSX 14.10.pdf", plan_text(
            "13/10/2026 15:00",
            line("T1", "vin   fast", 45, 1200),
            line("T2", "Khách Lạ", 50, 900, core="giấy"),
            line("T3", "VINFAST", 38, 1000, core="Nhựa"),
            "T4   VINFAST   14.10   PE   khác   40   800",
            "ghi chu T5 VINFAST 14.10 thêm 41 801",
        ))
        start = shift(14)
        self.assertEqual(self.machine(start, "VINFAST", 45, 1200), "T1")
        self.assertEqual(self.machine(start, "Khách Lạ", 50, 900), "T2")
        self.assertEqual(self.machine(start, "khách  lạ", 50, 900), "T2")
        self.assertEqual(self.machine(start, "Vin Fast", 38, 1000), "T3")
        self.assertIsNone(self.machine(start, "VinFast", 40, 800))
        self.assertIsNone(self.machine(start, "VinFast", 41, 801))

    def test_comma_received_date_and_missing_fields(self):
        self.add("KHSX 14.10.pdf", plan_text("13/10/2026 15:00", line("T1", "VinFast", 45, 1200, received="14,10")))
        start = shift(14)
        self.assertEqual(self.machine(start), "T1")
        for args in ((None, 45, 1200), ("VinFast", None, 1200), ("VinFast", 45, None), ("", 45, 1200)):
            self.assertIsNone(machine_for(self.load(), start, *args))

    def test_plan_match_returns_the_file_name_for_the_note(self):
        self.add("KHSX 14.10.pdf", plan_text("13/10/2026 15:00", line("T1", "VinFast", 45, 1200)))
        self.assertEqual(plan_match(self.load(), shift(14), "VinFast", 45, 1200), PlanMatch("T1", "KHSX 14.10.pdf"))
        self.assertIsNone(plan_match(self.load(), shift(14), "VinFast", 45, 999))

    def test_plan_updated_at_the_shift_start_does_not_count(self):
        self.add("KHSX 14.10.pdf", plan_text("14/10/2026 06:00", line("T1", "VinFast", 45, 1200)))
        self.assertIsNone(self.machine(shift(14, 6, 0)))
        self.assertEqual(self.machine(shift(14, 6, 1)), "T1")

    def test_a_plan_one_minute_before_the_shift_counts(self):
        self.add("KHSX 14.10.pdf", plan_text("14/10/2026 05:59", line("T1", "VinFast", 45, 1200)))
        self.assertEqual(self.machine(shift(14, 6, 0)), "T1")

    def test_a_broken_newest_plan_leaves_the_older_plan_in_use(self):
        self.add("KHSX 13.10.pdf", plan_text("12/10/2026 15:00", line("T1", "VinFast", 45, 1200)))
        self.add("KHSX 15.10.pdf", ValueError("bad pdf"))
        with self.assertLogs("hasc.plans", logging.WARNING) as logged:
            self.assertEqual(self.machine(shift(16)), "T1")
        self.assertEqual(len(logged.records), 1)
        self.assertIn("KHSX 15.10.pdf", logged.output[0])

    def test_a_plan_with_no_time_anywhere_is_skipped(self):
        self.add("KHSX 13.10.pdf", plan_text("12/10/2026 15:00", line("T1", "VinFast", 45, 1200)))
        self.add("khsx.pdf", plan_text(None, line("T2", "VinFast", 45, 1200)))
        with self.assertLogs("hasc.plans", logging.WARNING):
            self.assertEqual(self.machine(shift(16)), "T1")

    def test_newest_plan_date_wins_when_a_product_moves_machines(self):
        self.add("KHSX 12.10.pdf", plan_text("12/10/2026 09:00", line("T1", "VinFast", 45, 1200)))
        self.add("KHSX 14.10.pdf", plan_text("14/10/2026 09:00", line("T2", "VinFast", 45, 1200)))
        self.assertEqual(self.machine(shift(13)), "T1")
        self.assertEqual(self.machine(shift(15)), "T2")
        self.assertEqual(plan_match(self.load(), shift(15), "VinFast", 45, 1200).file, "KHSX 14.10.pdf")

    def test_a_machine_uses_its_own_newest_plan_date(self):
        self.add("KHSX 12.10.pdf", plan_text("12/10/2026 09:00", line("T1", "VinFast", 45, 1200)))
        self.add("KHSX 14.10.pdf", plan_text("14/10/2026 09:00", line("T2", "Khac", 50, 900)))
        self.assertEqual(self.machine(shift(15)), "T1")

    def test_a_product_dropped_from_the_machines_newest_plan_is_not_matched(self):
        self.add("KHSX 12.10.pdf", plan_text("12/10/2026 09:00", line("T1", "VinFast", 45, 1200)))
        self.add("KHSX 14.10.pdf", plan_text("14/10/2026 09:00", line("T1", "Khac", 50, 900)))
        self.assertIsNone(self.machine(shift(15)))

    def test_same_date_tie_between_machines_is_none(self):
        self.add("KHSX 14.10.pdf", plan_text("14/10/2026 09:00",
                                             line("T1", "VinFast", 45, 1200), line("T2", "VinFast", 45, 1200)))
        self.assertIsNone(self.machine(shift(15)))

    def test_no_match_is_none(self):
        self.add("KHSX 14.10.pdf", plan_text("14/10/2026 09:00", line("T1", "VinFast", 45, 1200)))
        self.assertIsNone(self.machine(shift(15), "HD", 100, 650))
        self.assertIsNone(machine_for(load_plans(self.root / "missing", NAMES), shift(15), "VinFast", 45, 1200))
        self.assertIsNone(machine_for(load_plans(None, NAMES), shift(15), "VinFast", 45, 1200))

    def test_plans_of_one_date_merge_main_and_song_sao(self):
        self.add("KHSX 14.10.pdf", plan_text("14/10/2026 09:00", line("T6", "VinFast", 45, 1200)))
        self.add("KHSX SONG SAO 14.10.pdf", plan_text("14/10/2026 10:00", "T6   CT0123   70   740",
                                                       "T6   TĐ-9   70   900", "T1   CT0999   10   20"))
        for args in (("VinFast", 45, 1200), ("Song Sao", 70, 740), ("song sao", 70, 900)):
            self.assertEqual(self.machine(shift(15), *args), "T6")
        self.assertEqual(plan_match(self.load(), shift(15), "SS", 70, 740).file, "KHSX SONG SAO 14.10.pdf")
        self.assertEqual(self.machine(shift(15), "Song Sao", 10, 20), "T1")
        self.assertIsNone(self.machine(shift(15), "Song Sao", 70, 800))

    def test_song_sao_takes_the_two_whole_numbers_after_the_code_with_words_before_it(self):
        self.add("KHSX SONG SAO 27.10.pdf", plan_text(
            "26/10/2026 16:00",
            "T6 Hồng trắng CTĐXL-02 70 740 40 390 350 1 1300 4 62,6 250,5",
            "T6 Sao Vàng trắng 350 + CTVT-25 70 900 40 390 350 1 1300 4 62,6 250,5",
            "T6 Song 23.10 + 40 TĐ-IN60 giấy 850 60 390",
            "T1 Hồng trắng CTĐXL-09 55,5 600 40",
            "ghi chú T1 CTĐXL-10 45 700",
        ))
        start = shift(27, 7)
        self.assertEqual(plan_match(self.load(), start, "Song Sao", 70, 740), PlanMatch("T6", "KHSX SONG SAO 27.10.pdf"))
        self.assertEqual(self.machine(start, "Song Sao", 70, 900), "T6")
        self.assertEqual([(l.machine, l.thickness, l.width) for l in self.load().plans[0].lines],
                         [("T6", 70, 740), ("T6", 70, 900)])

    def test_song_sao_lines_in_an_ordinary_plan_follow_the_ordinary_rule(self):
        self.add("KHSX 14.10.pdf", plan_text("14/10/2026 09:00", "T6   CT0123   70   740"))
        self.assertIsNone(self.machine(shift(15), "Song Sao", 70, 740))

    def test_file_name_time_counts_from_the_next_day(self):
        self.add("KHSX 14.10.pdf", plan_text(None, line("T1", "VinFast", 45, 1200)),
                 mtime=int(datetime(2026, 10, 14, 9, 0, tzinfo=VN).timestamp() * 1e9))
        self.assertEqual(self.load().plans[0].time, datetime(2026, 10, 14, 23, 59, tzinfo=VN))
        self.assertIsNone(self.machine(shift(14, 18, 0)))
        self.assertIsNone(self.machine(shift(14, 23, 59)))
        self.assertEqual(self.machine(shift(15, 0, 0)), "T1")
        self.assertEqual(self.machine(shift(15, 6, 0)), "T1")

    def test_file_name_year_is_the_mtime_year_or_the_one_before(self):
        mtime = lambda y, m, d: int(datetime(y, m, d, 9, 0, tzinfo=VN).timestamp() * 1e9)
        self.add("KHSX 30.12.pdf", plan_text(None, line("T1", "VinFast", 45, 1200)), mtime(2027, 1, 2))
        self.add("KHSX 15.01.pdf", plan_text(None, line("T1", "VinFast", 45, 1200)), mtime(2027, 1, 10))
        self.add("KHSX 05.11.2025.pdf", plan_text(None, line("T1", "VinFast", 45, 1200)), mtime(2026, 12, 31))
        years = {plan.file: plan.time.year for plan in self.load().plans}
        self.assertEqual(years, {"KHSX 30.12.pdf": 2026, "KHSX 15.01.pdf": 2027, "KHSX 05.11.2025.pdf": 2025})

    def test_cache_rereads_a_changed_file_and_drops_a_deleted_one(self):
        path = self.add("KHSX 14.10.pdf", plan_text("14/10/2026 09:00", line("T1", "VinFast", 45, 1200)), mtime=10**18)
        other = self.add("KHSX 13.10.pdf", plan_text("13/10/2026 09:00", line("T2", "Khac", 50, 900)))
        reads = mock.Mock(side_effect=self.fake_text)
        with mock.patch.object(plans_module, "_pdf_text", reads):
            self.assertEqual(self.machine(shift(15)), "T1")
            self.assertEqual(self.machine(shift(15)), "T1")
            self.assertEqual(reads.call_count, 2)
            self.texts["KHSX 14.10.pdf"] = plan_text("14/10/2026 09:00", line("T3", "VinFast", 45, 1200))
            os.utime(path, ns=(10**18 + 5, 10**18 + 5))
            self.assertEqual(self.machine(shift(15)), "T3")
            self.assertEqual(reads.call_count, 3)
            path.unlink()
            self.assertIsNone(self.machine(shift(15)))
            self.assertEqual(self.machine(shift(15), "Khac", 50, 900), "T2")
            self.assertEqual(reads.call_count, 3)
        other.unlink()
        self.assertEqual(self.load().plans, ())

    def test_only_pdf_files_are_plans(self):
        self.add("KHSX 14.10.PDF", plan_text("14/10/2026 09:00", line("T1", "VinFast", 45, 1200)))
        self.add("notes.txt", plan_text("14/10/2026 09:00", line("T2", "VinFast", 45, 1200)))
        self.assertEqual([plan.file for plan in self.load().plans], ["KHSX 14.10.PDF"])

    def test_without_names_customers_compare_case_folded(self):
        self.add("KHSX 14.10.pdf", plan_text("14/10/2026 09:00", line("T1", "VINFAST", 45, 1200)))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HASC_KNOWN_NAMES", None)
            os.environ.pop("HASC_WORKER_ENV", None)
            plans = load_plans(self.root)
        self.assertEqual(machine_for(plans, shift(15), "vinfast", 45, 1200), "T1")
        self.assertIsNone(machine_for(plans, shift(15), "Vin Fast", 45, 1200))

    def test_names_come_from_the_environment_or_the_worker_env_file(self):
        self.add("KHSX 14.10.pdf", plan_text("14/10/2026 09:00", line("T1", "VINFAST", 45, 1200)))
        names_file = self.root / "known_names.json"
        names_file.write_text(json.dumps(NAMES), encoding="utf-8")
        env_file = self.root / "worker.env"
        env_file.write_text(f"# comment\nOTHER=1\nHASC_KNOWN_NAMES={names_file}\n", encoding="utf-8")
        for env in ({"HASC_KNOWN_NAMES": str(names_file)}, {"HASC_WORKER_ENV": str(env_file)}):
            with mock.patch.dict(os.environ, env):
                if "HASC_KNOWN_NAMES" not in env:
                    os.environ.pop("HASC_KNOWN_NAMES", None)
                plans = load_plans(self.root)
            self.assertEqual(machine_for(plans, shift(15), "Vin Fast", 45, 1200), "T1")


if __name__ == "__main__":
    unittest.main()
