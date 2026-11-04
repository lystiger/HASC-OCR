import unittest

from hasc.rules import rules


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


class Failures(unittest.TestCase):
    def test_clean_row_has_no_failures(self):
        self.assertEqual(rules.failures(cells(gross_kg=17.6, core_kg=0.8, net_kg=16.8)), [])

    def test_weight_failure(self):
        self.assertEqual(rules.failures(cells(gross_kg=17.6, core_kg=0.8, net_kg=16.6)), ["weight"])

    def test_thickness_failure(self):
        row = cells(**{f"thickness_{i}": 60 for i in range(1, 7)}, thickness_avg=61)
        self.assertEqual(rules.failures(row), ["thickness"])

    def test_both_failures(self):
        row = cells(**{f"thickness_{i}": 60 for i in range(1, 7)}, thickness_avg=61,
                    gross_kg=17.6, core_kg=0.8, net_kg=16.6)
        self.assertEqual(rules.failures(row), ["weight", "thickness"])

    def test_flag_row_combines_failures_and_doubt(self):
        row = cells(gross_kg=17.6, core_kg=0.8, net_kg=16.6)
        row["cells"]["net_kg"]["doubt"] = True
        self.assertEqual(rules.flag_row(row), ["weight", "doubt:net_kg"])


if __name__ == "__main__":
    unittest.main()
