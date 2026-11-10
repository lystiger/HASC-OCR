import gc
import io
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from hasc.db import create_sheet, create_user, get_connection
from hasc.web.app import create_app
from hasc.web.security import hash_password


def make_test_table_jpeg() -> bytes:
    img = Image.new("L", (900, 700), 255)
    for y in range(50, 650, 30):
        img.paste(0, (50, y, 850, y + 2))
    for x in range(50, 850, 50):
        img.paste(0, (x, 50, x + 2, 650))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


class TestOfficeFrame(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.photos_dir = Path(self.temp_dir.name) / "photos"
        self.photos_dir.mkdir(parents=True, exist_ok=True)

        self.app = create_app(db_path=self.db_path, photos_dir=self.photos_dir)

        conn = get_connection(self.db_path)
        self.users = {
            "operator": create_user(conn, "op_user", hash_password("pass"), role="operator", active=1),
            "employee": create_user(conn, "emp_user", hash_password("pass"), role="employee", active=1),
            "boss": create_user(conn, "boss_user", hash_password("pass"), role="boss", active=1),
            "admin": create_user(conn, "admin_user", hash_password("pass"), role="admin", active=1),
        }
        create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-11-06",
            shift=1,
            page_no=1,
            status="needs_review",
            version=1,
        )

        self.clients = {}
        for role_name, username in [
            ("operator", "op_user"),
            ("employee", "emp_user"),
            ("boss", "boss_user"),
            ("admin", "admin_user"),
        ]:
            client = TestClient(self.app)
            res = client.post("/api/login", json={"username": username, "password": "pass"})
            self.assertEqual(res.status_code, 200, f"Login failed for {role_name}")
            self.clients[role_name] = client

    def tearDown(self):
        gc.collect()
        self.temp_dir.cleanup()

    def test_criterion_13_office_users_can_upload_photo(self):
        """Criterion 13: An employee, a boss and an admin can upload a photo from Tải ảnh lên,
        and the sheet records them as the uploader.
        """
        jpeg_bytes = make_test_table_jpeg()

        for role_name in ("employee", "boss", "admin"):
            client = self.clients[role_name]
            # 1. Check GET /api/limits is allowed
            res_limits = client.get("/api/limits")
            self.assertEqual(res_limits.status_code, 200, f"{role_name} should access /api/limits")

            # 2. Upload via API POST /api/photos
            upload_res = client.post(
                "/api/photos",
                data={
                    "machine": "T1",
                    "production_date": "2026-11-06",
                    "shift": 1,
                    "page_no": 2,
                },
                files={"file": (f"{role_name}.jpg", jpeg_bytes, "image/jpeg")},
            )
            self.assertEqual(upload_res.status_code, 201, f"{role_name} upload failed: {upload_res.text}")
            photo_id = upload_res.json()["photo_id"]
            sheet_id = upload_res.json()["sheet_id"]

            # Verify in DB that uploaded_by is this user
            conn = get_connection(self.db_path)
            row = conn.execute("SELECT uploaded_by FROM photos WHERE id = ?", (photo_id,)).fetchone()
            self.assertEqual(row["uploaded_by"], self.users[role_name])

            # 3. Check GET /api/uploads/mine is allowed
            res_mine = client.get("/api/uploads/mine")
            self.assertEqual(res_mine.status_code, 200)

            # 4. Upload via HTML POST /operator/upload
            form_res = client.post(
                "/operator/upload",
                data={
                    "machine": "T6",
                    "production_date": "2026-11-06",
                    "shift": 2,
                    "paper_no": 1,
                },
                files={"file_front": (f"{role_name}_front.jpg", jpeg_bytes, "image/jpeg")},
            )
            self.assertEqual(form_res.status_code, 200)
            self.assertIn("Đã gửi thành công", form_res.text)

    def test_criterion_14_sidebar_shows_accounts_to_admins_only(self):
        """Criterion 14, as changed by P148: the sidebar shows Quản lý tài khoản to admins only, and
        Tài khoản của tôi to everyone; a non-admin requesting the accounts page gets 403.
        """
        admin_res = self.clients["admin"].get("/office")
        self.assertEqual(admin_res.status_code, 200)
        self.assertIn("Quản lý tài khoản", admin_res.text)
        self.assertIn('href="/admin/accounts"', admin_res.text)

        for role in ("employee", "boss"):
            res = self.clients[role].get("/office")
            self.assertEqual(res.status_code, 200)
            self.assertNotIn("Quản lý tài khoản", res.text)
            self.assertNotIn("/admin/accounts", res.text)
            self.assertIn('href="/account"', res.text)

        # Non-admin requesting the accounts page gets 403
        for role in ("employee", "boss", "operator"):
            res_page = self.clients[role].get("/admin/accounts")
            self.assertEqual(res_page.status_code, 403, f"{role} must get 403 for /admin/accounts")

            res_api = self.clients[role].get("/api/admin/users")
            self.assertEqual(res_api.status_code, 403, f"{role} must get 403 for /api/admin/users")

    def test_criterion_15_office_frame_phone_width_menu(self):
        """Criterion 15: The accounts screen and the frame work at phone width (360 px) without sideways scrolling:
        a test checks the viewport meta tag and the folded menu's markup; the verifier checks the layout at 360 px.
        """
        res = self.clients["admin"].get("/office")
        self.assertEqual(res.status_code, 200)
        html = res.text

        # 1. Viewport meta tag
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1.0"', html)

        # 2. Folded menu markup: toggle button / menu control and sidebar
        self.assertTrue(
            ("menu-toggle" in html or "sidebar-toggle" in html or "office-menu" in html or "menu-btn" in html),
            "Expected folded menu control markup in office frame",
        )
        self.assertIn("sidebar", html.lower())

    def test_office_frame_header_shows_username_and_logout(self):
        """Office frame header contains user's name and log-out link."""
        for role, username in [("employee", "emp_user"), ("boss", "boss_user"), ("admin", "admin_user")]:
            res = self.clients[role].get("/office")
            self.assertEqual(res.status_code, 200)
            html = res.text
            self.assertIn(username, html)
            self.assertIn('href="/logout"', html)
            self.assertIn("Đăng xuất", html)

    def test_office_frame_header_shows_no_role_name(self):
        """Spec 4: 'Pages other than the accounts screen show no role names ('show no roles', Lystiger).'"""
        for role, username in [("employee", "emp_user"), ("boss", "boss_user"), ("admin", "admin_user")]:
            res = self.clients[role].get("/office")
            self.assertEqual(res.status_code, 200)
            html = res.text
            # Should not display role labels like 'Văn phòng', 'Ban giám đốc', 'Quản trị' in header/frame
            self.assertNotIn("Văn phòng", html)
            self.assertNotIn("Ban giám đốc", html)
            self.assertNotIn("Quản trị", html)

    def test_office_frame_sidebar_items(self):
        """Sidebar contains Hồ sơ, Báo cáo ngày, Tải ảnh lên, Tài khoản của tôi (and Quản lý tài khoản for admin)."""
        res = self.clients["employee"].get("/office")
        self.assertEqual(res.status_code, 200)
        html = res.text
        self.assertIn("Hồ sơ", html)
        self.assertIn("Báo cáo ngày", html)
        self.assertIn("Tải ảnh lên", html)
        self.assertIn("Tài khoản của tôi", html)
        self.assertNotIn("Quản lý tài khoản", html)

        res_admin = self.clients["admin"].get("/office")
        self.assertEqual(res_admin.status_code, 200)
        html_admin = res_admin.text
        self.assertIn("Hồ sơ", html_admin)
        self.assertIn("Báo cáo ngày", html_admin)
        self.assertIn("Tải ảnh lên", html_admin)
        self.assertIn("Quản lý tài khoản", html_admin)

    def test_office_detail_page_extends_office_frame(self):
        """Existing office detail page /office/sheets/{id} extends the frame."""
        res = self.clients["employee"].get("/office/sheets/1")
        self.assertEqual(res.status_code, 200)
        html = res.text
        self.assertIn("emp_user", html)
        self.assertIn("Hồ sơ", html)
        self.assertIn("Báo cáo ngày", html)
        self.assertIn("Tải ảnh lên", html)
        self.assertIn('href="/logout"', html)

    def test_upload_screen_inside_frame_for_office_users(self):
        """Tải ảnh lên opens the existing upload screen inside the frame."""
        for role, username in [("employee", "emp_user"), ("boss", "boss_user"), ("admin", "admin_user")]:
            res = self.clients[role].get("/operator")
            self.assertEqual(res.status_code, 200)
            html = res.text
            # Existing upload screen content
            self.assertIn("Tải lên biểu mẫu sản xuất", html)
            self.assertIn("Tiêu chuẩn in:", html)
            self.assertIn("Biểu mẫu đã gửi", html)
            # Office frame content
            self.assertIn(username, html)
            self.assertIn("Hồ sơ", html)
            self.assertIn("Báo cáo ngày", html)
            self.assertIn("Tải ảnh lên", html)
            self.assertIn('href="/logout"', html)

    def test_operator_keeps_screen_without_frame(self):
        """Operators keep their current phone screen, without the frame."""
        res = self.clients["operator"].get("/operator")
        self.assertEqual(res.status_code, 200)
        html = res.text
        # Upload screen content is present
        self.assertIn("Tải lên biểu mẫu sản xuất", html)
        self.assertIn("Tiêu chuẩn in:", html)
        self.assertIn('href="/logout"', html)
        # But NO user's name in header
        self.assertNotIn("op_user", html)
        # And NO sidebar
        self.assertNotIn("Hồ sơ", html)
        self.assertNotIn("Tải ảnh lên", html)
        self.assertNotIn("sidebar", html.lower())
