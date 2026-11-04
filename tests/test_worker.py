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
    get_connection,
    get_read_job_by_id,
    get_sheet_by_id,
    get_sheet_data,
)
from hasc.reader.reader import CloudReader, ReaderError
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

    def __init__(self, *outcomes):
        self.outcomes, self.paths, self.sizes = list(outcomes), [], []

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

    def queue(self, shift=1, width=200, height=100):
        sheet_id = create_sheet(self.conn, form="BMMT01-2022/HASC", machine="T6",
                                production_date="2026-10-21", shift=shift)
        path = self.root / f"photo{shift}.jpg"
        path.write_bytes(jpeg(width, height))
        photo_id = create_photo(self.conn, sheet_id=sheet_id, path=str(path), sha256=f"hash{shift}")
        job_id = create_read_job(self.conn, photo_id=photo_id)
        return sheet_id, job_id

    def drain(self, *outcomes):
        reader = FakeReader(*outcomes)
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
        self.queue()
        self.drain(result(row(roll_code="1", gross_kg=17.6, core_kg=0.8, net_kg=1.0), layout="back"))
        self.assertEqual(self.cells(1)[0]["net_kg"]["flag"], "none")


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
