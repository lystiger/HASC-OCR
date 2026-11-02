import contextlib
import io
import pathlib
import sys
import tempfile
import unittest

from openpyxl import Workbook

import score

KEYS = ["customer", "film_type", "roll_code", "thickness_mic", "width_mm", "length_mm",
        "gross_kg", "core_kg", "cover_kg", "net_kg", "subtotal_note"]


def workbook(path, sheets):
    wb = Workbook()
    index = wb.active
    index.title = "Sheets"
    index.append(["Tab", "Photo", "Split", "Shift", "Rows labelled"])
    for name, split, rows in sheets:
        index.append([name, "", split, 1, None])
        tab = wb.create_sheet(name)
        for col, key in enumerate(KEYS, 1):
            tab.cell(4, col, key)
        for r, row in enumerate(rows, 7):
            for col, key in enumerate(KEYS, 1):
                tab.cell(r, col, row.get(key))
    wb.save(path)


class Values(unittest.TestCase):
    def test_decimal_comma_and_dot_are_equal(self):
        self.assertTrue(score.same("13,6", "13.6"))
        self.assertTrue(score.same("70", "70,0"))

    def test_no_tolerance(self):
        self.assertFalse(score.same("13.6", "13.7"))

    def test_text_ignores_case_and_spaces_not_accents(self):
        self.assertTrue(score.same("vinfast  ống", "VINFAST ống"))
        self.assertFalse(score.same("ống", "ong"))

    def test_non_numeric_stays_text(self):
        self.assertTrue(score.same("480+40", "480+40"))
        self.assertFalse(score.same("480+40", "480"))


class Parsing(unittest.TestCase):
    def test_reads_rows_and_skips_think_and_separator(self):
        rows, malformed = score.parse_pipe(
            "<think>\n\n</think>\n| HD | HCK | 1 | 90 | 480 | 1710 | 70,6 | 1,6 | 0,4 | 68,6 |\n"
            "|---|---|---|---|---|---|---|---|---|---|\n| | | 2 | | | | 70 | | | 68 |\n")
        self.assertEqual(malformed, 0)
        self.assertEqual(rows[0]["customer"], "HD")
        self.assertEqual(rows[1]["roll_code"], "2")
        self.assertIsNone(rows[1]["customer"])

    def test_wrong_cell_count_is_counted_not_guessed(self):
        rows, malformed = score.parse_pipe("|HD|HIC|2|70|11|11|68|\n")
        self.assertEqual((rows, malformed), ([], 1))


class Scoring(unittest.TestCase):
    def tally(self, labels, preds):
        return score.score_sheet(labels, preds)

    def test_blank_matching_blank_earns_nothing(self):
        t = self.tally([{"net_kg": "13.6", "cover_kg": None}], [{"net_kg": "13.6", "cover_kg": None}])
        self.assertEqual(t["net_kg"], [1, 1])
        self.assertNotIn("cover_kg", t)

    def test_invented_value_counts_against(self):
        t = self.tally([{"net_kg": "13.6", "cover_kg": None}], [{"net_kg": "13.6", "cover_kg": "11"}])
        self.assertEqual(t["cover_kg"], [0, 1])

    def test_unreadable_label_is_left_out(self):
        t = self.tally([{"net_kg": "?", "gross_kg": "15.8"}], [{"net_kg": "9", "gross_kg": "15.8"}])
        self.assertNotIn("net_kg", t)
        self.assertEqual(t["gross_kg"], [1, 1])

    def test_skipped_row_does_not_shift_the_rest(self):
        labels = [{"roll_code": str(i), "net_kg": str(60 + i)} for i in range(1, 6)]
        preds = [r for r in labels if r["roll_code"] != "2"]
        t = self.tally(labels, preds)
        self.assertEqual(t["net_kg"], [4, 5])
        self.assertEqual(t["roll_code"], [4, 5])

    def test_extra_row_counts_against(self):
        t = self.tally([{"net_kg": "60"}], [{"net_kg": "60"}, {"net_kg": "61"}])
        self.assertEqual(t["net_kg"], [1, 2])


class Report(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.dir.name)
        self.xlsx = self.root / "labels.xlsx"
        self.preds = self.root / "preds"
        self.preds.mkdir()

    def tearDown(self):
        self.dir.cleanup()

    def test_loads_labels_by_split_and_ignores_subtotal(self):
        workbook(self.xlsx, [("a", "tuning", [{"net_kg": "60", "subtotal_note": "60"}]), ("b", "hidden", [{"net_kg": "1"}])])
        labels = score.load_labels(self.xlsx, "tuning")
        self.assertEqual(list(labels), ["a"])
        self.assertNotIn("subtotal_note", labels["a"][0])

    def test_unlabelled_tab_and_missing_output_make_it_incomplete(self):
        workbook(self.xlsx, [("a", "tuning", []), ("b", "tuning", [{"net_kg": "60"}])])
        result = score.run(self.xlsx, self.preds, "tuning")
        self.assertEqual(result["unlabelled"], ["a"])
        self.assertEqual(result["missing_output"], ["b"])
        self.assertFalse(result["complete"])

    def test_score_excludes_unread_fields_and_small_fields(self):
        rows = [{"net_kg": str(i), "customer": "X"} for i in range(1, 101)]
        rows[0]["cover_kg"] = "1"
        workbook(self.xlsx, [("a", "hidden", rows)])
        lines = ["|X|||||||||%d|" % i for i in range(1, 101)]
        (self.preds / "a.out").write_text("\n".join(lines), encoding="utf-8")
        result = score.run(self.xlsx, self.preds, "hidden")
        self.assertTrue(result["complete"])
        self.assertEqual(result["scored_fields"], ["customer", "net_kg"])
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["fields"]["cover_kg"], [0, 1])

    def test_failed_gate_exits_non_zero(self):
        workbook(self.xlsx, [("a", "hidden", [{"net_kg": str(i)} for i in range(1, 101)])])
        (self.preds / "a.out").write_text("\n".join("|||||||||%d|" % (i + 1) for i in range(1, 101)), encoding="utf-8")
        argv = sys.argv
        sys.argv = ["score.py", str(self.xlsx), str(self.preds), "--split", "hidden"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(score.main(), 1)
        finally:
            sys.argv = argv


if __name__ == "__main__":
    unittest.main()
