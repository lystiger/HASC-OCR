"""Photo intake rules shared by the upload page, the Zalo bot and the Zalo gate: image checks and saving, then\nhow a gated Zalo photo is dated, paged and filed."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
import io
import json
from pathlib import Path
import re

from PIL import Image

from hasc.images import TURNS, display_copy

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


# ---------------------------------------------------------------------------------------------------------
# Zalo intake (zalo-intake spec R0, R3-R5, R8): what a gated Zalo photo becomes.
#
# Every Zalo sheet comes from exactly one inbox row, so the inbox is the paper index: page numbers, resends and
# back pairing are decided from the inbox rows of one group. Pilot rows are settled as 'filed', staging rows as
# 'staging_checked'; staging is decided the same way but writes nothing except its own inbox row.
# ---------------------------------------------------------------------------------------------------------

VN = timezone(timedelta(hours=7))
SHIFT_LENGTH = timedelta(hours=12)
AGREE_AFTER_END = timedelta(hours=12)  # a sheet is often photographed after its shift ends (R3.4)
LATE_PHOTO = timedelta(hours=2)  # a blank header sent this soon after a shift change belongs to the shift before
LOOKBACK = timedelta(days=7)
BACK_WINDOW = timedelta(minutes=30)

WAIT_NO_HEADER = "không đọc được tiêu đề"
WAIT_BAD_DATE = "ngày trên giấy không hợp lệ"
WAIT_BAD_SHIFT = "ca trên giấy không hợp lệ"
WAIT_DISAGREE = "ngày/ca trên giấy khác giờ gửi"
WAIT_NO_FRONT = "không tìm thấy mặt trước"
WAIT_NO_FILE = "không tìm thấy tệp ảnh"
WAIT_BAD_IMAGE = "không mở được ảnh"
NOT_FORM = "không phải biểu mẫu A"
PAGE_TAKEN = "trang sau của tờ này đã có"
ZALO_FORM = "BMMT01-2022/HASC"

SETTLED = {0: "filed", 1: "staging_checked"}  # inbox status of a decided row, by the staging flag
BY_GATE = ("delivered",)
BY_PERSON = ("delivered", "waiting", "not_form")
FRONT_OUTCOMES = ("new", "resend")
NUMBER = re.compile(r"[0-9]+")
WEIGHT = re.compile(r"-?[0-9]+(?:[.,][0-9]+)?")

Fingerprint = tuple[str | None, float | None, float | None, float | None]


@dataclass(frozen=True)
class Key:
    production_date: date
    shift: int
    doubt: bool  # inferred from the send time, not written


@dataclass(frozen=True)
class Wait:
    reason: str


class Refused(Exception):
    """A person's filing that cannot be done; the message is Vietnamese and the row is unchanged."""


def shift_start(d: date, shift: int) -> datetime:
    return datetime.combine(d, time(7 if shift == 1 else 19), VN)


def shift_of(t: datetime) -> tuple[date, int]:
    local = t.astimezone(VN)
    if local.hour < 7:
        return local.date() - timedelta(days=1), 2
    return local.date(), 1 if local.hour < 19 else 2


def _shift_before(d: date, shift: int) -> tuple[date, int]:
    return (d, 1) if shift == 2 else (d - timedelta(days=1), 2)


def _number(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if NUMBER.fullmatch(value) else None


def _written_date(day: str, month: str, send_date: date) -> date | None:
    """The year is never read: it is the one that puts the date within the 7 days up to the send date."""
    d, m = _number(day), _number(month)
    if d is None or m is None:
        return None
    for year in (send_date.year, send_date.year - 1):
        try:
            candidate = date(year, m, d)
        except ValueError:
            continue
        if send_date - LOOKBACK <= candidate <= send_date:
            return candidate
    return None


def header_key(fields: dict[str, str], send: datetime) -> Key | Wait:
    """R3.3-R3.5: the (date, shift) a front is filed under, or why it waits."""
    blank = lambda name: not (fields.get(name) or "").strip()
    written_date = written_shift = None
    if not blank("day") and not blank("month"):
        written_date = _written_date(fields["day"], fields["month"], send.astimezone(VN).date())
        if written_date is None:
            return Wait(WAIT_BAD_DATE)
    if not blank("shift"):
        written_shift = _number(fields["shift"])
        if written_shift not in (1, 2):
            return Wait(WAIT_BAD_SHIFT)
    if written_date and written_shift:
        start = shift_start(written_date, written_shift)
        if start <= send < start + SHIFT_LENGTH + AGREE_AFTER_END:
            return Key(written_date, written_shift, doubt=False)
        return Wait(WAIT_DISAGREE)
    current = shift_of(send)
    inferred = _shift_before(*current) if send < shift_start(*current) + LATE_PHOTO else current
    if written_date not in (None, inferred[0]) or written_shift not in (None, inferred[1]):
        return Wait(WAIT_DISAGREE)
    return Key(*inferred, doubt=True)


def _weight(value: str | None) -> float | None:
    value = (value or "").strip()
    if not WEIGHT.fullmatch(value):
        return None
    return float(Decimal(value.replace(",", ".")).quantize(Decimal("0.1"), ROUND_HALF_UP))


def fingerprint(fields: dict[str, str]) -> Fingerprint | None:
    """R4: (first_code, net1, net2, net3). None when all four are blank: such a front is always a new paper."""
    code = (fields.get("first_code") or "").strip().casefold()
    parts = (None if code in ("", "-", "?") else code, *(_weight(fields.get(f"net{i}")) for i in (1, 2, 3)))
    return None if all(part is None for part in parts) else parts


# --- Filing ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Decision:
    outcome: str  # new / resend / back / waiting / not_form
    production_date: date | None = None
    shift: int | None = None
    page_no: int | None = None
    sheet_id: int | None = None  # a resend's paper
    reason: str | None = None
    doubt: bool = False


def file_front(conn, inbox_id: int, key: Key, gate: dict | None, photos_dir: Path, now: datetime) -> str:
    """The gate's filing of a checked front (R4): 'new', 'resend', or 'noop' for a row already settled."""
    return _file(conn, inbox_id, gate, photos_dir, now, None, BY_GATE,
                 lambda row: _place_front(conn, row, key, gate))


def file_back(conn, inbox_id: int, gate: dict | None, photos_dir: Path, now: datetime) -> str:
    """The gate's filing of a back (R5): 'back', 'waiting' when no front pairs, or 'noop'."""
    return _file(conn, inbox_id, gate, photos_dir, now, None, BY_GATE, lambda row: _pair_back(conn, row))


def record_wait(conn, inbox_id: int, reason: str, gate: dict | None, now: datetime,
                outcome: str = "waiting") -> str:
    """Settle a delivered row without a sheet: 'waiting' or 'not_form' (staging: 'staging_checked')."""
    decision = _Decision(outcome, reason=reason)
    return _file(conn, inbox_id, gate, None, now, None, BY_GATE, lambda row: decision, need_file=False)


def file_front_by_person(conn, inbox_id: int, production_date: date, shift: int, user_id: int,
                         photos_dir: Path, now: datetime | None = None) -> str:
    """R8: file a front under a person's date and shift. No R3 checks; the stored gate fingerprint still finds a resend."""
    if shift not in (1, 2):
        raise Refused(WAIT_BAD_SHIFT)
    gate = _stored_gate(conn, inbox_id)
    key = Key(production_date, shift, doubt=False)
    return _file(conn, inbox_id, gate, photos_dir, now, user_id, BY_PERSON,
                 lambda row: _place_front(conn, row, key, gate))


def file_back_by_person(conn, inbox_id: int, production_date: date, shift: int, paper_no: int, user_id: int,
                        photos_dir: Path, now: datetime | None = None) -> str:
    """R8: file a back as page 2n of the paper a person picked. Only the page's occupancy is checked."""
    if shift not in (1, 2):
        raise Refused(WAIT_BAD_SHIFT)
    if paper_no < 1:
        raise Refused("số tờ không hợp lệ")

    def place(row):
        page = 2 * paper_no
        if _page_taken(conn, row["staging"], production_date, shift, page):
            raise Refused(PAGE_TAKEN)
        return _Decision("back", production_date, shift, page)
    return _file(conn, inbox_id, _stored_gate(conn, inbox_id), photos_dir, now, user_id, BY_PERSON, place)


def _stored_gate(conn, inbox_id):
    row = conn.execute("SELECT gate_reply FROM zalo_inbox WHERE id = ?", (inbox_id,)).fetchone()
    return json.loads(row["gate_reply"]) if row and row["gate_reply"] else None


def send_time(row) -> datetime:
    t = datetime.fromisoformat(row["sent_at"] or row["received_at"])
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def inbox_file(row, photos_dir: Path | None) -> Path | None:
    """The saved photo of an inbox row, also when the data folder moved since it was saved."""
    if not row["path"]:
        return None
    path = Path(row["path"])
    if path.is_file():
        return path
    if photos_dir is not None and (moved := Path(photos_dir) / "zalo" / path.name).is_file():
        return moved
    return None


def _turn(gate):
    return TURNS.get(str((gate or {}).get("turn", "")).strip().lower(), 0)


def _file(conn, inbox_id, gate, photos_dir, now, user_id, allowed, decide, need_file=True):
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT * FROM zalo_inbox WHERE id = ?", (inbox_id,)).fetchone()
    if row is None or row["status"] not in allowed:
        return "noop"
    path = inbox_file(row, photos_dir) if need_file else None
    if need_file and path is None:
        raise Refused(WAIT_NO_FILE)
    small_copy = None
    if path is not None and not row["staging"]:
        small_copy = _display_copy(path, row["sha256"], _turn(gate))
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM zalo_inbox WHERE id = ?", (inbox_id,)).fetchone()
        if row["status"] not in allowed:
            conn.rollback()
            return "noop"
        decision = decide(row)
        _apply(conn, row, decision, gate, path, small_copy, user_id, now.astimezone(timezone.utc).isoformat())
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return decision.outcome


def _display_copy(path, sha256, turn):
    try:
        shown = path.with_name(f"{sha256 or path.stem}_display.jpg")
        shown.write_bytes(display_copy(path.read_bytes(), turn))
        return str(shown)
    except Exception:  # the office pages fall back to the original, as for an upload
        return None


def _place_front(conn, row, key, gate):
    fronts = conn.execute("""
        SELECT id, page_no, sheet_id, gate_reply FROM zalo_inbox
        WHERE staging = ? AND status = ? AND outcome = 'new' AND production_date = ? AND shift = ? ORDER BY id
    """, (row["staging"], SETTLED[row["staging"]], key.production_date.isoformat(), key.shift)).fetchall()
    wanted = fingerprint(gate) if gate else None
    if wanted is not None:
        for front in fronts:
            same = front["gate_reply"] and fingerprint(json.loads(front["gate_reply"])) == wanted
            if same and (row["staging"] or front["sheet_id"] is not None):
                return _Decision("resend", key.production_date, key.shift, front["page_no"], front["sheet_id"],
                                 doubt=key.doubt)
    return _Decision("new", key.production_date, key.shift, 2 * len(fronts) + 1, doubt=key.doubt)


def _not_a_front(row):
    layout = json.loads(row["gate_reply"]).get("layout") if row["gate_reply"] else None
    return row["status"] == "not_form" or row["outcome"] in ("not_form", "back") or layout in ("back", "other")


def _pair_back(conn, row):
    """R5 and sketch gap 3: the latest same-sender row sent in [back - 30 min, back] that may be a front.
    It pairs only when it is a settled front; an undecided or waiting front makes the back wait."""
    sent = send_time(row)
    wait = _Decision("waiting", reason=WAIT_NO_FRONT)
    if not row["sender_id"]:
        return wait
    day = timedelta(days=1)
    nearby = conn.execute("""
        SELECT * FROM zalo_inbox
        WHERE staging = ? AND sender_id = ? AND id != ?
          AND status IN ('delivered', 'filed', 'waiting', 'not_form', 'staging_checked')
          AND COALESCE(sent_at, received_at) BETWEEN ? AND ?
    """, (row["staging"], row["sender_id"], row["id"], (sent - day).isoformat(), (sent + day).isoformat())).fetchall()
    candidates = [r for r in nearby if sent - BACK_WINDOW <= send_time(r) <= sent and not _not_a_front(r)]
    if not candidates:
        return wait
    front = max(candidates, key=lambda r: (send_time(r), r["id"]))
    if front["status"] != SETTLED[row["staging"]] or front["outcome"] not in FRONT_OUTCOMES:
        return wait
    paper_date, paper_shift = date.fromisoformat(front["production_date"]), front["shift"]
    page = front["page_no"] + 1
    if _page_taken(conn, row["staging"], paper_date, paper_shift, page):
        return wait
    return _Decision("back", paper_date, paper_shift, page)


def _page_taken(conn, staging, production_date, shift, page_no):
    args = (production_date.isoformat(), shift, page_no)
    if conn.execute("""
        SELECT 1 FROM zalo_inbox WHERE staging = ? AND status = ? AND production_date = ? AND shift = ? AND page_no = ?
          AND outcome IN ('new', 'back')
    """, (staging, SETTLED[staging], *args)).fetchone():
        return True
    return not staging and conn.execute(
        "SELECT 1 FROM sheets WHERE form = ? AND machine = '' AND production_date = ? AND shift = ? AND page_no = ?",
        (ZALO_FORM, *args)).fetchone() is not None


def _apply(conn, row, decision, gate, path, small_copy, user_id, at):
    """Write the decision. Staging writes only its inbox row; pilot writes the sheet, photo, job and event too."""
    sheet_id = photo_id = None
    if not row["staging"] and decision.outcome in ("new", "resend", "back"):
        sheet_id = decision.sheet_id
        if decision.outcome != "resend":
            sheet_id = conn.execute("""
                INSERT INTO sheets (form, machine, production_date, shift, page_no, status, version, status_since, layout)
                VALUES (?, '', ?, ?, ?, 'received', 1, ?, ?)
            """, (ZALO_FORM, decision.production_date.isoformat(), decision.shift, decision.page_no, at,
                  "back" if decision.outcome == "back" else "form_a")).lastrowid
        photo_id = conn.execute("""
            INSERT INTO photos (sheet_id, path, small_copy_path, sha256, uploaded_by, uploaded_at, turn, turn_checked)
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
        """, (sheet_id, str(path), small_copy, row["sha256"], at, _turn(gate), int(gate is not None))).lastrowid
        if decision.outcome != "resend":
            conn.execute("INSERT INTO read_jobs (photo_id, status, queued_at) VALUES (?, 'queued', ?)", (photo_id, at))
        conn.execute("INSERT INTO events (at, user_id, action, sheet_id, detail) VALUES (?, ?, ?, ?, ?)",
                     (at, user_id, f"zalo_{'filed' if decision.outcome == 'new' else decision.outcome}", sheet_id,
                      _event_detail(row["id"], decision, user_id)))
        if decision.outcome == "new":
            d = decision.production_date
            flag = "doubt" if decision.doubt else None
            for field, value in (("day", d.day), ("month", d.month), ("year", d.year), ("shift", decision.shift)):
                conn.execute("""
                    INSERT INTO page_values (sheet_id, field, value, source_photo_id, flag) VALUES (?, ?, ?, ?, ?)
                """, (sheet_id, field, str(value), photo_id, flag))
    if row["staging"]:
        status = SETTLED[1]
    elif decision.outcome in ("waiting", "not_form"):
        status = decision.outcome
    else:
        status = SETTLED[0]
    conn.execute("""
        UPDATE zalo_inbox SET status = ?, outcome = ?, reason = ?, production_date = ?, shift = ?, page_no = ?,
            sheet_id = ?, photo_id = ?, gate_reply = COALESCE(?, gate_reply), gated_at = ?
        WHERE id = ?
    """, (status, decision.outcome, decision.reason,
          decision.production_date.isoformat() if decision.production_date else None, decision.shift,
          decision.page_no, sheet_id, photo_id, json.dumps(gate, ensure_ascii=False) if gate else None, at, row["id"]))


def _event_detail(inbox_id, decision, user_id):
    what = {"back": "trang sau", "resend": "gửi lại"}.get(decision.outcome, "mặt trước")
    by = "người xếp" if user_id is not None else "tự ghép" if decision.outcome == "back" else "tự xếp"
    return f"Zalo ảnh {inbox_id}, trang {decision.page_no}: {what}, {by}"
