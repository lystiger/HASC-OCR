import unittest

from hasc.reader import reader
from hasc.reader.reader import ReaderError

NAMES = {"customers": {"Toàn Mỹ": ["T-Mỹ"], "HD": []}, "products": {"Cuộn PE": ["cuộn PE"], "GT": []}}


def reply(rows, layout="form_a", page="day=1 month=10 year=2026 shift=1"):
    lines = [f"layout={layout} {page}"] + [" ".join(f"{k}={v}" for k, v in r.items()) for r in rows]
    return {"choices": [{"message": {"content": "\n".join(lines)}}], "usage": {"cost": 0.003}}


class FakeTransport:
    def __init__(self, *answers):
        self.answers, self.bodies = list(answers), []

    def __call__(self, body):
        self.bodies.append(body)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def make(*answers, fallback=None):
    return reader.CloudReader(FakeTransport(*answers), NAMES, model="primary", fallback=fallback,
                              sleep=lambda s: None)


ROW = {"roll_code": "1", "gross_kg": "17,6", "core_kg": "0,8", "net_kg": "16,8"}


class Parsing(unittest.TestCase):
    def test_returns_read_result_with_numbers(self):
        result = make(reply([ROW])).read(b"jpg")
        cells = result["rows"][0]["cells"]
        self.assertEqual(result["layout"], "form_a")
        self.assertEqual(cells["gross_kg"], {"value": 17.6, "doubt": False})
        self.assertEqual(cells["roll_code"], {"value": "1", "doubt": False})
        self.assertEqual(result["page"]["day"], {"value": "1", "doubt": False})
        self.assertEqual(result["reader_name"], "primary")

    def test_non_numeric_weight_is_kept_as_text_with_doubt(self):
        cells = make(reply([{"gross_kg": "17,6kg"}])).read(b"jpg")["rows"][0]["cells"]
        self.assertEqual(cells["gross_kg"], {"value": "17,6kg", "doubt": True})

    def test_question_mark_is_unreadable(self):
        cells = make(reply([{"net_kg": "?"}])).read(b"jpg")["rows"][0]["cells"]
        self.assertEqual(cells["net_kg"], {"value": None, "doubt": True, "unreadable": True})

    def test_known_alias_becomes_standard_name(self):
        cells = make(reply([{"customer": "t-mỹ", "film_type": "cuộn PE"}])).read(b"jpg")["rows"][0]["cells"]
        self.assertEqual(cells["customer"], {"value": "Toàn Mỹ", "doubt": False})
        self.assertEqual(cells["film_type"], {"value": "Cuộn PE", "doubt": False})

    def test_unknown_name_is_kept_and_doubted(self):
        cells = make(reply([{"customer": "XYZ"}])).read(b"jpg")["rows"][0]["cells"]
        self.assertEqual(cells["customer"], {"value": "XYZ", "doubt": True})

    def test_request_denies_training(self):
        transport = FakeTransport(reply([ROW]))
        reader.CloudReader(transport, NAMES, model="primary", sleep=lambda s: None).read(b"jpg")
        self.assertEqual(transport.bodies[0]["provider"]["data_collection"], "deny")

    def test_tilde_marks_doubt_and_names_keep_spaces(self):
        result = make(reply([{"net_kg": "~16,8", "customer": "Toàn Mỹ"}], page="day=1 names=Hà, Bảo")).read(b"jpg")
        self.assertEqual(result["rows"][0]["cells"]["net_kg"], {"value": 16.8, "doubt": True})
        self.assertEqual(result["rows"][0]["cells"]["customer"]["value"], "Toàn Mỹ")
        self.assertEqual(result["page"]["names"]["value"], "Hà, Bảo")

    def test_not_form_a(self):
        with self.assertRaises(ReaderError) as caught:
            make(reply([], layout="other")).read(b"jpg")
        self.assertEqual(caught.exception.kind, "not_form_a")


THICK = {f"thickness_{i}": "45" for i in range(1, 7)}


class Subtotals(unittest.TestCase):
    def test_average_far_above_thicknesses_is_moved_to_subtotal_note(self):
        cells = make(reply([{**THICK, "thickness_mic": "45", "thickness_avg": "363,2"}])).read(b"jpg")["rows"][0]["cells"]
        self.assertNotIn("thickness_avg", cells)
        self.assertEqual(cells["subtotal_note"], {"value": 363.2, "doubt": True})

    def test_normal_average_is_untouched(self):
        cells = make(reply([{**THICK, "thickness_mic": "45", "thickness_avg": "45,2"}])).read(b"jpg")["rows"][0]["cells"]
        self.assertEqual(cells["thickness_avg"], {"value": 45.2, "doubt": False})
        self.assertNotIn("subtotal_note", cells)

    def test_model_written_subtotal_note_is_kept(self):
        cells = make(reply([{**THICK, "thickness_mic": "45", "subtotal_note": "363,2"}])).read(b"jpg")["rows"][0]["cells"]
        self.assertEqual(cells["subtotal_note"], {"value": 363.2, "doubt": False})
        self.assertNotIn("thickness_avg", cells)

    def test_large_average_without_any_thickness_is_a_subtotal(self):
        cells = make(reply([{"thickness_avg": "363,2"}])).read(b"jpg")["rows"][0]["cells"]
        self.assertEqual(cells["subtotal_note"], {"value": 363.2, "doubt": True})
        self.assertNotIn("thickness_avg", cells)

    def test_plausible_average_without_measured_thicknesses_stays(self):
        """P84: measured thicknesses are usually blank, so a written average alone is kept."""
        cells = make(reply([{"thickness_avg": "45,2"}])).read(b"jpg")["rows"][0]["cells"]
        self.assertEqual(cells["thickness_avg"], {"value": 45.2, "doubt": False})


class Retry(unittest.TestCase):
    def test_retries_network_error_then_succeeds(self):
        result = make(reader.TransportError("boom"), reply([ROW])).read(b"jpg")
        self.assertEqual(len(result["rows"]), 1)

    def test_retries_reply_without_lines(self):
        bad = {"choices": [{"message": {"content": "sorry, I cannot"}}]}
        result = make(bad, reply([ROW])).read(b"jpg")
        self.assertEqual(len(result["rows"]), 1)

    def test_unknown_field_is_invalid(self):
        bad = reply([{"weight": "1"}])
        result = make(bad, reply([ROW])).read(b"jpg")
        self.assertIn("gross_kg", result["rows"][0]["cells"])

    def test_gives_up_after_three_tries(self):
        errors = [reader.TransportError("boom")] * 3
        with self.assertRaises(ReaderError) as caught:
            make(*errors).read(b"jpg")
        self.assertEqual(caught.exception.kind, "other")

    def test_timeout_kind(self):
        with self.assertRaises(ReaderError) as caught:
            make(*[reader.TransportError("slow", timeout=True)] * 3).read(b"jpg")
        self.assertEqual(caught.exception.kind, "timeout")


class Fallback(unittest.TestCase):
    def test_fallback_model_marks_every_cell_doubtful(self):
        transport = FakeTransport(*[reader.TransportError("down")] * 3, reply([ROW]))
        r = reader.CloudReader(transport, NAMES, model="primary", fallback="secondary", sleep=lambda s: None)
        result = r.read(b"jpg")
        self.assertEqual(result["reader_name"], "secondary")
        self.assertTrue(all(c["doubt"] for c in result["rows"][0]["cells"].values()))
        self.assertEqual(transport.bodies[-1]["model"], "secondary")

    def test_fallback_result_says_why_the_main_model_failed(self):
        transport = FakeTransport(*[reader.TransportError("down")] * 3, reply([ROW]))
        r = reader.CloudReader(transport, NAMES, model="primary", fallback="secondary", sleep=lambda s: None)
        self.assertEqual(r.read(b"jpg")["fallback_reason"], "primary: down")

    def test_main_model_result_has_no_fallback_reason(self):
        self.assertNotIn("fallback_reason", make(reply([ROW])).read(b"jpg"))


if __name__ == "__main__":
    unittest.main()
