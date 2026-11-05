import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from hasc.db import create_read_job, create_user, get_connection, get_default_photos_dir
from hasc.web.app import create_app
from hasc.web.security import hash_password
from hasc.worker.jobs import process_job

REPO_ROOT = Path(__file__).resolve().parents[1]


def dummy_jpeg() -> bytes:
    # Minimal 1x1 valid JPEG
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00"
        b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19"
        b"\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9="
        b"82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00"
        b"\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04"
        b"\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
    )


class TestPhotoFolder(unittest.TestCase):
    def test_upload_with_hasc_db_path_stores_absolute_path_under_temp_dir(self):
        """When run with no arguments (as in uvicorn hasc.web:create_app --factory),
        create_app takes the photo folder from HASC_DB_PATH's folder, stores absolute paths,
        and never creates data/photos inside the repository."""
        repo_data_photos = REPO_ROOT / "data" / "photos"
        self.assertFalse(repo_data_photos.exists(), "data/photos should not exist in repo before test")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp).resolve()
            db_file = tmp_path / "hasc.db"
            env = {"HASC_DB_PATH": str(db_file)}

            with mock.patch.dict(os.environ, env):
                if "HASC_PHOTOS_DIR" in os.environ:
                    del os.environ["HASC_PHOTOS_DIR"]

                # Factory call with NO arguments, exactly like uvicorn
                app = create_app()

                with TestClient(app) as client:
                    # Seed operator user in the DB
                    conn = get_connection(db_file)
                    create_user(conn, "op_test", hash_password("pass"), role="operator", active=1)
                    conn.close()

                    # Login
                    res_login = client.post("/api/login", json={"username": "op_test", "password": "pass"})
                    self.assertEqual(res_login.status_code, 200)

                    # Upload photo
                    files = {"file": ("sheet.jpg", dummy_jpeg(), "image/jpeg")}
                    data = {
                        "machine": "T1",
                        "production_date": "2026-10-20",
                        "shift": "1",
                        "page_no": "1",
                    }
                    res_upload = client.post("/api/photos", data=data, files=files)
                    self.assertEqual(res_upload.status_code, 201)
                    photo_id = res_upload.json()["photo_id"]

                    # Inspect DB record
                    conn = get_connection(db_file)
                    photo = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
                    conn.close()

                    stored_path = Path(photo["path"])

                    # Assert stored path is absolute
                    self.assertTrue(stored_path.is_absolute(), f"Stored path is not absolute: {stored_path}")

                    # Assert stored path is under the temp dir
                    self.assertTrue(
                        stored_path.is_relative_to(tmp_path),
                        f"Stored path {stored_path} is not under temp dir {tmp_path}",
                    )

                    # Assert the file actually exists on disk
                    self.assertTrue(stored_path.is_file(), f"Photo file not found on disk at {stored_path}")

                    # Assert nothing was created under the repo's data/photos
                    self.assertFalse(
                        repo_data_photos.exists(),
                        f"File or directory was created under repository: {repo_data_photos}",
                    )

    def test_upload_with_hasc_photos_dir_stores_absolute_path_under_temp_dir(self):
        """When HASC_PHOTOS_DIR is set, photos are saved directly under that folder."""
        repo_data_photos = REPO_ROOT / "data" / "photos"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp).resolve()
            db_file = tmp_path / "hasc.db"
            photos_dir = tmp_path / "custom_photos"
            env = {
                "HASC_DB_PATH": str(db_file),
                "HASC_PHOTOS_DIR": str(photos_dir),
            }

            with mock.patch.dict(os.environ, env):
                app = create_app()

                with TestClient(app) as client:
                    conn = get_connection(db_file)
                    create_user(conn, "op_test2", hash_password("pass"), role="operator", active=1)
                    conn.close()

                    res_login = client.post("/api/login", json={"username": "op_test2", "password": "pass"})
                    self.assertEqual(res_login.status_code, 200)

                    files = {"file": ("sheet.jpg", dummy_jpeg(), "image/jpeg")}
                    data = {
                        "machine": "T3",
                        "production_date": "2026-10-21",
                        "shift": "2",
                        "page_no": "1",
                    }
                    res_upload = client.post("/api/photos", data=data, files=files)
                    self.assertEqual(res_upload.status_code, 201)
                    photo_id = res_upload.json()["photo_id"]

                    conn = get_connection(db_file)
                    photo = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
                    conn.close()

                    stored_path = Path(photo["path"])
                    self.assertTrue(stored_path.is_absolute())
                    self.assertTrue(stored_path.is_relative_to(photos_dir))
                    self.assertTrue(stored_path.is_file())
                    self.assertFalse(repo_data_photos.exists())

    def test_worker_reads_photo_from_photo_folder_env(self):
        """Worker claims and reads job when photos_dir is derived from HASC_DB_PATH or HASC_PHOTOS_DIR."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp).resolve()
            db_file = tmp_path / "hasc.db"
            photos_dir = tmp_path / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)

            photo_file = photos_dir / "sample.jpg"
            photo_file.write_bytes(dummy_jpeg())

            conn = get_connection(db_file)
            from hasc.db import create_photo, create_sheet

            sheet_id = create_sheet(
                conn, form="BMMT01-2022/HASC", machine="T1", production_date="2026-10-20", shift=1
            )
            # Store photo with absolute path under temp dir
            photo_id = create_photo(conn, sheet_id=sheet_id, path=str(photo_file), sha256="dummy_hash")
            job_id = create_read_job(conn, photo_id=photo_id)

            env = {"HASC_DB_PATH": str(db_file)}
            with mock.patch.dict(os.environ, env):
                if "HASC_PHOTOS_DIR" in os.environ:
                    del os.environ["HASC_PHOTOS_DIR"]

                scratch = tmp_path / "scratch"
                scratch.mkdir()

                class DummyReader:
                    def read(self, path):
                        return {
                            "layout": "form_a",
                            "page": {},
                            "rows": [],
                            "reader_name": "test-reader",
                        }

                job = conn.execute("SELECT * FROM read_jobs WHERE id = ?", (job_id,)).fetchone()
                status = process_job(conn, job, DummyReader(), scratch)
                self.assertEqual(status, "done")
            conn.close()

    def test_get_default_photos_dir_never_returns_repo_path_by_default(self):
        """When no env vars or arguments are provided, default photo folder is not in repo."""
        with mock.patch.dict(os.environ, {}, clear=True):
            resolved = get_default_photos_dir()
            self.assertTrue(resolved.is_absolute())
            self.assertFalse(resolved.is_relative_to(REPO_ROOT))


if __name__ == "__main__":
    unittest.main()
