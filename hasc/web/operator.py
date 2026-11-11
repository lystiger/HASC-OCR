from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from PIL import ExifTags, Image, ImageFilter, ImageStat

from hasc.db import (
    create_event,
    create_photo,
    create_read_job,
    create_sheet,
    find_sheet,
    get_connection,
    get_default_photos_dir,
)
from hasc.images import display_copy
from hasc.web.auth import get_active_user, get_authenticated_user, get_db
from hasc.web.errors import AppError
from hasc.web.roles import require_role

router = APIRouter()

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB per spec Functional / criterion 14
ALLOWED_MACHINES = ("T1", "T3", "T6")
MAX_PAPERS = 50  # far more papers than one machine fills in a shift


def get_shift_and_date(now_dt: datetime | None = None) -> tuple[int, str, str]:
    """Calculate pre-selected shift, production_date (YYYY-MM-DD), and date display (DD/MM).
    - Shift 1: 07:00 to 19:00 (7am-7pm)
    - Shift 2: 19:00 to 07:00 (7pm-7am)
    - Date: between midnight (00:00) and 07:00 defaults to yesterday.
    """
    if now_dt is None:
        now_dt = datetime.now().astimezone()

    hour = now_dt.hour
    if 0 <= hour < 7:
        shift = 2
        prod_date = now_dt.date() - timedelta(days=1)
    elif 7 <= hour < 19:
        shift = 1
        prod_date = now_dt.date()
    else:
        shift = 2
        prod_date = now_dt.date()

    date_str = prod_date.strftime("%Y-%m-%d")
    date_display = f"{prod_date.day:02d}/{prod_date.month:02d}"
    return shift, date_str, date_display


def get_remembered_machine(
    conn: sqlite3.Connection, user_id: int, request: Request | None = None
) -> str:
    """Pre-select the machine this account chose last, defaulting to T1.

    Only front pages count (odd Trang, P121): a back photo may be on another machine (P101) and is
    saved after its front, so the last photo alone would offer the back's machine next time.
    """
    cursor = conn.execute(
        """
        SELECT s.machine
        FROM photos p
        JOIN sheets s ON p.sheet_id = s.id
        WHERE p.uploaded_by = ? AND s.page_no % 2 = 1
        ORDER BY p.id DESC
        LIMIT 1
        """,
        (user_id,),
    )
    row = cursor.fetchone()
    if row and row["machine"] in ALLOWED_MACHINES:
        return row["machine"]

    if request:
        cookies = getattr(request, "cookies", None)
        if cookies:
            cookie_val = cookies.get(f"last_machine_{user_id}")
            if cookie_val in ALLOWED_MACHINES:
                return cookie_val

    return "T1"


def parse_exif_datetime(dt_str: str, offset_str: str | None = None) -> str | None:
    """Parse EXIF datetime (YYYY:MM:DD HH:MM:SS) into ISO 8601 string."""
    if not dt_str or not isinstance(dt_str, str):
        return None
    dt_str = dt_str.strip()
    if len(dt_str) >= 19 and dt_str[4] == ":" and dt_str[7] == ":":
        iso_base = f"{dt_str[:4]}-{dt_str[5:7]}-{dt_str[8:10]}T{dt_str[11:19]}"
        if offset_str and isinstance(offset_str, str):
            offset_str = offset_str.strip()
            if offset_str.startswith(("+", "-")) and len(offset_str) in (5, 6):
                return f"{iso_base}{offset_str}"
        return iso_base
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.isoformat()
    except Exception:
        return dt_str


def extract_exif_camera_time(file_bytes: bytes) -> str | None:
    """Extract camera time from EXIF data when present."""
    # Try Pillow getexif
    try:
        with Image.open(io.BytesIO(file_bytes)) as img:
            exif = img.getexif()
            if exif:
                ifd = exif.get_ifd(ExifTags.IFD.Exif) if hasattr(exif, "get_ifd") else {}
                dt_orig = (
                    ifd.get(ExifTags.Base.DateTimeOriginal)
                    or exif.get(ExifTags.Base.DateTimeOriginal)
                    or exif.get(ExifTags.Base.DateTime)
                )
                offset = (
                    ifd.get(36881)
                    or exif.get(36881)
                    or ifd.get(36880)
                    or exif.get(36880)
                )
                if dt_orig:
                    return parse_exif_datetime(str(dt_orig), str(offset) if offset else None)
    except Exception:
        pass

    # Regex scan for standard EXIF timestamp pattern in binary payload
    match = re.search(rb"(20\d\d:[01]\d:[0-3]\d [0-2]\d:[0-5]\d:[0-5]\d)", file_bytes)
    if match:
        try:
            return parse_exif_datetime(match.group(1).decode("ascii"))
        except Exception:
            pass
    return None


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


def check_image_quality(file_bytes: bytes) -> list[str]:
    """Test blur, darkness, resolution, and form table structure.
    Returns Vietnamese warnings; never blocks upload (criterion 13).
    """
    warnings: list[str] = []
    try:
        with Image.open(io.BytesIO(file_bytes)) as img:
            width, height = img.size
            if width < 800 or height < 600:
                warnings.append("Độ phân giải ảnh thấp, nên chụp ảnh lớn và rõ hơn.")

            gray = img.convert("L")
            stat = ImageStat.Stat(gray)
            mean_luminance = stat.mean[0]
            if mean_luminance < 40:
                warnings.append("Ảnh bị tối, nên chụp lại ở nơi đủ ánh sáng.")

            stddev = stat.stddev[0]
            if stddev < 10:
                warnings.append("Không phát hiện cấu trúc biểu mẫu trong ảnh, vui lòng chụp toàn bộ trang giấy.")
            else:
                edges = gray.filter(ImageFilter.FIND_EDGES)
                estat = ImageStat.Stat(edges)
                edge_var = estat.var[0]
                if edge_var < 500:
                    warnings.append("Ảnh bị mờ, vui lòng giữ chắc máy khi chụp để bảng biểu rõ nét.")
    except Exception:
        pass

    return warnings


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


def get_operator_uploads(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Get upload history for an operator.
    After reading shows rolls that failed a rule as roll code plus problem type only, with no values.
    AI doubt alone is not listed (criterion 55).
    """
    cursor = conn.execute(
        """
        SELECT s.id as sheet_id, s.machine, s.production_date, s.shift, s.page_no, s.status,
               MAX(p.uploaded_at) as uploaded_at
        FROM sheets s
        JOIN photos p ON s.id = p.sheet_id
        WHERE p.uploaded_by = ?
        GROUP BY s.id
        ORDER BY uploaded_at DESC, s.id DESC
        """,
        (user_id,),
    )
    rows = cursor.fetchall()
    results = []

    status_labels = {
        "received": "Đã nhận",
        "reading": "Đang đọc",
        "needs_review": "Cần kiểm tra",
        "in_review": "Đang kiểm tra",
        "reviewed": "Đã kiểm tra",
        "failed": "Lỗi",
    }

    for r in rows:
        sheet_id = r["sheet_id"]
        prob_cursor = conn.execute(
            """
            SELECT r.row_key, c.field, c.flag, c.note
            FROM rows r
            JOIN cells c ON r.id = c.row_id
            WHERE r.sheet_id = ? AND r.invalid = 0
              AND c.flag IN ('rule', 'unreadable')
            ORDER BY r.position ASC, r.id ASC
            """,
            (sheet_id,),
        )
        cell_rows = prob_cursor.fetchall()
        problems = []
        seen = set()

        for c in cell_rows:
            roll_code = c["row_key"]
            flag = c["flag"]
            field = c["field"]
            note = (c["note"] or "").lower()

            if flag == "unreadable":
                prob_type = "unreadable"
                label = "Không đọc được"
            elif flag == "rule":
                if (
                    field in ("gross_kg", "core_kg", "cover_kg", "net_kg")
                    or "weight" in note
                    or "khối lượng" in note
                ):
                    prob_type = "weight"
                    label = "Khối lượng không khớp"
                elif (
                    field
                    in (
                        "thickness_avg",
                        "thickness_mic",
                        "thickness_1",
                        "thickness_2",
                        "thickness_3",
                        "thickness_4",
                        "thickness_5",
                        "thickness_6",
                    )
                    or "thick" in note
                    or "độ dày" in note
                ):
                    prob_type = "thickness"
                    label = "Độ dày không khớp"
                else:
                    prob_type = "rule"
                    label = "Sai quy tắc"
            else:
                continue

            key = (roll_code, prob_type)
            if key not in seen:
                seen.add(key)
                problems.append({
                    "roll_code": roll_code,
                    "problem": prob_type,
                    "label": label,
                })

        results.append({
            "sheet_id": sheet_id,
            "machine": r["machine"],
            "production_date": r["production_date"],
            "shift": r["shift"],
            "page_no": r["page_no"],
            "status": r["status"],
            "status_vi": status_labels.get(r["status"], r["status"]),
            "uploaded_at": r["uploaded_at"],
            "problems": problems,
        })

    return results


def process_photo_upload(
    conn: sqlite3.Connection,
    photos_dir: Path,
    user_id: int,
    file_bytes: bytes,
    filename: str | None,
    machine: str,
    production_date: str,
    shift: int,
    page_no: int,
    opened_at: str | None,
) -> tuple[int, int, str, list[str]]:
    """Validate, save byte-identical original, store EXIF camera time, create sheet,
    create read job unless an identical photo was already read for this sheet.
    Returns (photo_id, sheet_id, status, warnings).
    """
    if machine not in ALLOWED_MACHINES:
        raise AppError(
            code="invalid_machine",
            message=f"Máy không hợp lệ (chỉ chấp nhận {', '.join(ALLOWED_MACHINES)})",
            status_code=400,
        )

    if shift not in (1, 2):
        raise AppError(
            code="invalid_shift",
            message="Ca làm việc không hợp lệ (chỉ chấp nhận Ca 1 hoặc Ca 2)",
            status_code=400,
        )

    try:
        datetime.strptime(production_date, "%Y-%m-%d")
    except ValueError:
        raise AppError(
            code="invalid_date",
            message="Ngày sản xuất không hợp lệ (định dạng YYYY-MM-DD)",
            status_code=400,
        )

    if page_no < 1:
        raise AppError(
            code="invalid_page",
            message="Số trang không hợp lệ (tối thiểu là 1)",
            status_code=400,
        )

    if len(file_bytes) > MAX_FILE_SIZE:
        raise AppError(
            code="file_too_large",
            message="Tập tin vượt quá dung lượng tối đa 20 MB",
            status_code=400,
        )

    valid, _ = is_valid_image(file_bytes)
    if not valid:
        raise AppError(
            code="invalid_image",
            message="Tập tin không phải là hình ảnh hợp lệ",
            status_code=400,
        )

    # 1. Save original byte-identical first
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    saved_path = save_photo_file(photos_dir, file_bytes, filename, sha256)

    # 1b. Create upright display copy
    small_copy_path = None
    try:
        upright_bytes = display_copy(file_bytes)
        upright_file = (photos_dir / f"{sha256}_display.jpg").resolve()
        upright_file.write_bytes(upright_bytes)
        small_copy_path = str(upright_file)
    except Exception:
        pass

    # 2. Extract EXIF camera time
    taken_at = extract_exif_camera_time(file_bytes)

    # 3. Quality warnings (never blocks)
    warnings = check_image_quality(file_bytes)

    # 4. Upload ms calculation
    upload_ms = None
    if opened_at:
        try:
            opened_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
            now_dt = datetime.now(timezone.utc)
            upload_ms = max(0, int((now_dt - opened_dt).total_seconds() * 1000))
        except Exception:
            pass

    # 5. Create or join sheet
    form = "BMMT01-2022/HASC"
    sheet = find_sheet(conn, form, machine, production_date, shift, page_no)
    if not sheet:
        sheet_id = create_sheet(
            conn,
            form=form,
            machine=machine,
            production_date=production_date,
            shift=shift,
            page_no=page_no,
            status="received",
            version=1,
        )
        sheet_status = "received"
    else:
        sheet_id = sheet["id"]
        sheet_status = sheet["status"]

    # 6. Save photo record
    photo_id = create_photo(
        conn,
        sheet_id=sheet_id,
        path=str(saved_path),
        small_copy_path=small_copy_path,
        sha256=sha256,
        uploaded_by=user_id,
        taken_at=taken_at,
        upload_ms=upload_ms,
        quality_warnings=json.dumps(warnings) if warnings else None,
    )
    create_event(
        conn,
        user_id=user_id,
        action="upload",
        sheet_id=sheet_id,
        detail=f"Tải lên ảnh {photo_id}",
    )

    # 7. Create read job unless an identical photo was already read or queued for this sheet (P116)
    cursor = conn.execute(
        """
        SELECT rj.id
        FROM read_jobs rj
        JOIN photos p ON rj.photo_id = p.id
        WHERE p.sha256 = ? AND p.sheet_id = ? AND p.id != ?
        LIMIT 1
        """,
        (sha256, sheet_id, photo_id),
    )
    already_read = cursor.fetchone() is not None
    if not already_read:
        create_read_job(conn, photo_id=photo_id, status="queued")

    return photo_id, sheet_id, sheet_status, warnings


# -----------------------------------------------------------------------------
# API Endpoints
# -----------------------------------------------------------------------------


@router.post("/api/photos", status_code=201)
def api_upload_photo(
    request: Request,
    file: UploadFile = File(...),
    machine: str = Form(...),
    production_date: str = Form(...),
    shift: int = Form(...),
    page_no: int = Form(1),
    opened_at: Optional[str] = Form(None),
    user: dict = Depends(require_role("upload_photo")),
    conn: sqlite3.Connection = Depends(get_db),
):
    photos_dir = getattr(request.app.state, "photos_dir", None)
    if not photos_dir:
        photos_dir = get_default_photos_dir(db_path=getattr(request.app.state, "db_path", None))
    photos_dir = Path(photos_dir).resolve()

    file_bytes = file.file.read()
    photo_id, sheet_id, status, warnings = process_photo_upload(
        conn=conn,
        photos_dir=photos_dir,
        user_id=user["id"],
        file_bytes=file_bytes,
        filename=file.filename,
        machine=machine,
        production_date=production_date,
        shift=shift,
        page_no=page_no,
        opened_at=opened_at,
    )

    response = JSONResponse(
        status_code=201,
        content={
            "photo_id": photo_id,
            "sheet_id": sheet_id,
            "status": status,
            "warnings": warnings,
        },
    )
    response.set_cookie(
        key=f"last_machine_{user['id']}",
        value=machine,
        max_age=30 * 86400,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/api/uploads/mine")
def api_uploads_mine(
    user: dict = Depends(require_role("view_own_uploads")),
    conn: sqlite3.Connection = Depends(get_db),
):
    return get_operator_uploads(conn, user["id"])


# -----------------------------------------------------------------------------
# HTML Endpoints & HTMX Polling
# -----------------------------------------------------------------------------


def render_operator_screen(
    request: Request,
    user: dict,
    conn: sqlite3.Connection,
    error: str | None = None,
    success_message: str | None = None,
    warnings: list[str] | None = None,
    override_now: datetime | None = None,
) -> HTMLResponse:
    default_shift, default_date, default_date_display = get_shift_and_date(override_now)
    default_machine = get_remembered_machine(conn, user["id"], request)
    default_paper = 1
    button_label = f"Gửi: {default_machine} · Ca {default_shift} · {default_date_display} · Tờ {default_paper}"

    uploads = get_operator_uploads(conn, user["id"])
    templates = request.app.state.templates

    frame_template = (
        "office_base.html"
        if user.get("role") in ("employee", "boss", "admin")
        else "base.html"
    )
    return templates.TemplateResponse(
        request=request,
        name="operator.html",
        context={
            "user": user,
            "frame_template": frame_template,
            "default_machine": default_machine,
            "default_shift": default_shift,
            "default_date": default_date,
            "default_date_display": default_date_display,
            "default_paper": default_paper,
            "button_label": button_label,
            "uploads": uploads,
            "error": error,
            "success_message": success_message,
            "warnings": warnings or [],
        },
    )


@router.get("/operator", response_class=HTMLResponse)
@router.get("/office/upload", response_class=HTMLResponse)
def operator_page(
    request: Request,
    user: dict = Depends(require_role("upload_photo")),
    conn: sqlite3.Connection = Depends(get_db),
):
    return render_operator_screen(request, user, conn)


@router.get("/operator/uploads", response_class=HTMLResponse)
def operator_uploads_partial(
    request: Request,
    user: dict = Depends(require_role("view_own_uploads")),
    conn: sqlite3.Connection = Depends(get_db),
):
    uploads = get_operator_uploads(conn, user["id"])
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="operator_uploads_partial.html",
        context={
            "uploads": uploads,
        },
    )


def _chosen(*uploads: Optional[UploadFile]) -> tuple[Optional[UploadFile], bytes]:
    """The first input that holds a photo; a browser sends an unused file input as an empty part."""
    for upload in uploads:
        if upload is not None and upload.filename:
            content = upload.file.read()
            if content:
                return upload, content
    return None, b""


def paper_pages(paper_no: int) -> tuple[int, int]:
    """Paper n is two pages: its front is Trang 2n-1 and its back Trang 2n (P121)."""
    return 2 * paper_no - 1, 2 * paper_no


@router.post("/operator/upload", response_class=HTMLResponse)
def operator_upload_submit(
    request: Request,
    file_front: Optional[UploadFile] = File(None),
    file_front_saved: Optional[UploadFile] = File(None),
    file_back: Optional[UploadFile] = File(None),
    file_back_saved: Optional[UploadFile] = File(None),
    machine: str = Form(...),
    back_machine: Optional[str] = Form(None),
    production_date: str = Form(...),
    shift: int = Form(...),
    paper_no: int = Form(1),
    opened_at: Optional[str] = Form(None),
    user: dict = Depends(require_role("upload_photo")),
    conn: sqlite3.Connection = Depends(get_db),
):
    """One paper: Ảnh 1 is its front, Ảnh 2 (optional) its back, which may be another machine (P101).

    Each side has two inputs: `file_<side>` opens the camera, `file_<side>_saved` picks a saved photo (P122).
    """
    photos_dir = getattr(request.app.state, "photos_dir", None)
    if not photos_dir:
        photos_dir = get_default_photos_dir(db_path=getattr(request.app.state, "db_path", None))
    photos_dir = Path(photos_dir).resolve()

    if not 1 <= paper_no <= MAX_PAPERS:
        return render_operator_screen(request, user, conn, error=f"Tờ số phải từ 1 đến {MAX_PAPERS}")
    back_machine = back_machine or machine  # empty means "same as the front"
    if machine not in ALLOWED_MACHINES or back_machine not in ALLOWED_MACHINES:
        return render_operator_screen(request, user, conn, error="Máy không hợp lệ")
    front_page, back_page = paper_pages(paper_no)
    front, front_bytes = _chosen(file_front, file_front_saved)
    back, back_bytes = _chosen(file_back, file_back_saved)
    if front is None:
        return render_operator_screen(request, user, conn, error="Ảnh 1 (mặt trước): chưa chọn ảnh")
    sides = [("Ảnh 1 (mặt trước)", front, machine, front_page)]
    if back is not None:
        sides.append(("Ảnh 2 (mặt sau)", back, back_machine, back_page))

    # Check every photo before saving any, so a bad back photo does not leave the front sent alone.
    contents = [front_bytes] + ([back_bytes] if back is not None else [])
    for (label, *_), file_bytes in zip(sides, contents):
        if len(file_bytes) > MAX_FILE_SIZE or not is_valid_image(file_bytes)[0]:
            reason = "Tập tin vượt quá dung lượng tối đa 20 MB" if len(file_bytes) > MAX_FILE_SIZE else "Tập tin không phải là hình ảnh hợp lệ"
            return render_operator_screen(request, user, conn, error=f"{label}: {reason}")

    sent, warnings = [], []
    for (label, upload, side_machine, page_no), file_bytes in zip(sides, contents):
        try:
            _, _, _, side_warnings = process_photo_upload(
                conn=conn,
                photos_dir=photos_dir,
                user_id=user["id"],
                file_bytes=file_bytes,
                filename=upload.filename,
                machine=side_machine,
                production_date=production_date,
                shift=shift,
                page_no=page_no,
                opened_at=opened_at,
            )
        except AppError as e:
            done = f"Đã gửi {', '.join(sent)}. " if sent else ""
            return render_operator_screen(request, user, conn, error=f"{done}{label}: {e.message}")
        sent.append(f"{label} {side_machine}")
        warnings += [f"{label}: {w}" for w in side_warnings]

    success_msg = f"Đã gửi thành công: {' · '.join(sent)} · Ca {shift} · {production_date} · Tờ {paper_no}"
    response = render_operator_screen(
        request, user, conn, success_message=success_msg, warnings=warnings
    )
    response.set_cookie(
        key=f"last_machine_{user['id']}",
        value=machine,
        max_age=30 * 86400,
        httponly=True,
        samesite="lax",
    )
    return response
