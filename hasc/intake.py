"""Photo intake rules shared by the upload page and the Zalo bot: machines, size limit, image check, saving."""

import io
from pathlib import Path

from PIL import Image

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB per spec Functional / criterion 14
ALLOWED_MACHINES = ("T1", "T3", "T6")


def is_valid_image(file_bytes: bytes) -> tuple[bool, str]:
    """Check if the bytes represent a valid image (JPEG, PNG, HEIC, etc.)."""
    if not file_bytes:
        return False, ""

    try:
        with Image.open(io.BytesIO(file_bytes)) as img:
            fmt = img.format or "JPEG"
            return True, fmt
    except Exception:
        pass

    # Check for HEIC / HEIF ISOBMFF header
    if len(file_bytes) >= 12 and file_bytes[4:8] == b"ftyp":
        major_brand = file_bytes[8:12].lower()
        if major_brand in (b"heic", b"heix", b"mif1", b"msf1", b"hevc", b"hevx"):
            return True, "HEIC"
        box_len = int.from_bytes(file_bytes[0:4], "big")
        compatible = file_bytes[16 : min(box_len, 64)].lower()
        for b in (b"heic", b"heix", b"mif1", b"msf1"):
            if b in compatible:
                return True, "HEIC"

    return False, ""


def save_photo_file(
    photos_dir: Path, file_bytes: bytes, filename: str | None, sha256: str
) -> Path:
    photos_dir = Path(photos_dir).resolve()
    photos_dir.mkdir(parents=True, exist_ok=True)
    ext = ".jpg"
    if filename:
        p_ext = Path(filename).suffix.lower()
        if p_ext in (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"):
            ext = p_ext
    saved_path = (photos_dir / f"{sha256}{ext}").resolve()
    saved_path.write_bytes(file_bytes)
    return saved_path
