"""End-to-end integration tests for HASC production records.

Verifies the full lifecycle:
1. Operator logs in and uploads generated JPEG photo via POST /api/photos.
2. Worker processes the queued job using a fake reader with made-up data.
3. Office user logs in, sees the sheet status as 'Needs review'.
4. Office user downloads the .xlsx workbook and verifies the net-weight column sums correctly.
5. Office user confirms the sheet, transitioning it to 'Reviewed'.

Also validates negative checks:
- Stale version 409 conflict upon confirmation.
- 403 Forbidden for unauthorized role actions across operator, office, and admin.
- Prohibition of real reader or external network usage.
"""

from datetime import date, datetime, timezone
import io
from pathlib import Path
import socket
import tempfile
import unittest

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image

from hasc.db import (
    create_photo,
    create_read_job,
    create_sheet,
    create_user,
    get_connection,
    get_read_job_by_id,
    get_sheet_by_id,
    increment_sheet_version,
)
from hasc.excel.page import FIRST_DATA_ROW
from hasc.reader.reader import CloudReader
from hasc.web.app import create_app
from hasc.web.security import hash_password
from hasc.worker import jobs


def generate_test_sheet_jpeg(width: int = 800, height: int = 600) -> bytes:
    """Generate a clean, high-contrast grid image that passes quality checks."""
    img = Image.new("L", (width, height), 255)
    # Draw dark grid lines representing table rows and columns
    for y in range(40, height - 40, 25):
        img.paste(0, (40, y, width - 40, y + 2))
    for x in range(40, width - 40, 40):
        img.paste(0, (x, 40, x + 2, height - 40))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


class FakeReader:
    """Duck-typed reader returning made-up sheet data without network or model calls."""

    def __init__(self, result_payload: dict):
        self.result_payload = result_payload
        self.invoked_paths: list[str] = []

    def read(self, image_path: str) -> dict:
        self.invoked_paths.append(image_path)
        return self.result_payload


class TestEndToEndFlow(unittest.TestCase):
    def setUp(self):
        # 1. Enforce no network and no real reader use
        self._orig_socket_connect = socket.socket.connect

        orig_connect = self._orig_socket_connect

        def _forbidden_connect(sock, address, *args, **kwargs):
            # Loopback stays allowed: on Windows asyncio builds its internal socket pair over 127.0.0.1.
            if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
                return orig_connect(sock, address, *args, **kwargs)
            raise RuntimeError("Network use is strictly forbidden in e2e tests")

        socket.socket.connect = _forbidden_connect
        self.addCleanup(setattr, socket.socket, "connect", self._orig_socket_connect)

        self._orig_cloud_reader_init = CloudReader.__init__

        def _forbidden_reader_init(*args, **kwargs):
            raise RuntimeError("Real CloudReader use is forbidden in e2e tests")

        CloudReader.__init__ = _forbidden_reader_init
        self.addCleanup(setattr, CloudReader, "__init__", self._orig_cloud_reader_init)

        # 2. Setup isolated temporary storage
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "hasc_e2e.db"
        self.photos_dir = self.root_path / "photos"
        self.scratch_dir = self.root_path / "scratch"
        self.photos_dir.mkdir(parents=True, exist_ok=True)
        self.scratch_dir.mkdir(parents=True, exist_ok=True)

        self.app = create_app(db_path=self.db_path, photos_dir=self.photos_dir)

        # 3. Create initial users with made-up credentials
        conn = get_connection(self.db_path)
        try:
            self.op_user_id = create_user(
                conn,
                username="operator_lead",
                password_hash=hash_password("op_pass_123"),
                role="operator",
                active=1,
            )
            self.office_user_id = create_user(
                conn,
                username="office_reviewer",
                password_hash=hash_password("office_pass_123"),
                role="employee",
                active=1,
            )
            self.admin_user_id = create_user(
                conn,
                username="admin_sys",
                password_hash=hash_password("admin_pass_123"),
                role="admin",
                active=1,
            )
        finally:
            conn.close()

    def _get_db(self):
        conn = get_connection(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def test_e2e_operator_upload_worker_review_and_confirm(self):
        """Full happy path:
        - Operator uploads generated JPEG photo
        - Worker processes job with fake reader
        - Office reviewer sees 'Needs review'
        - Reviewer downloads Excel and verifies net weights sum correctly
        - Reviewer confirms sheet, moving status to 'Reviewed'
        """
        # --- Step 1: Operator logs in ---
        op_client = TestClient(self.app)
        login_res = op_client.post(
            "/api/login",
            json={"username": "operator_lead", "password": "op_pass_123"},
        )
        self.assertEqual(login_res.status_code, 200)

        # --- Step 2: Operator uploads generated JPEG photo ---
        jpeg_bytes = generate_test_sheet_jpeg(width=800, height=600)
        upload_data = {
            "machine": "T1",
            "production_date": "2026-10-25",
            "shift": 1,
            "page_no": 1,
        }
        upload_files = {
            "file": ("production_record_p1.jpg", jpeg_bytes, "image/jpeg"),
        }
        upload_res = op_client.post("/api/photos", data=upload_data, files=upload_files)
        self.assertEqual(upload_res.status_code, 201)
        upload_json = upload_res.json()
        photo_id = upload_json["photo_id"]
        sheet_id = upload_json["sheet_id"]
        self.assertEqual(upload_json["status"], "received")

        # Verify initial database state
        conn = self._get_db()
        sheet = get_sheet_by_id(conn, sheet_id)
        self.assertEqual(sheet["status"], "received")
        self.assertEqual(sheet["version"], 1)

        # --- Step 3: Worker processes the job with fake reader ---
        # Made-up rows: net_kg = gross_kg - (core_kg + cover_kg)
        # Row 1: 25.5 - 2.0 - 0.5 = 23.0
        # Row 2: 30.0 - 2.5 - 0.5 = 27.0
        # Row 3: 18.0 - 1.5 - 0.5 = 16.0
        # Net sum = 23.0 + 27.0 + 16.0 = 66.0
        fake_result = {
            "layout": "form_a",
            "page": {
                "day": {"value": 25, "doubt": False},
                "month": {"value": 10, "doubt": False},
                "year": {"value": 2026, "doubt": False},
            },
            "reader_name": "fake-e2e-reader",
            "rows": [
                {
                    "cells": {
                        "roll_code": {"value": "R-101", "doubt": False},
                        "customer": {"value": "Fictional Packaging Ltd", "doubt": False},
                        "film_type": {"value": "LDPE-Heavy", "doubt": False},
                        "gross_kg": {"value": 25.5, "doubt": False},
                        "core_kg": {"value": 2.0, "doubt": False},
                        "cover_kg": {"value": 0.5, "doubt": False},
                        "net_kg": {"value": 23.0, "doubt": False},
                    }
                },
                {
                    "cells": {
                        "roll_code": {"value": "R-102", "doubt": False},
                        "customer": {"value": "Fictional Packaging Ltd", "doubt": False},
                        "film_type": {"value": "LDPE-Heavy", "doubt": False},
                        "gross_kg": {"value": 30.0, "doubt": False},
                        "core_kg": {"value": 2.5, "doubt": False},
                        "cover_kg": {"value": 0.5, "doubt": False},
                        "net_kg": {"value": 27.0, "doubt": False},
                    }
                },
                {
                    "cells": {
                        "roll_code": {"value": "R-103", "doubt": False},
                        "customer": {"value": "Fictional Packaging Ltd", "doubt": False},
                        "film_type": {"value": "LDPE-Heavy", "doubt": False},
                        "gross_kg": {"value": 18.0, "doubt": False},
                        "core_kg": {"value": 1.5, "doubt": False},
                        "cover_kg": {"value": 0.5, "doubt": False},
                        "net_kg": {"value": 16.0, "doubt": False},
                    }
                },
            ],
        }

        fake_reader = FakeReader(fake_result)
        processed_count = jobs.run_pending(conn, fake_reader, self.scratch_dir)
        self.assertEqual(processed_count, 1)
        self.assertEqual(len(fake_reader.invoked_paths), 1)

        # Worker transitions sheet to needs_review and increments version to 2
        updated_sheet = get_sheet_by_id(conn, sheet_id)
        self.assertEqual(updated_sheet["status"], "needs_review")
        self.assertEqual(updated_sheet["version"], 2)

        # --- Step 4: Office user logs in and sees sheet as Needs review ---
        office_client = TestClient(self.app)
        office_login_res = office_client.post(
            "/api/login",
            json={"username": "office_reviewer", "password": "office_pass_123"},
        )
        self.assertEqual(office_login_res.status_code, 200)

        # Check in HTML office dashboard
        office_page_res = office_client.get("/office")
        self.assertEqual(office_page_res.status_code, 200)
        self.assertIn("Cần kiểm tra", office_page_res.text)
        self.assertIn("T1", office_page_res.text)

        # Check in JSON sheet listing API
        sheets_api_res = office_client.get("/api/sheets")
        self.assertEqual(sheets_api_res.status_code, 200)
        listed_sheet = next(s for s in sheets_api_res.json() if s["id"] == sheet_id)
        self.assertEqual(listed_sheet["status"], "needs_review")

        # Check sheet detail page
        detail_res = office_client.get(f"/office/sheets/{sheet_id}")
        self.assertEqual(detail_res.status_code, 200)
        self.assertIn("Cần kiểm tra", detail_res.text)
        self.assertIn("Đúng, xác nhận", detail_res.text)

        # --- Step 5: Office user downloads .xlsx and checks net-weight column sum ---
        excel_res = office_client.get(f"/api/sheets/{sheet_id}/excel")
        self.assertEqual(excel_res.status_code, 200)
        self.assertEqual(
            excel_res.headers["content-type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("HASC_T1_2026-10-25_Ca1_Trang1.xlsx", excel_res.headers["content-disposition"])

        wb = load_workbook(io.BytesIO(excel_res.content))
        self.assertIn("Báo cáo", wb.sheetnames)
        self.assertIn("_meta", wb.sheetnames)

        # Verify _meta tab contents
        meta_ws = wb["_meta"]
        meta_entries = {row[0]: row[1] for row in meta_ws.iter_rows(values_only=True) if len(row) >= 2}
        self.assertEqual(meta_entries.get("sheet_id"), sheet_id)
        self.assertEqual(meta_entries.get("version"), 2)

        # Check net-weight column (column 13, net_kg)
        report_ws = wb["Báo cáo"]
        net_col = 13
        net_weights = [
            report_ws.cell(row=FIRST_DATA_ROW + i, column=net_col).value
            for i in range(len(fake_result["rows"]))
        ]
        # Ensure numbers are stored as numbers, not strings
        for val in net_weights:
            self.assertIsInstance(val, (int, float), f"Expected numeric net weight, got {type(val)}: {val}")

        self.assertEqual(net_weights, [23.0, 27.0, 16.0])
        total_net_weight = sum(net_weights)
        self.assertAlmostEqual(total_net_weight, 66.0, places=2)

        # --- Step 6: Office user confirms it, and sheet becomes Reviewed ---
        confirm_res = office_client.post(
            f"/api/sheets/{sheet_id}/confirm",
            json={"version": 2},
        )
        self.assertEqual(confirm_res.status_code, 200)
        self.assertEqual(confirm_res.json(), {"version": 2, "status": "reviewed"})

        # Verify sheet status in database
        final_sheet = get_sheet_by_id(conn, sheet_id)
        self.assertEqual(final_sheet["status"], "reviewed")

        # Verify sheet detail API
        detail_api_res = office_client.get(f"/api/sheets/{sheet_id}")
        self.assertEqual(detail_api_res.status_code, 200)
        self.assertEqual(detail_api_res.json()["status"], "reviewed")


class TestNegativeChecks(unittest.TestCase):
    def setUp(self):
        # Enforce no network and no real reader use
        self._orig_socket_connect = socket.socket.connect

        orig_connect = self._orig_socket_connect

        def _forbidden_connect(sock, address, *args, **kwargs):
            # Loopback stays allowed: on Windows asyncio builds its internal socket pair over 127.0.0.1.
            if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
                return orig_connect(sock, address, *args, **kwargs)
            raise RuntimeError("Network use is strictly forbidden in e2e tests")

        socket.socket.connect = _forbidden_connect
        self.addCleanup(setattr, socket.socket, "connect", self._orig_socket_connect)

        self._orig_cloud_reader_init = CloudReader.__init__

        def _forbidden_reader_init(*args, **kwargs):
            raise RuntimeError("Real CloudReader use is forbidden in e2e tests")

        CloudReader.__init__ = _forbidden_reader_init
        self.addCleanup(setattr, CloudReader, "__init__", self._orig_cloud_reader_init)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "hasc_neg.db"
        self.photos_dir = self.root_path / "photos"
        self.photos_dir.mkdir(parents=True, exist_ok=True)

        self.app = create_app(db_path=self.db_path, photos_dir=self.photos_dir)

        conn = get_connection(self.db_path)
        try:
            self.op_id = create_user(
                conn, "op_worker", hash_password("pass"), role="operator", active=1
            )
            self.office_id = create_user(
                conn, "office_worker", hash_password("pass"), role="employee", active=1
            )
            self.admin_id = create_user(
                conn, "admin_user", hash_password("pass"), role="admin", active=1
            )

            # Create a test sheet in needs_review with version 2
            self.sheet_id = create_sheet(
                conn,
                form="BMMT01-2022/HASC",
                machine="T1",
                production_date="2026-10-25",
                shift=1,
                page_no=1,
                status="needs_review",
                version=2,
            )
        finally:
            conn.close()

        # Logged-in clients
        self.op_client = TestClient(self.app)
        self.op_client.post("/api/login", json={"username": "op_worker", "password": "pass"})

        self.office_client = TestClient(self.app)
        self.office_client.post("/api/login", json={"username": "office_worker", "password": "pass"})

        self.admin_client = TestClient(self.app)
        self.admin_client.post("/api/login", json={"username": "admin_user", "password": "pass"})

    def _get_db(self):
        conn = get_connection(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def test_stale_version_409(self):
        """Confirming an out-of-date sheet version is rejected with 409 stale_version."""
        conn = self._get_db()

        # 1. Sheet is version 2, client provides stale version 1 via API
        res_stale_api = self.office_client.post(
            f"/api/sheets/{self.sheet_id}/confirm",
            json={"version": 1},
        )
        self.assertEqual(res_stale_api.status_code, 409)
        err_api = res_stale_api.json()["error"]
        self.assertEqual(err_api["code"], "stale_version")
        self.assertTrue(len(err_api["message"]) > 0)

        # Status remains needs_review
        sheet_after = get_sheet_by_id(conn, self.sheet_id)
        self.assertEqual(sheet_after["status"], "needs_review")

        # 2. HTML form submission with stale version is refused too, back on the sheet page with the reason
        res_stale_html = self.office_client.post(
            f"/office/sheets/{self.sheet_id}/confirm",
            data={"version": "1"},
        )
        self.assertEqual(res_stale_html.status_code, 200)
        self.assertIn("Dữ liệu đã thay đổi", res_stale_html.text)
        self.assertNotIn('{"error"', res_stale_html.text)
        self.assertEqual(get_sheet_by_id(conn, self.sheet_id)["status"], "needs_review")

        # 3. Simulate version bump in database (e.g. data modified to version 3)
        increment_sheet_version(conn, self.sheet_id)
        bumped_sheet = get_sheet_by_id(conn, self.sheet_id)
        self.assertEqual(bumped_sheet["version"], 3)

        # Confirming with previously downloaded version 2 is now rejected as stale
        res_bumped_stale = self.office_client.post(
            f"/api/sheets/{self.sheet_id}/confirm",
            json={"version": 2},
        )
        self.assertEqual(res_bumped_stale.status_code, 409)
        self.assertEqual(res_bumped_stale.json()["error"]["code"], "stale_version")

    def test_403_on_wrong_roles(self):
        """Role enforcement:
        - Operator is forbidden from office views, Excel download, pickup, and confirm.
        - Office employee is forbidden from operator photo uploads.
        - Admin is forbidden from review actions (pickup, confirm, Excel download).
        """
        # --- Operator attempts office endpoints -> 403 Forbidden ---
        self.assertEqual(self.op_client.get("/office").status_code, 403)
        self.assertEqual(self.op_client.get(f"/office/sheets/{self.sheet_id}").status_code, 403)
        self.assertEqual(self.op_client.get("/api/sheets").status_code, 403)
        self.assertEqual(self.op_client.get(f"/api/sheets/{self.sheet_id}/excel").status_code, 403)
        self.assertEqual(self.op_client.get(f"/office/sheets/{self.sheet_id}/excel").status_code, 403)
        self.assertEqual(
            self.op_client.post(f"/api/sheets/{self.sheet_id}/pickup", json={"takeover": False}).status_code,
            403,
        )
        self.assertEqual(
            self.op_client.post(f"/api/sheets/{self.sheet_id}/confirm", json={"version": 2}).status_code,
            403,
        )
        self.assertEqual(
            self.op_client.post(f"/office/sheets/{self.sheet_id}/confirm", data={"version": "2"}).status_code,
            403,
        )

        # Check 403 error payload format
        op_res = self.op_client.get("/api/sheets")
        self.assertEqual(op_res.status_code, 403)
        self.assertEqual(op_res.json()["error"]["code"], "forbidden")

        # --- Office employee photo upload -> 201 (spec 1.1.0 P128, criterion 13) ---
        jpeg_bytes = generate_test_sheet_jpeg()
        upload_res = self.office_client.post(
            "/api/photos",
            data={"machine": "T1", "production_date": "2026-10-25", "shift": 1, "page_no": 1},
            files={"file": ("photo.jpg", jpeg_bytes, "image/jpeg")},
        )
        self.assertEqual(upload_res.status_code, 201)
        self.assertEqual(self.office_client.get("/api/uploads/mine").status_code, 200)

        # --- Admin attempts sheet review actions -> 403 Forbidden ---
        self.assertEqual(
            self.admin_client.post(f"/api/sheets/{self.sheet_id}/pickup", json={"takeover": False}).status_code,
            403,
        )
        self.assertEqual(
            self.admin_client.post(f"/api/sheets/{self.sheet_id}/confirm", json={"version": 2}).status_code,
            403,
        )
        self.assertEqual(self.admin_client.get(f"/api/sheets/{self.sheet_id}/excel").status_code, 403)

    def test_no_reader_or_network_use(self):
        """Verify guards prevent any outbound network connection or real reader initialization."""
        # 1. Any network socket connect must fail
        with self.assertRaises(RuntimeError) as ctx_net:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("8.8.8.8", 80))
        self.assertIn("Network use is strictly forbidden", str(ctx_net.exception))

        # 2. Any CloudReader instantiation must fail
        with self.assertRaises(RuntimeError) as ctx_reader:
            CloudReader(None, None)
        self.assertIn("Real CloudReader use is forbidden", str(ctx_reader.exception))
