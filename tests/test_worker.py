import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from hasc.db import (
    create_photo,
    create_read_job,
    create_sheet,
    create_user,
    get_connection,
    get_read_job_by_id,
    get_sheet_by_id,
    get_sheet_data,
)
from hasc.reader.reader import CloudReader, ReaderError
from hasc.web.operator import get_operator_uploads
from hasc.web.security import hash_password
from hasc.worker import config, jobs
from hasc.worker.__main__ import main
from tests.test_import_boundaries import REPO_ROOT, extract_imports


def jpeg(width, height):
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, "JPEG")
    return out.getvalue()


def row(**values):
    return {"cells": {field: {"value": value, "doubt": False} for field, value in values.items()}}


def result(*rows, layout="form_a", page=None):
    return {"layout": layout, "page": page or {}, "rows": list(rows), "reader_name": "fake-model"}


class FakeReader:
    """Returns or raises the next outcome. Records each path it was asked for and the size of that image."""

    def __init__(self, *outcomes, turn=0):
        self.outcomes, self.paths, self.sizes, self.turn_by = list(outcomes), [], [], turn

    def turn(self, image):
        return self.turn_by, {"cost": 0.001, "prompt_tokens": 10, "completion_tokens": 1}

    def read(self, image_path):
        self.paths.append(image_path)
        with Image.open(image_path) as image:
            self.sizes.append(image.size)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class WorkerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db_path = self.root / "hasc.db"
        self.conn = get_connection(self.db_path)
        self.addCleanup(self.conn.close)
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()

    def queue(self, shift=1, width=200, height=100, uploaded_by=None):
        sheet_id = create_sheet(self.conn, form="BMMT01-2022/HASC", machine="T6",
                                production_date="2026-10-21", shift=shift)
        path = self.root / f"photo{shift}.jpg"
        path.write_bytes(jpeg(width, height))
        photo_id = create_photo(self.conn, sheet_id=sheet_id, path=str(path), sha256=f"hash{shift}",
                                uploaded_by=uploaded_by)
        job_id = create_read_job(self.conn, photo_id=photo_id)
        return sheet_id, job_id

    def drain(self, *outcomes, turn=0):
        reader = FakeReader(*outcomes, turn=turn)
        jobs.run_pending(self.conn, reader, self.scratch)
        return reader

    def events(self, action):
        return self.conn.execute("SELECT * FROM events WHERE action = ?", (action,)).fetchall()

    def cells(self, sheet_id):
        return [row_["cells"] for row_ in get_sheet_data(self.conn, sheet_id)]


class Queue(WorkerTest):
    def test_claim_takes_the_oldest_job_once(self):
        _, first = self.queue(shift=1)
        _, second = self.queue(shift=2)
        other = get_connection(self.db_path)
        self.addCleanup(other.close)
        claimed = jobs.claim_job(self.conn)
        self.assertEqual(claimed["id"], first)
        self.assertEqual(claimed["status"], "running")
        self.assertEqual(claimed["attempts"], 1)
        self.assertEqual(jobs.claim_job(other)["id"], second)
        self.assertIsNone(jobs.claim_job(self.conn))

    def test_clean_read_writes_rows_and_moves_sheet_to_needs_review(self):
        sheet_id, job_id = self.queue()
        reader = self.drain(result(row(roll_code="1", customer="AB", film_type="GT", gross_kg=15.8,
                                       core_kg=2.2, cover_kg=0.4, net_kg=13.2)))
        self.assertTrue(reader.paths[0].endswith(".jpg"))
        job = get_read_job_by_id(self.conn, job_id)
        self.assertEqual((job["status"], job["attempts"], job["reader_name"]), ("done", 1, "fake-model"))
        sheet = get_sheet_by_id(self.conn, sheet_id)
        self.assertEqual((sheet["status"], sheet["version"]), ("needs_review", 2))
        data = get_sheet_data(self.conn, sheet_id)
        self.assertEqual(data[0]["row_key"], "1")
        self.assertEqual(data[0]["cells"]["gross_kg"]["value"], "15.8")
        self.assertEqual(data[0]["cells"]["gross_kg"]["flag"], "none")

    def test_fallback_read_keeps_the_main_model_error(self):
        _, job_id = self.queue()
        read = result(row(roll_code="1"))
        read["fallback_reason"] = "primary: HTTP 502"
        self.drain(read)
        job = get_read_job_by_id(self.conn, job_id)
        self.assertEqual((job["status"], job["last_error"]), ("done", "primary: HTTP 502"))

    def test_upside_down_photo_is_turned_for_the_reader_and_the_screen(self):
        sheet_id, job_id = self.queue()
        photo = self.conn.execute("SELECT * FROM photos WHERE sheet_id = ?", (sheet_id,)).fetchone()
        marked = Image.new("RGB", (200, 100), "white")
        marked.paste((0, 0, 0), (0, 0, 10, 10))
        marked.save(photo["path"], "JPEG")
        display = self.root / "display.jpg"
        display.write_bytes(b"old")
        self.conn.execute("UPDATE photos SET small_copy_path = ? WHERE id = ?", (str(display), photo["id"]))
        self.conn.commit()
        reader = self.drain(result(row(roll_code="1")), turn=180)
        self.assertEqual(reader.sizes, [(200, 100)])
        photo = self.conn.execute("SELECT * FROM photos WHERE id = ?", (photo["id"],)).fetchone()
        self.assertEqual(photo["turn"], 180)
        with Image.open(display) as shown:
            self.assertLess(shown.getpixel((shown.width - 3, shown.height - 3))[0], 80)  # mark moved bottom-right
        job = get_read_job_by_id(self.conn, job_id)
        self.assertAlmostEqual(job["cost_usd"], 0.001)

    def test_photo_the_right_way_up_keeps_turn_zero(self):
        sheet_id, _ = self.queue()
        self.drain(result(row(roll_code="1")))
        photo = self.conn.execute("SELECT turn FROM photos WHERE sheet_id = ?", (sheet_id,)).fetchone()
        self.assertEqual(photo["turn"], 0)

    def test_reader_gets_the_upright_photo(self):
        self.queue(width=100, height=200)
        reader = self.drain(result(row(roll_code="1")))
        self.assertEqual(reader.sizes, [(200, 100)])


class Retries(WorkerTest):
    def test_fails_twice_then_succeeds_ends_in_needs_review(self):
        sheet_id, job_id = self.queue()
        reader = self.drain(ReaderError("timeout", "slow"), ReaderError("other", "busy"),
                            result(row(roll_code="1")))
        job = get_read_job_by_id(self.conn, job_id)
        self.assertEqual((job["status"], job["attempts"], len(reader.paths)), ("done", 3, 3))
        self.assertEqual(get_sheet_by_id(self.conn, sheet_id)["status"], "needs_review")
        self.assertEqual(len(self.events("read_failed")), 2)

    def test_keeps_failing_ends_failed_with_each_attempt_recorded(self):
        sheet_id, job_id = self.queue()
        reader = self.drain(ReaderError("other", "server down"), ReaderError("other", "server down"),
                            ReaderError("other", "server down"))
        job = get_read_job_by_id(self.conn, job_id)
        self.assertEqual((job["status"], job["attempts"]), ("failed", 3))
        self.assertIn("server down", job["last_error"])
        self.assertEqual(get_sheet_by_id(self.conn, sheet_id)["status"], "failed")
        self.assertEqual(len(reader.paths), 3)
        self.assertEqual(len(self.events("read_failed")), 3)

    def test_photo_that_is_not_a_sheet_is_not_retried(self):
        sheet_id, job_id = self.queue()
        reader = self.drain(ReaderError("not_form_a", "neither side"))
        job = get_read_job_by_id(self.conn, job_id)
        self.assertEqual((job["status"], job["attempts"], len(reader.paths)), ("failed", 1, 1))
        self.assertEqual(get_sheet_by_id(self.conn, sheet_id)["status"], "failed")


class CarryDownAndRules(WorkerTest):
    def test_carry_down_fills_blank_product_details_and_never_weights(self):
        self.queue()
        self.drain(result(
            row(roll_code="1", customer="AB", film_type="GT", thickness_mic=50, width_mm=600, length_mm=1500,
                gross_kg=15.8, core_kg=2.2, cover_kg=0.4, net_kg=13.2),
            row(roll_code="2", gross_kg=10, core_kg=1),
            {"cells": {"customer": {"value": None, "doubt": True, "unreadable": True},
                       "net_kg": {"value": 9.0, "doubt": False}}},
        ))
        _, second, third = self.cells(1)
        self.assertEqual(second["customer"]["value"], "AB")
        self.assertTrue(second["customer"]["inherited"])
        self.assertEqual(second["width_mm"]["value"], "600")
        self.assertNotIn("net_kg", second)
        self.assertIsNone(third["customer"]["value"])
        self.assertEqual(third["customer"]["flag"], "unreadable")
        self.assertEqual(third["width_mm"]["value"], "600")

    def test_carry_down_takes_the_new_customer_after_a_change(self):
        self.queue()
        self.drain(result(row(roll_code="1", customer="AB", net_kg=13.2), row(roll_code="2", net_kg=13),
                          row(roll_code="3", customer="CD", net_kg=12), row(roll_code="4", net_kg=11)))
        self.assertEqual([c["customer"]["value"] for c in self.cells(1)], ["AB", "AB", "CD", "CD"])

    def test_carry_down_keeps_details_across_a_customer_change(self):
        """P115: a row that writes only a new customer takes the other details from the row above."""
        self.queue()
        self.drain(result(row(roll_code="1", customer="AB", film_type="GT", width_mm=600, net_kg=13.2),
                          row(roll_code="2", customer="CD", net_kg=12)))
        second = self.cells(1)[1]
        self.assertEqual((second["film_type"]["value"], second["width_mm"]["value"]), ("GT", "600"))

    def test_carry_down_includes_pellet_formula_and_mixing_shift_date(self):
        self.queue()
        self.drain(result(row(roll_code="1", pellet_formula="F2", mixing_shift_date="Ca 1 18/10", net_kg=13.2),
                          row(roll_code="2", net_kg=12)))
        second = self.cells(1)[1]
        self.assertEqual(second["pellet_formula"]["value"], "F2")
        self.assertEqual(second["mixing_shift_date"]["value"], "Ca 1 18/10")
        self.assertTrue(second["pellet_formula"]["inherited"])

    def test_rule_failures_flag_cells_and_change_no_number(self):
        sheet_id, _ = self.queue()
        bad = row(roll_code="1", gross_kg=17.6, core_kg=0.8, net_kg=16.6, thickness_avg=61,
                  **{f"thickness_{i}": 60 for i in range(1, 7)})
        good = row(roll_code="2", gross_kg=10, core_kg=1, net_kg=9, width_mm=600)
        good["cells"]["width_mm"] = {"value": None, "doubt": True, "unreadable": True}
        good["cells"]["length_mm"] = {"value": 1500, "doubt": True}
        self.drain(result(bad, good))
        first, second = self.cells(1)
        self.assertEqual(first["net_kg"]["value"], "16.6")
        self.assertEqual((first["net_kg"]["flag"], first["net_kg"]["note"]), ("rule", "weight"))
        self.assertEqual((first["gross_kg"]["flag"], first["gross_kg"]["note"]), ("rule", "weight"))
        self.assertEqual((first["thickness_avg"]["flag"], first["thickness_avg"]["note"]), ("rule", "thickness"))
        self.assertEqual((first["thickness_3"]["flag"], first["thickness_3"]["note"]), ("rule", "thickness"))
        self.assertEqual(first["roll_code"]["flag"], "none")
        self.assertEqual((second["width_mm"]["flag"], second["width_mm"]["value"]), ("unreadable", None))
        self.assertEqual(second["length_mm"]["flag"], "doubt")
        self.assertEqual(second["net_kg"]["flag"], "none")
        self.assertEqual(get_sheet_by_id(self.conn, sheet_id)["version"], 2)

    def test_blank_rule_input_is_skipped_without_flag(self):
        self.queue()
        self.drain(result(row(roll_code="1", gross_kg=17.6, core_kg=0.8)))
        self.assertEqual(self.cells(1)[0]["gross_kg"]["flag"], "none")

    def test_back_side_skips_the_weight_rule(self):
        sheet_id, _ = self.queue()
        self.drain(result(row(roll_code="1", gross_kg=17.6, core_kg=0.8, net_kg=1.0), layout="back"))
        self.assertEqual(self.cells(sheet_id)[0]["net_kg"]["flag"], "none")
        self.assertEqual(get_sheet_by_id(self.conn, sheet_id)["layout"], "back")


class RollCodeFill(WorkerTest):
    def test_all_blank_roll_codes_filled_with_page_position_and_marked_inherited(self):
        sheet_id, _ = self.queue()
        self.drain(result(
            row(customer="AB", film_type="GT", gross_kg=15.8, core_kg=2.2, cover_kg=0.4, net_kg=13.2),
            row(gross_kg=10.0, core_kg=1.0, cover_kg=0.5, net_kg=8.5),
            {"cells": {"roll_code": {"value": None, "doubt": False}, "net_kg": {"value": 9.0, "doubt": False}}},
        ))
        data = get_sheet_data(self.conn, sheet_id)
        self.assertEqual(len(data), 3)

        self.assertEqual(data[0]["row_key"], "1")
        self.assertEqual(data[0]["cells"]["roll_code"]["value"], "1")
        self.assertTrue(data[0]["cells"]["roll_code"]["inherited"])

        self.assertEqual(data[1]["row_key"], "2")
        self.assertEqual(data[1]["cells"]["roll_code"]["value"], "2")
        self.assertTrue(data[1]["cells"]["roll_code"]["inherited"])

        self.assertEqual(data[2]["row_key"], "3")
        self.assertEqual(data[2]["cells"]["roll_code"]["value"], "3")
        self.assertTrue(data[2]["cells"]["roll_code"]["inherited"])

    def test_mixed_blank_and_written_roll_codes_preserve_written_codes(self):
        sheet_id, _ = self.queue()
        self.drain(result(
            row(roll_code="10", customer="AB", net_kg=13.2),
            row(net_kg=12.0),
            row(roll_code="25", net_kg=11.0),
            {"cells": {"roll_code": {"value": "", "doubt": False}, "net_kg": {"value": 10.0, "doubt": False}}},
        ))
        data = get_sheet_data(self.conn, sheet_id)
        self.assertEqual([d["row_key"] for d in data], ["10", "2", "25", "4"])
        self.assertEqual([d["cells"]["roll_code"]["value"] for d in data], ["10", "2", "25", "4"])
        self.assertFalse(data[0]["cells"]["roll_code"]["inherited"])
        self.assertTrue(data[1]["cells"]["roll_code"]["inherited"])
        self.assertFalse(data[2]["cells"]["roll_code"]["inherited"])
        self.assertTrue(data[3]["cells"]["roll_code"]["inherited"])

    def test_second_photo_of_a_sheet_continues_the_numbering(self):
        sheet_id, _ = self.queue()
        self.drain(result(row(net_kg=13.2), row(net_kg=12.0)))
        photo_id = create_photo(self.conn, sheet_id=sheet_id, path=str(self.root / "photo1.jpg"), sha256="hash1b")
        create_read_job(self.conn, photo_id=photo_id)
        self.drain(result(row(net_kg=11.0)))
        self.assertEqual([d["row_key"] for d in get_sheet_data(self.conn, sheet_id)], ["1", "2", "3"])

    def test_written_roll_code_is_never_renumbered_even_when_out_of_order(self):
        sheet_id, _ = self.queue()
        self.drain(result(
            row(customer="AB", net_kg=13.2),
            row(roll_code="1", net_kg=12.0),
        ))
        data = get_sheet_data(self.conn, sheet_id)
        self.assertEqual([d["row_key"] for d in data], ["1", "1"])
        self.assertTrue(data[0]["cells"]["roll_code"]["inherited"])
        self.assertFalse(data[1]["cells"]["roll_code"]["inherited"])

    def test_row_key_and_operator_problem_list_naming(self):
        op_id = create_user(self.conn, "test_op", hash_password("pass"), role="operator", active=1)
        sheet_id, _ = self.queue(uploaded_by=op_id)
        bad_weight = row(gross_kg=17.6, core_kg=0.8, net_kg=12.0)  # blank roll_code, fails weight check
        bad_thick = row(roll_code="88", gross_kg=10.0, core_kg=1.0, net_kg=9.0,
                        thickness_avg=65, **{f"thickness_{i}": 60 for i in range(1, 7)})
        unreadable_cell = row(gross_kg=10.0, core_kg=1.0, net_kg=9.0)
        unreadable_cell["cells"]["customer"] = {"value": None, "doubt": True, "unreadable": True}
        clean_row = row(gross_kg=10.0, core_kg=1.0, net_kg=9.0)

        self.drain(result(bad_weight, bad_thick, unreadable_cell, clean_row))

        data = get_sheet_data(self.conn, sheet_id)
        self.assertEqual([d["row_key"] for d in data], ["1", "88", "3", "4"])
        self.assertEqual([d["cells"]["roll_code"]["value"] for d in data], ["1", "88", "3", "4"])
        self.assertTrue(data[0]["cells"]["roll_code"]["inherited"])
        self.assertFalse(data[1]["cells"]["roll_code"]["inherited"])
        self.assertTrue(data[2]["cells"]["roll_code"]["inherited"])
        self.assertTrue(data[3]["cells"]["roll_code"]["inherited"])

        uploads = get_operator_uploads(self.conn, op_id)
        self.assertEqual(len(uploads), 1)
        problems = uploads[0]["problems"]
        self.assertEqual(len(problems), 3)

        prob_by_roll = {p["roll_code"]: p for p in problems}
        self.assertIn("1", prob_by_roll)
        self.assertEqual(prob_by_roll["1"]["problem"], "weight")
        self.assertIn("khối lượng", prob_by_roll["1"]["label"].lower())

        self.assertIn("88", prob_by_roll)
        self.assertEqual(prob_by_roll["88"]["problem"], "thickness")
        self.assertIn("độ dày", prob_by_roll["88"]["label"].lower())

        self.assertIn("3", prob_by_roll)
        self.assertEqual(prob_by_roll["3"]["problem"], "unreadable")
        self.assertIn("không đọc được", prob_by_roll["3"]["label"].lower())

        self.assertNotIn("4", prob_by_roll)


class Recovery(WorkerTest):
    def test_job_left_running_by_a_crash_is_requeued_and_completed(self):
        sheet_id, job_id = self.queue()
        jobs.claim_job(self.conn)
        self.assertEqual(jobs.recover_running_jobs(self.conn), 1)
        self.assertEqual(get_read_job_by_id(self.conn, job_id)["status"], "queued")
        reader = self.drain(result(row(roll_code="1")))
        job = get_read_job_by_id(self.conn, job_id)
        self.assertEqual((job["status"], job["attempts"], len(reader.paths)), ("done", 2, 1))
        self.assertEqual(get_sheet_by_id(self.conn, sheet_id)["status"], "needs_review")

    def test_job_whose_last_attempt_crashed_fails_the_sheet(self):
        sheet_id, job_id = self.queue()
        for _ in range(jobs.MAX_ATTEMPTS - 1):
            jobs.claim_job(self.conn)
            self.conn.execute("UPDATE read_jobs SET status = 'queued' WHERE id = ?", (job_id,))
        jobs.claim_job(self.conn)  # the last attempt, cut off by a crash
        jobs.recover_running_jobs(self.conn)
        job = get_read_job_by_id(self.conn, job_id)
        self.assertEqual((job["status"], job["attempts"]), ("failed", jobs.MAX_ATTEMPTS))
        self.assertEqual(get_sheet_by_id(self.conn, sheet_id)["status"], "failed")


class Entry(WorkerTest):
    def test_worker_code_does_not_import_the_web_package(self):
        violations = []
        for path in (REPO_ROOT / "hasc" / "worker").rglob("*.py"):
            for name in extract_imports(path.read_text(encoding="utf-8"), path):
                if name == "hasc.web" or name.startswith("hasc.web."):
                    violations.append((path.name, name))
        self.assertEqual(violations, [])

    def test_once_drains_the_queue_with_the_injected_reader(self):
        _, job_id = self.queue()
        reader = FakeReader(result(row(roll_code="1")))
        with mock.patch.dict(os.environ, {"HASC_DB_PATH": str(self.db_path)}):
            main(["--once"], reader_factory=lambda: reader)
        self.assertEqual(get_read_job_by_id(self.conn, job_id)["status"], "done")

    def test_missing_api_key_stops_the_worker_and_names_the_setting(self):
        env_path = self.root / "worker.env"
        env_path.write_text("HASC_READER_MODEL=some/model\nHASC_KNOWN_NAMES=names.json\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {config.ENV_VAR: str(env_path)}, clear=True):
            with self.assertRaises(SystemExit) as caught:
                config.cloud_reader_from_env()
        self.assertIn("OPENROUTER_API_KEY missing", str(caught.exception))

    def test_env_var_names_the_settings_file(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as caught:
                config.cloud_reader_from_env()
        self.assertIn(config.ENV_VAR, str(caught.exception))

    def test_cloud_reader_from_env_reads_a_photo_through_the_cloud_reader(self):
        names = self.root / "names.json"
        names.write_text(json.dumps({"customers": {}, "products": {}}), encoding="utf-8")
        env_path = self.root / "worker.env"
        env_path.write_text(f"OPENROUTER_API_KEY=test-key\nHASC_READER_MODEL=some/model\n"
                            f"HASC_KNOWN_NAMES={names}\n", encoding="utf-8")
        bodies = []
        reply = {"choices": [{"message": {"content": "layout=form_a\nroll_code=1 gross_kg=15,8 core_kg=2,2 "
                                                     "cover_kg=0,4 net_kg=13,2"}}]}

        def transport(body):
            bodies.append(body)
            return reply

        photo_path = self.root / "photo.jpg"
        photo_path.write_bytes(jpeg(200, 100))
        with mock.patch.dict(os.environ, {config.ENV_VAR: str(env_path)}, clear=True):
            with mock.patch.object(config, "openrouter_transport", return_value=transport):
                reader = config.cloud_reader_from_env()
        out = reader.read(str(photo_path))
        self.assertEqual(out["rows"][0]["cells"]["net_kg"]["value"], 13.2)
        self.assertEqual(out["reader_name"], "some/model")
        self.assertEqual(bodies[0]["provider"], {"data_collection": "deny"})
        self.assertIsInstance(reader.cloud, CloudReader)


if __name__ == "__main__":
    unittest.main()
