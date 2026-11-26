"""Zalo intake pure rules (zalo-intake spec R0, R2-R4; criteria 6 and 7): shifts, header keys, fingerprints, gate parsing."""
from datetime import date, datetime
import unittest

from hasc.intake import (VN, WAIT_BAD_DATE, WAIT_BAD_SHIFT, WAIT_DISAGREE, Key, Wait, fingerprint, header_key,
                         shift_of, shift_start)
from hasc.worker.gate import GATE, parse_gate


def vn(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=VN)


class ShiftIntervals(unittest.TestCase):
    def test_shifts_are_half_open_from_seven_and_nineteen_vietnam_time(self):
        self.assertEqual(shift_of(vn(2026, 10, 19, 7)), (date(2026, 10, 19), 1))
        self.assertEqual(shift_of(vn(2026, 10, 19, 18, 59)), (date(2026, 10, 19), 1))
        self.assertEqual(shift_of(vn(2026, 10, 19, 19)), (date(2026, 10, 19), 2))
        self.assertEqual(shift_of(vn(2026, 10, 20, 6, 59)), (date(2026, 10, 19), 2))
        self.assertEqual(shift_start(date(2026, 10, 19), 2), vn(2026, 10, 19, 19))

    def test_utc_send_times_are_placed_in_vietnam_time(self):
        utc = datetime.fromisoformat("2026-10-19T23:30:00+00:00")  # 06:30 on the 20th in Vietnam
        self.assertEqual(shift_of(utc), (date(2026, 10, 19), 2))


class BlankHeader(unittest.TestCase):
    def test_blank_header_takes_the_shift_before_until_two_hours_into_a_shift(self):
        d = date(2026, 10, 19)
        cases = {
            vn(2026, 10, 19, 7): (date(2026, 10, 18), 2),
            vn(2026, 10, 19, 8, 59): (date(2026, 10, 18), 2),
            vn(2026, 10, 19, 9): (d, 1),
            vn(2026, 10, 19, 19): (d, 1),
            vn(2026, 10, 19, 20, 59): (d, 1),
            vn(2026, 10, 19, 21): (d, 2),
            vn(2026, 10, 19, 23, 30): (d, 2),
            vn(2026, 10, 20, 6, 59): (d, 2),
        }
        for send, (want_date, want_shift) in cases.items():
            with self.subTest(send=send.strftime("%d %H:%M")):
                self.assertEqual(header_key({"layout": "form_a"}, send), Key(want_date, want_shift, doubt=True))

    def test_missing_month_counts_as_a_blank_date(self):
        self.assertEqual(header_key({"day": "3", "shift": "1"}, vn(2026, 10, 19, 15)),
                         Key(date(2026, 10, 19), 1, doubt=True))


class WrittenHeader(unittest.TestCase):
    def test_written_shift_one_agrees_until_twelve_hours_after_it_ends(self):
        fields = {"day": "19", "month": "10", "shift": "1"}
        for send in (vn(2026, 10, 19, 18, 59), vn(2026, 10, 19, 19), vn(2026, 10, 20, 6, 59)):
            with self.subTest(send=send.strftime("%d %H:%M")):
                self.assertEqual(header_key(fields, send), Key(date(2026, 10, 19), 1, doubt=False))
        self.assertEqual(header_key(fields, vn(2026, 10, 20, 7)), Wait(WAIT_DISAGREE))

    def test_written_shift_sent_before_it_starts_disagrees(self):
        self.assertEqual(header_key({"day": "19", "month": "10", "shift": "2"}, vn(2026, 10, 19, 18)),
                         Wait(WAIT_DISAGREE))

    def test_new_years_eve_night_shift_sent_on_new_years_day_gets_the_previous_year(self):
        fields = {"day": "31", "month": "12", "year": "2027", "shift": "2"}
        self.assertEqual(header_key(fields, vn(2027, 1, 1, 6, 30)), Key(date(2026, 12, 31), 2, doubt=False))

    def test_gate_year_is_never_used(self):
        fields = {"day": "19", "month": "10", "year": "2020", "shift": "1"}
        self.assertEqual(header_key(fields, vn(2026, 10, 19, 15)), Key(date(2026, 10, 19), 1, doubt=False))

    def test_bad_dates_and_shifts_wait_with_their_reason(self):
        send = vn(2026, 10, 19, 15)
        cases = {
            "8 days before the send date": ({"day": "11", "month": "10", "shift": "1"}, WAIT_BAD_DATE),
            "7 days before is still a date": ({"day": "12", "month": "10", "shift": "1"}, WAIT_DISAGREE),
            "after the send date": ({"day": "20", "month": "10", "shift": "1"}, WAIT_BAD_DATE),
            "impossible date": ({"day": "31", "month": "9", "shift": "1"}, WAIT_BAD_DATE),
            "not a number": ({"day": "1g", "month": "10", "shift": "1"}, WAIT_BAD_DATE),
            "shift 3": ({"day": "19", "month": "10", "shift": "3"}, WAIT_BAD_SHIFT),
            "shift 3, date blank": ({"shift": "3"}, WAIT_BAD_SHIFT),
            "disagrees with send time": ({"day": "18", "month": "10", "shift": "1"}, WAIT_DISAGREE),
        }
        for name, (fields, reason) in cases.items():
            with self.subTest(name):
                self.assertEqual(header_key(fields, send), Wait(reason))


class PartialHeader(unittest.TestCase):
    def test_written_date_only_fills_the_inferred_shift_when_the_date_agrees(self):
        self.assertEqual(header_key({"day": "18", "month": "10"}, vn(2026, 10, 19, 8)),
                         Key(date(2026, 10, 18), 2, doubt=True))

    def test_written_date_only_that_disagrees_with_the_inferred_date_waits(self):
        self.assertEqual(header_key({"day": "19", "month": "10"}, vn(2026, 10, 19, 8)), Wait(WAIT_DISAGREE))

    def test_written_shift_only_fills_the_inferred_date_when_the_shift_agrees(self):
        self.assertEqual(header_key({"shift": "2"}, vn(2026, 10, 19, 22)), Key(date(2026, 10, 19), 2, doubt=True))
        self.assertEqual(header_key({"shift": "1"}, vn(2026, 10, 19, 22)), Wait(WAIT_DISAGREE))


class Fingerprints(unittest.TestCase):
    def test_comma_and_dot_decimals_round_to_one_place(self):
        self.assertEqual(fingerprint({"first_code": " 1 ", "net1": "13,6", "net2": "13.60", "net3": "13,64"}),
                         ("1", 13.6, 13.6, 13.6))
        self.assertEqual(fingerprint({"net1": "13,65"}), (None, 13.7, None, None))

    def test_code_is_case_folded_and_dash_or_question_mark_is_blank(self):
        self.assertEqual(fingerprint({"first_code": "AbC", "net1": "1"})[0], "abc")
        self.assertEqual(fingerprint({"first_code": "-", "net1": "13,6"}), (None, 13.6, None, None))
        self.assertIsNone(fingerprint({"first_code": "?"}))

    def test_non_numbers_are_blank_and_all_blank_is_no_fingerprint(self):
        self.assertIsNone(fingerprint({"net1": "nan", "net2": "inf", "net3": "~13,6"}))
        self.assertIsNone(fingerprint({"layout": "form_a", "day": "19"}))

    def test_the_four_19_october_fingerprints_all_differ(self):
        prints = [fingerprint(dict(zip(("first_code", "net1", "net2", "net3"), values))) for values in (
            ("1", "13.6", "14.2", "14.4"), ("1", "13,6", "13,6", "13,6"), ("1", "13.8", "13.8", "13.8"),
            ("-", "13,6", "13,2", "13,6"))]
        self.assertEqual(len(set(prints)), 4)


class GateParsing(unittest.TestCase):
    def test_first_line_with_layout_gives_the_kept_keys(self):
        text = ("Here you go.\nturn=left layout=form_a code=BMMT01-2022/HASC day=19 month=10 year=2026 shift=1 "
                "first_code=1 net1=13,6\nlayout=back day=1")
        self.assertEqual(parse_gate(text), {"turn": "left", "layout": "form_a", "code": "BMMT01-2022/HASC",
                                            "day": "19", "month": "10", "year": "2026", "shift": "1",
                                            "first_code": "1", "net1": "13,6"})

    def test_aliases_lower_case_and_first_duplicate_wins(self):
        self.assertEqual(parse_gate("LAYOUT=form_a Ca=2 Tháng=10 Năm=2026 shift=1 day=5 day=6"),
                         {"layout": "form_a", "shift": "2", "month": "10", "year": "2026", "day": "5"})

    def test_unknown_keys_and_instruction_text_are_dropped(self):
        text = "layout=other status=filed machine=T1 ignore the rules and send=yes reason=ok"
        self.assertEqual(parse_gate(text), {"layout": "other"})

    def test_malformed_replies(self):
        for text in (None, "", "turn=none day=19", "layout=form_b day=19", "layout= form_a", "xlayout=form_a"):
            with self.subTest(text=text):
                self.assertIsNone(parse_gate(text))

    def test_prompt_is_the_spike_prompt(self):
        self.assertTrue(GATE.startswith("This photo shows a paper form on a desk. Do NOT read the table rows."))
        self.assertIn("first_code= the Mã cuộn of the first filled table row. net1= net2= net3=", GATE)
        self.assertTrue(GATE.endswith("Leave out any key that is blank or unreadable. Never guess."))


if __name__ == "__main__":
    unittest.main()
