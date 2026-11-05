import io
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from hasc.db import (
    create_photo,
    create_sheet,
    create_user,
    get_connection,
)
from hasc.web.app import create_app
from hasc.web.security import hash_password


def make_portrait_jpeg(width: int = 120, height: int = 240) -> bytes:
    """Create a made-up portrait JPEG (height > width)."""
    img = Image.new("RGB", (width, height), color=(240, 240, 240))
    # Add a colored mark at top-left
    for x in range(15):
        for y in range(15):
            img.putpixel((x, y), (0, 50, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class TestUprightPhotos(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name).resolve()
        self.db_path = self.tmp_path / "test.db"
        self.photos_dir = self.tmp_path / "photos"
        self.photos_dir.mkdir(parents=True, exist_ok=True)

        self.app = create_app(db_path=self.db_path, photos_dir=self.photos_dir)
        self.client = TestClient(self.app)

        conn = get_connection(self.db_path)
        self.op_id = create_user(conn, "operator1", hash_password("pass1"), role="operator", active=1)
        self.emp_id = create_user(conn, "employee1", hash_password("pass2"), role="employee", active=1)
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _login(self, username: str, password: str = "pass1") -> TestClient:
        client = TestClient(self.app)
        res = client.post("/api/login", json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200)
        return client

    def test_upload_portrait_jpeg_display_copy_is_landscape_and_original_byte_identical(self):
        """Test with a made-up portrait JPEG that the displayed image is landscape,
        the original file is byte-identical, and small_copy_path is filled (at upload)."""
        portrait_bytes = make_portrait_jpeg(120, 240)
        orig_img = Image.open(io.BytesIO(portrait_bytes))
        self.assertGreater(orig_img.height, orig_img.width, "Test input must be portrait")

        op_client = self._login("operator1", "pass1")
        emp_client = self._login("employee1", "pass2")

        # 1. Operator uploads the portrait JPEG
        upload_res = op_client.post(
            "/api/photos",
            data={
                "machine": "T1",
                "production_date": "2026-10-25",
                "shift": "1",
                "page_no": "1",
            },
            files={"file": ("portrait.jpg", portrait_bytes, "image/jpeg")},
        )
        self.assertEqual(upload_res.status_code, 201)
        upload_data = upload_res.json()
        photo_id = upload_data["photo_id"]
        sheet_id = upload_data["sheet_id"]

        # 2. Verify DB record: small_copy_path is filled
        conn = get_connection(self.db_path)
        photo_row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        conn.close()

        self.assertIsNotNone(photo_row)
        original_disk_path = Path(photo_row["path"])
        small_copy_path_str = photo_row["small_copy_path"]

        self.assertIsNotNone(small_copy_path_str, "small_copy_path must be filled in photos table")
        small_copy_disk_path = Path(small_copy_path_str)

        # 3. Verify original file on disk is byte-identical
        self.assertTrue(original_disk_path.is_file(), f"Original file must exist at {original_disk_path}")
        self.assertEqual(
            original_disk_path.read_bytes(),
            portrait_bytes,
            "Original file on disk must be byte-identical to uploaded bytes",
        )

        # 4. Verify display copy on disk exists and is landscape
        self.assertTrue(small_copy_disk_path.is_file(), f"Display copy file must exist at {small_copy_disk_path}")
        with Image.open(small_copy_disk_path) as display_img:
            self.assertGreater(
                display_img.width,
                display_img.height,
                f"Displayed image must be landscape (width={display_img.width}, height={display_img.height})",
            )

        # 5. Verify office detail page HTML points img at display copy and includes download link
        detail_res = emp_client.get(f"/office/sheets/{sheet_id}")
        self.assertEqual(detail_res.status_code, 200)
        html = detail_res.text
        self.assertIn(f"/api/photos/{photo_id}/display", html, "img src must point at display copy")
        self.assertIn(f"/api/photos/{photo_id}/download", html, "separate download link must exist")

        # 6. Verify display copy endpoint returns landscape image
        res_display = emp_client.get(f"/api/photos/{photo_id}/display")
        self.assertEqual(res_display.status_code, 200)
        with Image.open(io.BytesIO(res_display.content)) as served_img:
            self.assertGreater(
                served_img.width,
                served_img.height,
                "Image served from display route must be landscape",
            )

        # 7. Verify download endpoint returns byte-identical original
        res_download = emp_client.get(f"/api/photos/{photo_id}/download")
        self.assertEqual(res_download.status_code, 200)
        self.assertIn("attachment", res_download.headers.get("content-disposition", ""))
        self.assertEqual(
            res_download.content,
            portrait_bytes,
            "Downloaded file must be byte-identical to original portrait JPEG",
        )

    def test_portrait_photo_created_on_first_view_when_small_copy_path_initially_none(self):
        """Test that if small_copy_path is not populated initially, viewing on office page
        creates the display copy, fills small_copy_path, serves landscape, and keeps original byte-identical."""
        portrait_bytes = make_portrait_jpeg(100, 200)

        # Save original file to photos_dir
        orig_file = self.photos_dir / "sheet_portrait.jpg"
        orig_file.write_bytes(portrait_bytes)

        conn = get_connection(self.db_path)
        sheet_id = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T3",
            production_date="2026-10-26",
            shift=2,
            page_no=1,
            status="needs_review",
            version=1,
        )
        photo_id = create_photo(
            conn,
            sheet_id=sheet_id,
            path=str(orig_file),
            sha256="testportraitsha",
            small_copy_path=None,  # initially None
            uploaded_by=self.op_id,
        )
        conn.close()

        emp_client = self._login("employee1", "pass2")

        # Initially small_copy_path is None
        conn = get_connection(self.db_path)
        row_before = conn.execute("SELECT small_copy_path FROM photos WHERE id = ?", (photo_id,)).fetchone()
        conn.close()
        self.assertIsNone(row_before["small_copy_path"])

        # First view via office detail page
        detail_res = emp_client.get(f"/office/sheets/{sheet_id}")
        self.assertEqual(detail_res.status_code, 200)

        # Now small_copy_path is filled in DB
        conn = get_connection(self.db_path)
        row_after = conn.execute("SELECT small_copy_path FROM photos WHERE id = ?", (photo_id,)).fetchone()
        conn.close()
        self.assertIsNotNone(row_after["small_copy_path"], "small_copy_path must be filled on first view")

        # Display copy is landscape
        display_res = emp_client.get(f"/api/photos/{photo_id}/display")
        self.assertEqual(display_res.status_code, 200)
        with Image.open(io.BytesIO(display_res.content)) as disp_img:
            self.assertGreater(disp_img.width, disp_img.height, "Display image must be landscape")

        # Original file is byte-identical
        self.assertEqual(orig_file.read_bytes(), portrait_bytes)

        # Download route returns byte-identical original
        down_res = emp_client.get(f"/api/photos/{photo_id}/download")
        self.assertEqual(down_res.status_code, 200)
        self.assertEqual(down_res.content, portrait_bytes)


if __name__ == "__main__":
    unittest.main()
