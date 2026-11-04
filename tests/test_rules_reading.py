import unittest

from hasc_ocr import reading, rules


def cells(**values):
    return {"cells": {k: {"value": v, "doubt": False} for k, v in values.items()}}


class WeightRule(unittest.TestCase):
    def test_passes_when_net_matches(self):
        self.assertTrue(rules.weight_rule(cells(gross_kg=17.6, core_kg=0.8, net_kg=16.8)))

    def test_missing_cover_counts_as_zero_but_missing_gross_skips(self):
        self.assertTrue(rules.weight_rule(cells(gross_kg=17.6, core_kg=0.8, cover_kg=None, net_kg=16.8)))
        self.assertIsNone(rules.weight_rule(cells(core_kg=0.8, net_kg=16.8)))

    def test_fails_on_mismatch(self):
        self.assertFalse(rules.weight_rule(cells(gross_kg=17.6, core_kg=0.8, net_kg=16.6)))

    def test_text_value_skips(self):
        self.assertIsNone(rules.weight_rule(cells(gross_kg="17,6kg", core_kg=0.8, net_kg=16.8)))

    def test_back_layout_skips(self):
        self.assertIsNone(rules.weight_rule(cells(gross_kg=17.6, core_kg=0.8, net_kg=1.0), layout="back"))


class ThicknessRule(unittest.TestCase):
    def test_average(self):
        row = cells(**{f"thickness_{i}": 60 for i in range(1, 7)}, thickness_avg=60)
        self.assertTrue(rules.thickness_rule(row))
        row["cells"]["thickness_avg"]["value"] = 61
        self.assertFalse(rules.thickness_rule(row))

    def test_skips_when_a_value_is_empty(self):
        self.assertIsNone(rules.thickness_rule(cells(thickness_1=60, thickness_avg=60)))


def result(*rows, layout="form_a"):
    return {"layout": layout, "page": {}, "rows": list(rows), "reader_name": "primary"}


class StubReader:
    def __init__(self, *results):
        self.results, self.efforts = list(results), []

    def read(self, image, effort="low"):
        self.efforts.append(effort)
        return self.results.pop(0)


class Reading(unittest.TestCase):
    def test_clean_page_reads_once(self):
        stub = StubReader(result(cells(gross_kg=17.6, core_kg=0.8, net_kg=16.8)))
        out = reading.read_sheet(stub, b"jpg")
        self.assertEqual(stub.efforts, ["low"])
        self.assertEqual(out["rows"][0]["flags"], [])

    def test_rule_failure_rereads_and_takes_the_passing_row(self):
        stub = StubReader(result(cells(gross_kg=17.6, core_kg=0.8, net_kg=16.6)),
                          result(cells(gross_kg=17.6, core_kg=0.8, net_kg=16.8)))
        out = reading.read_sheet(stub, b"jpg")
        self.assertEqual(stub.efforts, ["low", "high"])
        self.assertEqual(out["rows"][0]["cells"]["net_kg"]["value"], 16.8)
        self.assertEqual(out["rows"][0]["flags"], [])
        self.assertEqual(out["rereads"], 1)

    def test_still_failing_keeps_first_reading_and_flags(self):
        stub = StubReader(result(cells(gross_kg=17.6, core_kg=0.8, net_kg=16.6)),
                          result(cells(gross_kg=17.6, core_kg=0.8, net_kg=16.5)))
        out = reading.read_sheet(stub, b"jpg")
        self.assertEqual(out["rows"][0]["cells"]["net_kg"]["value"], 16.6)
        self.assertEqual(out["rows"][0]["flags"], ["weight"])

    def test_reread_with_different_row_count_is_ignored(self):
        stub = StubReader(result(cells(gross_kg=17.6, core_kg=0.8, net_kg=16.6)),
                          result(cells(net_kg=1), cells(net_kg=2)))
        out = reading.read_sheet(stub, b"jpg")
        self.assertEqual(len(out["rows"]), 1)
        self.assertEqual(out["rows"][0]["flags"], ["weight"])

    def test_doubtful_cell_is_flagged(self):
        row = cells(net_kg=16.8)
        row["cells"]["net_kg"]["doubt"] = True
        out = reading.read_sheet(StubReader(result(row)), b"jpg")
        self.assertEqual(out["rows"][0]["flags"], ["doubt:net_kg"])


if __name__ == "__main__":
    unittest.main()
