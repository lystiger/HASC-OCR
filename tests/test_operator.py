from datetime import datetime, timezone
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from PIL import ExifTags, Image, ImageFilter

from hasc.db import (
    create_photo,
    create_row,
    create_sheet,
    create_user,
    get_connection,
    upsert_cell,
)
from hasc.web.app import create_app
from hasc.web.operator import (
    check_image_quality,
    extract_exif_camera_time,
    get_shift_and_date,
    is_valid_image,
    parse_exif_datetime,
    render_operator_screen,
)
from hasc.web.security import hash_password


def make_test_jpeg(width=800, height=600, color="white", exif_dict=None) -> bytes:
    img = Image.new("RGB", (width, height), color)
    exif = img.getexif()
    if exif_dict:
        ifd = exif.get_ifd(ExifTags.IFD.Exif)
        for tag, val in exif_dict.items():
            if isinstance(tag, str):
                if tag == "DateTimeOriginal":
                    ifd[ExifTags.Base.DateTimeOriginal] = val
                elif tag == "DateTime":
                    exif[ExifTags.Base.DateTime] = val
            elif isinstance(tag, int):
                exif[tag] = val
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def make_test_table_jpeg() -> bytes:
    """Sharp table image that passes quality checks."""
    img = Image.new("L", (900, 700), 255)
    for y in range(50, 650, 30):
        img.paste(0, (50, y, 850, y + 2))
    for x in range(50, 850, 50):
        img.paste(0, (x, 50, x + 2, 650))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


class TestOperatorUpload(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.photos_dir = Path(self.temp_dir.name) / "photos"
        self.app = create_app(db_path=self.db_path, photos_dir=self.photos_dir)

        conn = get_connection(self.db_path)
        self.op1_id = create_user(
            conn, "operator1", hash_password("pass"), role="operator", active=1
        )
        self.op2_id = create_user(
            conn, "operator2", hash_password("pass"), role="operator", active=1
        )
        self.emp_id = create_user(
            conn, "employee1", hash_password("pass"), role="employee", active=1
        )

        self.client_op1 = TestClient(self.app)
        res = self.client_op1.post(
            "/api/login", json={"username": "operator1", "password": "pass"}
        )
        self.assertEqual(res.status_code, 200)

        self.client_op2 = TestClient(self.app)
        res = self.client_op2.post(
            "/api/login", json={"username": "operator2", "password": "pass"}
        )
        self.assertEqual(res.status_code, 200)

        self.client_emp = TestClient(self.app)
        res = self.client_emp.post(
            "/api/login", json={"username": "employee1", "password": "pass"}
        )
        self.assertEqual(res.status_code, 200)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_shift_and_date_clock_logic(self):
        """Test shift pre-selection and date defaulting by the clock:
        - Ca 1: 7am-7pm (07:00 - 18:59)
        - Ca 2: 7pm-7am (19:00 - 06:59)
        - Date: between midnight and 7am defaults to yesterday.
        """
        # 1. Midnight to 7am: e.g. 02:30 on 2026-10-20 -> Ca 2, date is 2026-10-19 (19/10)
        shift, p_date, display = get_shift_and_date(datetime(2026, 10, 20, 2, 30))
        self.assertEqual(shift, 2)
        self.assertEqual(p_date, "2026-10-19")
        self.assertEqual(display, "19/10")

        # 2. 06:59 on 2026-10-20 -> Ca 2, date is yesterday
        shift, p_date, display = get_shift_and_date(datetime(2026, 10, 20, 6, 59, 59))
        self.assertEqual(shift, 2)
        self.assertEqual(p_date, "2026-10-19")
        self.assertEqual(display, "19/10")

        # 3. 07:00 on 2026-10-20 -> Ca 1, date is today (2026-10-20, 20/10)
        shift, p_date, display = get_shift_and_date(datetime(2026, 10, 20, 7, 0))
        self.assertEqual(shift, 1)
        self.assertEqual(p_date, "2026-10-20")
        self.assertEqual(display, "20/10")

        # 4. 14:00 on 2026-10-20 -> Ca 1, date is today
        shift, p_date, display = get_shift_and_date(datetime(2026, 10, 20, 14, 0))
        self.assertEqual(shift, 1)
        self.assertEqual(p_date, "2026-10-20")
        self.assertEqual(display, "20/10")

        # 5. 18:59 on 2026-10-20 -> Ca 1, date is today
        shift, p_date, display = get_shift_and_date(datetime(2026, 10, 20, 18, 59, 59))
        self.assertEqual(shift, 1)
        self.assertEqual(p_date, "2026-10-20")
        self.assertEqual(display, "20/10")

        # 6. 19:00 on 2026-10-20 -> Ca 2, date is today (night shift start date)
        shift, p_date, display = get_shift_and_date(datetime(2026, 10, 20, 19, 0))
        self.assertEqual(shift, 2)
        self.assertEqual(p_date, "2026-10-20")
        self.assertEqual(display, "20/10")

        # 7. 23:30 on 2026-10-20 -> Ca 2, date is today
        shift, p_date, display = get_shift_and_date(datetime(2026, 10, 20, 23, 30))
        self.assertEqual(shift, 2)
        self.assertEqual(p_date, "2026-10-20")
        self.assertEqual(display, "20/10")

    def test_operator_upload_screen_html_and_elements(self):
        """Upload screen must be in Vietnamese, contain machine select (T1, T3, T6),
        page defaulting to 1, file input accepting camera, and submit button labelled properly.
        """
        res = self.client_op1.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("content-type", ""))
        html = res.text

        # Vietnamese texts
        self.assertIn("Tải lên biểu mẫu sản xuất", html)
        self.assertIn("BMMT01-2022/HASC", html)
        self.assertIn("Tiêu chuẩn in:", html)
        self.assertIn("Sai lệch độ dày", html)

        # Machine select options T1, T3, T6
        self.assertIn('<option value="T1"', html)
        self.assertIn('<option value="T3"', html)
        self.assertIn('<option value="T6"', html)

        # Page defaulting to 1
        self.assertIn('name="paper_no"', html)
        self.assertIn('value="1"', html)

        # File input accepting phone camera
        self.assertIn('accept="image/*,.heic,.heif"', html)
        # Each photo has two buttons (P122): the camera input has `capture`, the saved-photo
        # input must not, or Android opens only the camera (spec section 4, P25, P109).
        self.assertEqual(html.count('capture="environment"'), 2)
        self.assertIn("Chụp ảnh", html)
        self.assertIn("Chọn ảnh có sẵn", html)
        import re
        for saved in re.findall(r'<input[^>]*name="file_(?:front|back)_saved"[^>]*>', html):
            self.assertNotIn("capture", saved)

        # Submit button labelled like 'Gửi: T1 · Ca ...'
        self.assertIn("Gửi: T1 · Ca", html)
        self.assertIn("Tờ 1", html)
        self.assertIn("Ảnh 1 · mặt trước", html)
        self.assertIn("Ảnh 2 · mặt sau", html)

        # Also accessible via /operator
        res_op = self.client_op1.get("/operator")
        self.assertEqual(res_op.status_code, 200)
        self.assertIn("Tải lên biểu mẫu sản xuất", res_op.text)

    def test_every_signed_in_page_shows_the_user_and_logout(self):
        html = self.client_op1.get("/operator").text
        self.assertIn('href="/logout"', html)
        self.assertIn("operator1 · Trưởng ca", html)
        import hashlib
        css = Path(__file__).resolve().parents[1] / "hasc" / "web" / "static" / "css" / "app.css"
        self.assertIn(f'href="/static/css/app.css?v={hashlib.sha256(css.read_bytes()).hexdigest()[:10]}"', html)
        office = TestClient(self.app)
        office.post("/api/login", json={"username": "employee1", "password": "pass"})
        for page in ("/office", "/change-password"):
            self.assertIn('href="/logout"', office.get(page).text, page)

    def test_upload_times_show_in_vietnam_time(self):
        from hasc.web.app import vn_date, vn_time
        self.assertEqual(vn_time("2026-11-04T15:31:02.604008+00:00"), "04/11/2026 22:31")
        self.assertEqual(vn_time("2026-11-04T22:30:50"), "04/11/2026 22:30")
        self.assertEqual(vn_date("2026-10-30"), "30/10/2026")

    def send_paper(self, paper_no="1", back=True, back_bytes=None, back_machine="T3"):
        files = {"file_front": ("front.jpg", make_test_table_jpeg(), "image/jpeg")}
        if back:
            files["file_back"] = ("back.jpg", back_bytes or make_test_table_jpeg(), "image/jpeg")
        return self.client_op1.post("/operator/upload", files=files, data={
            "machine": "T6", "back_machine": back_machine, "production_date": "2026-11-04",
            "shift": "1", "paper_no": paper_no})

    def pages(self):
        conn = get_connection(self.db_path)
        rows = conn.execute("SELECT machine, page_no FROM sheets ORDER BY page_no").fetchall()
        conn.close()
        return [(r["machine"], r["page_no"]) for r in rows]

    def test_front_and_back_are_sent_together_as_two_pages(self):
        """P121: one Gửi sends the paper's front (Trang 1) and back (Trang 2); the back has its own machine."""
        res = self.send_paper()
        self.assertIn("Ảnh 1 (mặt trước) T6 · Ảnh 2 (mặt sau) T3", res.text)
        self.assertEqual(self.pages(), [("T6", 1), ("T3", 2)])

    def test_second_paper_is_pages_three_and_four(self):
        self.send_paper(paper_no="2", back_machine="T6")
        self.assertEqual(self.pages(), [("T6", 3), ("T6", 4)])

    def test_back_photo_is_optional(self):
        self.send_paper(back=False)
        self.assertEqual(self.pages(), [("T6", 1)])

    def test_empty_back_part_from_a_browser_is_no_back(self):
        files = {"file_front": ("front.jpg", make_test_table_jpeg(), "image/jpeg"),
                 "file_back": ("", b"", "application/octet-stream")}
        res = self.client_op1.post("/operator/upload", files=files, data={
            "machine": "T6", "back_machine": "", "production_date": "2026-11-04", "shift": "1", "paper_no": "1"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.pages(), [("T6", 1)])
        self.assertEqual(res.cookies.get(f"last_machine_{self.op1_id}"), "T6")

    def test_back_same_as_front_by_default(self):
        self.send_paper(back_machine="")
        self.assertEqual(self.pages(), [("T6", 1), ("T6", 2)])

    def test_bad_paper_number_or_back_machine_sends_nothing(self):
        for paper, back_machine in (("0", "T3"), ("51", "T3"), ("1", "X9")):
            res = self.send_paper(paper_no=paper, back_machine=back_machine)
            self.assertEqual(res.status_code, 200)
        self.assertEqual(self.pages(), [])

    def test_saved_photos_are_sent_like_camera_photos(self):
        files = {"file_front": ("", b"", "application/octet-stream"),
                 "file_front_saved": ("front.jpg", make_test_table_jpeg(), "image/jpeg"),
                 "file_back_saved": ("back.jpg", make_test_table_jpeg(), "image/jpeg")}
        self.client_op1.post("/operator/upload", files=files, data={
            "machine": "T6", "back_machine": "T3", "production_date": "2026-11-04", "shift": "1", "paper_no": "1"})
        self.assertEqual(self.pages(), [("T6", 1), ("T3", 2)])

    def test_no_front_photo_sends_nothing(self):
        res = self.client_op1.post("/operator/upload", files={"file_front": ("", b"", "application/octet-stream")},
                                   data={"machine": "T6", "production_date": "2026-11-04", "shift": "1"})
        self.assertIn("Ảnh 1 (mặt trước): chưa chọn ảnh", res.text)
        self.assertEqual(self.pages(), [])

    def test_a_bad_back_photo_sends_nothing(self):
        res = self.send_paper(back_bytes=b"not an image")
        self.assertIn("Ảnh 2 (mặt sau): Tập tin không phải là hình ảnh hợp lệ", res.text)
        self.assertEqual(self.pages(), [])

    def test_button_label_with_midnight_to_7am_clock(self):
        """Button labelled like 'Gửi: T1 · Ca 2 · 19/10 · Tờ 1' when between midnight and 7am."""
        from starlette.requests import Request as StarletteRequest

        conn = get_connection(self.db_path)
        user = {"id": self.op1_id, "username": "operator1", "role": "operator"}
        clock_dt = datetime(2026, 10, 20, 3, 0)  # 3:00 AM on 20th Oct
        scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "app": self.app}
        req = StarletteRequest(scope)
        response = render_operator_screen(
            request=req,
            user=user,
            conn=conn,
            override_now=clock_dt,
        )
        self.assertIn("Gửi: T1 · Ca 2 · 19/10 · Tờ 1", response.body.decode("utf-8"))

    def test_machine_select_remembered_per_account(self):
        """The machine select (T1, T3, T6) is remembered per account."""
        # Operator 1 uploads with machine T6
        jpeg_bytes = make_test_table_jpeg()
        res = self.client_op1.post(
            "/api/photos",
            data={
                "machine": "T6",
                "production_date": "2026-10-20",
                "shift": "1",
                "page_no": "1",
            },
            files={"file": ("photo.jpg", jpeg_bytes, "image/jpeg")},
        )
        self.assertEqual(res.status_code, 201)

        # Operator 1 visits upload screen -> T6 is pre-selected
        res1 = self.client_op1.get("/")
        self.assertEqual(res1.status_code, 200)
        self.assertIn('<option value="T6" selected>T6</option>', res1.text)
        self.assertIn("Gửi: T6 · Ca", res1.text)

        # Operator 2 visits upload screen -> still defaults to T1
        res2 = self.client_op2.get("/")
        self.assertEqual(res2.status_code, 200)
        self.assertIn('<option value="T1" selected>T1</option>', res2.text)
        self.assertIn("Gửi: T1 · Ca", res2.text)

    def test_post_api_photos_byte_identical_and_sha256(self):
        """POST /api/photos saves the original byte-identical first, stores its sha256."""
        raw_jpeg = make_test_table_jpeg()
        expected_sha = hashlib.sha256(raw_jpeg).hexdigest()

        res = self.client_op1.post(
            "/api/photos",
            data={
                "machine": "T1",
                "production_date": "2026-10-19",
                "shift": "2",
                "page_no": "1",
            },
            files={"file": ("report_photo.jpg", raw_jpeg, "image/jpeg")},
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertIn("photo_id", data)
        self.assertIn("sheet_id", data)
        self.assertEqual(data["status"], "received")
        self.assertEqual(data["warnings"], [])

        # Verify database record
        conn = get_connection(self.db_path)
        cursor = conn.execute("SELECT * FROM photos WHERE id = ?", (data["photo_id"],))
        photo = cursor.fetchone()
        self.assertIsNotNone(photo)
        self.assertEqual(photo["sha256"], expected_sha)
        self.assertEqual(photo["uploaded_by"], self.op1_id)

        # Verify byte-identical on disk (criterion 11)
        saved_file = Path(photo["path"])
        self.assertTrue(saved_file.is_file())
        self.assertEqual(saved_file.read_bytes(), raw_jpeg)

        # Verify read job created
        cursor = conn.execute(
            "SELECT * FROM read_jobs WHERE photo_id = ?", (data["photo_id"],)
        )
        job = cursor.fetchone()
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "queued")

    def test_post_api_photos_stores_exif_camera_time(self):
        """Stores EXIF camera time in photos.taken_at when present."""
        exif_dict = {"DateTimeOriginal": "2026:10:19 14:29:50"}
        raw_jpeg = make_test_jpeg(exif_dict=exif_dict)

        res = self.client_op1.post(
            "/api/photos",
            data={
                "machine": "T3",
                "production_date": "2026-10-19",
                "shift": "1",
                "page_no": "1",
            },
            files={"file": ("exif_photo.jpg", raw_jpeg, "image/jpeg")},
        )
        self.assertEqual(res.status_code, 201)
        photo_id = res.json()["photo_id"]

        conn = get_connection(self.db_path)
        cursor = conn.execute("SELECT taken_at FROM photos WHERE id = ?", (photo_id,))
        row = cursor.fetchone()
        self.assertEqual(row["taken_at"], "2026-10-19T14:29:50")

    def test_post_api_photos_heic_accepted_and_byte_identical(self):
        """Criterion 11: An uploaded HEIC creates a photo record with byte-identical original on disk."""
        heic_bytes = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1" + b"\x00" * 200
        res = self.client_op1.post(
            "/api/photos",
            data={
                "machine": "T1",
                "production_date": "2026-10-19",
                "shift": "1",
                "page_no": "1",
            },
            files={"file": ("camera.HEIC", heic_bytes, "image/heic")},
        )
        self.assertEqual(res.status_code, 201)
        photo_id = res.json()["photo_id"]

        conn = get_connection(self.db_path)
        cursor = conn.execute("SELECT path, sha256 FROM photos WHERE id = ?", (photo_id,))
        row = cursor.fetchone()
        self.assertEqual(row["sha256"], hashlib.sha256(heic_bytes).hexdigest())
        self.assertEqual(Path(row["path"]).read_bytes(), heic_bytes)

    def test_second_upload_identical_photo_starts_no_reading(self):
        """Criterion 56 & task: identical photo (same sha256) is saved but not read again."""
        raw_jpeg = make_test_table_jpeg()

        # First upload
        res1 = self.client_op1.post(
            "/api/photos",
            data={
                "machine": "T1",
                "production_date": "2026-10-19",
                "shift": "1",
                "page_no": "1",
            },
            files={"file": ("first.jpg", raw_jpeg, "image/jpeg")},
        )
        self.assertEqual(res1.status_code, 201)
        photo1_id = res1.json()["photo_id"]

        conn = get_connection(self.db_path)
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM read_jobs WHERE photo_id = ?", (photo1_id,))
        self.assertEqual(cursor.fetchone()["cnt"], 1)

        # Second upload with exact same bytes
        res2 = self.client_op1.post(
            "/api/photos",
            data={
                "machine": "T1",
                "production_date": "2026-10-19",
                "shift": "1",
                "page_no": "1",
            },
            files={"file": ("second.jpg", raw_jpeg, "image/jpeg")},
        )
        self.assertEqual(res2.status_code, 201)
        photo2_id = res2.json()["photo_id"]
        self.assertNotEqual(photo1_id, photo2_id)

        # Photo 2 saved in DB
        cursor = conn.execute("SELECT id FROM photos WHERE id = ?", (photo2_id,))
        self.assertIsNotNone(cursor.fetchone())

        # No read job created for Photo 2!
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM read_jobs WHERE photo_id = ?", (photo2_id,))
        self.assertEqual(cursor.fetchone()["cnt"], 0)

    def test_identical_photo_for_a_different_sheet_is_read_again(self):
        """A photo already read for one sheet and uploaded for another page starts a reading for that page."""
        raw_jpeg = make_test_table_jpeg()
        form = {"machine": "T1", "production_date": "2026-10-19", "shift": "1"}
        first = self.client_op1.post("/api/photos", data={**form, "page_no": "1"},
                                     files={"file": ("a.jpg", raw_jpeg, "image/jpeg")}).json()
        second = self.client_op1.post("/api/photos", data={**form, "page_no": "2"},
                                      files={"file": ("b.jpg", raw_jpeg, "image/jpeg")}).json()
        self.assertNotEqual(first["sheet_id"], second["sheet_id"])
        conn = get_connection(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM read_jobs WHERE photo_id = ?", (second["photo_id"],)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_upload_error_shapes_on_failure(self):
        """Returns the spec error shape on failure: {"error": {"code": "...", "message": "..."}}."""
        # 1. Non-image file (criterion 14)
        res_non_img = self.client_op1.post(
            "/api/photos",
            data={
                "machine": "T1",
                "production_date": "2026-10-19",
                "shift": "1",
                "page_no": "1",
            },
            files={"file": ("test.txt", b"Plain text file not an image", "text/plain")},
        )
        self.assertEqual(res_non_img.status_code, 400)
        data = res_non_img.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"]["code"], "invalid_image")
        self.assertIn("không phải là hình ảnh", data["error"]["message"])

        # No sheet or photo created
        conn = get_connection(self.db_path)
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM photos")
        self.assertEqual(cursor.fetchone()["cnt"], 0)

        # 2. File over 20 MB (criterion 14)
        oversized = b"x" * (20 * 1024 * 1024 + 10)
        res_large = self.client_op1.post(
            "/api/photos",
            data={
                "machine": "T1",
                "production_date": "2026-10-19",
                "shift": "1",
                "page_no": "1",
            },
            files={"file": ("huge.jpg", oversized, "image/jpeg")},
        )
        self.assertEqual(res_large.status_code, 400)
        data = res_large.json()
        self.assertEqual(data["error"]["code"], "file_too_large")
        self.assertIn("20 MB", data["error"]["message"])

        # 3. Invalid machine (e.g. T2)
        res_mach = self.client_op1.post(
            "/api/photos",
            data={
                "machine": "T2",
                "production_date": "2026-10-19",
                "shift": "1",
                "page_no": "1",
            },
            files={"file": ("photo.jpg", make_test_table_jpeg(), "image/jpeg")},
        )
        self.assertEqual(res_mach.status_code, 400)
        self.assertEqual(res_mach.json()["error"]["code"], "invalid_machine")

        # 4. Invalid shift (e.g. 3)
        res_shift = self.client_op1.post(
            "/api/photos",
            data={
                "machine": "T1",
                "production_date": "2026-10-19",
                "shift": "3",
                "page_no": "1",
            },
            files={"file": ("photo.jpg", make_test_table_jpeg(), "image/jpeg")},
        )
        self.assertEqual(res_shift.status_code, 400)
        self.assertEqual(res_shift.json()["error"]["code"], "invalid_shift")

        # 5. Non-operator forbidden
        res_emp = self.client_emp.post(
            "/api/photos",
            data={
                "machine": "T1",
                "production_date": "2026-10-19",
                "shift": "1",
                "page_no": "1",
            },
            files={"file": ("photo.jpg", make_test_table_jpeg(), "image/jpeg")},
        )
        self.assertEqual(res_emp.status_code, 403)
        self.assertEqual(res_emp.json()["error"]["code"], "forbidden")

    def test_quality_warnings_do_not_block_upload(self):
        """Criterion 13: A blurry, dark or non-form image is saved and returns a warning. The upload is not blocked."""
        # Dark image
        dark_jpeg = make_test_jpeg(width=800, height=600, color=(10, 10, 10))
        res = self.client_op1.post(
            "/api/photos",
            data={
                "machine": "T1",
                "production_date": "2026-10-19",
                "shift": "1",
                "page_no": "1",
            },
            files={"file": ("dark.jpg", dark_jpeg, "image/jpeg")},
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertTrue(len(data["warnings"]) > 0)
        self.assertTrue(any("tối" in w.lower() for w in data["warnings"]))

    def test_operator_upload_list_shows_rolls_failed_rule_with_no_values(self):
        """Criterion 55:
        After a reading, the operator's upload status lists each roll that failed a rule
        or was unreadable, as a roll code and a problem type (weight, thickness or unreadable)
        with a Vietnamese label. The response holds no other values, and AI doubt alone is not listed.
        """
        conn = get_connection(self.db_path)
        sheet_id = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-19",
            shift=1,
            page_no=1,
            status="needs_review",
        )
        photo_id = create_photo(
            conn,
            sheet_id=sheet_id,
            path="/tmp/fake.jpg",
            sha256="fake_sha",
            uploaded_by=self.op1_id,
        )

        # Row 1: Roll 1 fails weight rule
        r1 = create_row(conn, sheet_id=sheet_id, row_key="1", position=1)
        upsert_cell(conn, row_id=r1, field="gross_kg", value="15.8", flag="rule", note="weight mismatch")
        upsert_cell(conn, row_id=r1, field="net_kg", value="13.6", flag="none")

        # Row 2: Roll 2 fails thickness rule
        r2 = create_row(conn, sheet_id=sheet_id, row_key="2", position=2)
        upsert_cell(conn, row_id=r2, field="thickness_avg", value="48.0", flag="rule", note="avg thickness rule")

        # Row 3: Roll 3 unreadable
        r3 = create_row(conn, sheet_id=sheet_id, row_key="3", position=3)
        upsert_cell(conn, row_id=r3, field="note_field", value=None, flag="unreadable")

        # Row 4: Roll 4 has AI doubt only (should NOT be listed)
        r4 = create_row(conn, sheet_id=sheet_id, row_key="4", position=4)
        upsert_cell(conn, row_id=r4, field="core_kg", value="2.2", flag="doubt")

        # Row 5: Roll 5 passed all rules
        r5 = create_row(conn, sheet_id=sheet_id, row_key="5", position=5)
        upsert_cell(conn, row_id=r5, field="net_kg", value="14.0", flag="none")

        res = self.client_op1.get("/api/uploads/mine")
        self.assertEqual(res.status_code, 200)
        items = res.json()
        self.assertEqual(len(items), 1)

        upload = items[0]
        self.assertEqual(upload["sheet_id"], sheet_id)
        self.assertEqual(upload["machine"], "T1")
        self.assertEqual(upload["status"], "needs_review")

        problems = upload["problems"]
        self.assertEqual(len(problems), 3)

        prob_by_roll = {p["roll_code"]: p for p in problems}
        self.assertIn("1", prob_by_roll)
        self.assertEqual(prob_by_roll["1"]["problem"], "weight")
        self.assertIn("khối lượng", prob_by_roll["1"]["label"].lower())

        self.assertIn("2", prob_by_roll)
        self.assertEqual(prob_by_roll["2"]["problem"], "thickness")
        self.assertIn("độ dày", prob_by_roll["2"]["label"].lower())

        self.assertIn("3", prob_by_roll)
        self.assertEqual(prob_by_roll["3"]["problem"], "unreadable")
        self.assertIn("không đọc được", prob_by_roll["3"]["label"].lower())

        # Roll 4 (doubt alone) and Roll 5 (clean) MUST NOT be in problems
        self.assertNotIn("4", prob_by_roll)
        self.assertNotIn("5", prob_by_roll)

        # Crucial check: NO NUMERICAL VALUES anywhere in problems!
        for p in problems:
            self.assertNotIn("value", p)
            self.assertNotIn("gross_kg", p)
            self.assertNotIn("15.8", str(p))
            self.assertNotIn("48.0", str(p))

    def test_htmx_polling_partial_and_plain_form_fallback(self):
        """Operator upload list refreshes through htmx polling, falls back to plain forms."""
        # 1. HTMX polling endpoint /operator/uploads returns partial HTML
        res_htmx = self.client_op1.get("/operator/uploads")
        self.assertEqual(res_htmx.status_code, 200)
        self.assertIn("text/html", res_htmx.headers.get("content-type", ""))

        # 2. Plain form submission to /operator/upload
        jpeg_bytes = make_test_table_jpeg()
        res_plain = self.client_op1.post(
            "/operator/upload",
            data={
                "machine": "T3",
                "production_date": "2026-10-19",
                "shift": "2",
                "paper_no": "1",
            },
            files={"file_front": ("plain.jpg", jpeg_bytes, "image/jpeg")},
        )
        self.assertEqual(res_plain.status_code, 200)
        self.assertIn("Đã gửi thành công: Ảnh 1 (mặt trước) T3 · Ca 2", res_plain.text)
        self.assertIn("T3", res_plain.text)

        # Check that the uploaded item appears in the HTMX partial now
        res_after = self.client_op1.get("/operator/uploads")
        self.assertEqual(res_after.status_code, 200)
        self.assertIn("T3", res_after.text)
        self.assertIn("Ca 2", res_after.text)
        self.assertIn("Đã nhận", res_after.text)
