"""Reading worker jobs: claim one read job, read its photo, write rows, cells and flags.

The website queues a job in read_jobs. The worker claims one job at a time, turns the photo upright,
reads it, copies down product details, and flags rule failures and doubtful cells. Flags never change
a number. A failed attempt is queued again until MAX_ATTEMPTS, then the sheet is marked failed.
"""
from pathlib import Path

from hasc.db import (
    create_event,
    create_row,
    get_default_photos_dir,
    get_page_values,
    get_photo_by_id,
    get_rows_for_sheet,
    increment_sheet_version,
    update_read_job,
    update_sheet_status,
    upsert_cell,
    upsert_page_value,
)
from hasc.db.queries import now_iso
from hasc.reader.photo import upright
from hasc.reader.reader import ReaderError
from hasc.rules import rules

MAX_ATTEMPTS = 3
CARRIED = ("customer", "film_type", "pellet_formula", "mixing_shift_date", "thickness_mic", "width_mm",
           "length_mm")  # weights are never carried (P115)
RULE_FIELDS = {
    "weight": ("gross_kg", "core_kg", "cover_kg", "net_kg"),
    "thickness": ("thickness_1", "thickness_2", "thickness_3", "thickness_4", "thickness_5", "thickness_6",
                  "thickness_avg"),
}


def claim_job(conn):
    """Mark the oldest queued job running and return it, or None. One statement, so two workers cannot take one job."""
    with conn:
        rows = conn.execute(
            """
            UPDATE read_jobs SET status = 'running', attempts = attempts + 1, started_at = ?
            WHERE id = (SELECT id FROM read_jobs WHERE status = 'queued' ORDER BY queued_at ASC, id ASC LIMIT 1)
            RETURNING *
            """,
            (now_iso(),),
        ).fetchall()
    return rows[0] if rows else None


def recover_running_jobs(conn):
    """A job still running at startup was cut off by a crash. Requeue it, or fail it if its last attempt is used up."""
    stuck = conn.execute("SELECT * FROM read_jobs WHERE status = 'running'").fetchall()
    for job in stuck:
        _record_failure(conn, job, "worker stopped during the attempt")
    return len(stuck)


def resolve_photo_path(photo_path_str: str | Path, photos_dir: Path | None = None) -> Path:
    """Resolve photo file path, looking in photos_dir if relative or missing."""
    p = Path(photo_path_str)
    if photos_dir is not None:
        photos_dir = Path(photos_dir)
        if not p.is_absolute():
            candidate = photos_dir / p
            if candidate.is_file():
                return candidate
            candidate_name = photos_dir / p.name
            if candidate_name.is_file():
                return candidate_name
        elif not p.is_file():
            candidate_name = photos_dir / p.name
            if candidate_name.is_file():
                return candidate_name
    return p


def run_pending(conn, reader, scratch_dir, photos_dir=None):
    """Work the queue until no job is queued. Returns how many jobs were claimed."""
    count = 0
    while (job := claim_job(conn)) is not None:
        process_job(conn, job, reader, scratch_dir, photos_dir=photos_dir)
        count += 1
    return count


def process_job(conn, job, reader, scratch_dir, photos_dir=None):
    """Run one claimed job. Returns 'done', 'queued' (to retry) or 'failed'."""
    photo = get_photo_by_id(conn, job["photo_id"])
    update_sheet_status(conn, photo["sheet_id"], "reading")
    if photos_dir is None:
        photos_dir = get_default_photos_dir()
    file_path = resolve_photo_path(photo["path"], photos_dir)
    try:
        upright_path = Path(scratch_dir) / f"job{job['id']}.jpg"
        upright_path.write_bytes(upright(file_path.read_bytes()))
        result = reader.read(str(upright_path))
    except (ReaderError, OSError) as error:
        _add_usage(conn, job["id"], getattr(error, "usage", None))
        not_a_sheet = getattr(error, "kind", None) == "not_form_a"  # retrying the same photo cannot help
        return _record_failure(conn, job, str(error), not_retryable=not_a_sheet)

    _add_usage(conn, job["id"], result.get("usage"))
    carry_down(result["rows"])
    _write_result(conn, photo, result)
    with conn:
        conn.execute("UPDATE read_jobs SET reader_name = ? WHERE id = ?", (result["reader_name"], job["id"]))
    update_read_job(conn, job["id"], status="done", finished_at=now_iso())
    update_sheet_status(conn, photo["sheet_id"], "needs_review")
    return "done"


def _add_usage(conn, job_id, usage):
    """Add what this attempt cost to the job, for the AI page (P149). Retries add up."""
    if not usage:
        return
    with conn:
        conn.execute("""
            UPDATE read_jobs SET cost_usd = COALESCE(cost_usd, 0) + ?,
                prompt_tokens = COALESCE(prompt_tokens, 0) + ?, completion_tokens = COALESCE(completion_tokens, 0) + ?
            WHERE id = ?
        """, (float(usage.get("cost") or 0), int(usage.get("prompt_tokens") or 0),
              int(usage.get("completion_tokens") or 0), job_id))


def carry_down(rows):
    """A carried product detail that is missing from a row takes the value above it. A cell the reader wrote is kept."""
    for above, row in zip(rows, rows[1:]):
        for field in CARRIED:
            cell = above["cells"].get(field)
            if field not in row["cells"] and cell and cell["value"] is not None:
                row["cells"][field] = {**cell, "inherited": True}


def fill_roll_codes(rows, start=0):
    """Fill a blank roll_code with the row's 1-based position on the page (brief P118, P119; criterion 55).

    A roll code written on the paper always wins and is never renumbered.
    The filled roll code is marked as an inherited value so page Excel greys it,
    the operator problem list names the roll, and the rows table row_key matches.
    """
    for index, row in enumerate(rows, start=start + 1):
        cell = row["cells"].get("roll_code")
        if not cell or (not cell.get("unreadable") and (cell.get("value") is None or str(cell.get("value")).strip() == "")):
            row["cells"]["roll_code"] = {"value": str(index), "inherited": True, "doubt": False}



def _record_failure(conn, job, error, not_retryable=False):
    photo = get_photo_by_id(conn, job["photo_id"])
    sheet_id = photo["sheet_id"]
    create_event(conn, None, "read_failed", sheet_id=sheet_id,
                 detail=f"job {job['id']} attempt {job['attempts']}: {error}")
    if not_retryable or job["attempts"] >= MAX_ATTEMPTS:
        update_read_job(conn, job["id"], status="failed", last_error=error, finished_at=now_iso())
        update_sheet_status(conn, sheet_id, "failed")
        return "failed"
    update_read_job(conn, job["id"], status="queued", last_error=error)
    return "queued"


def _write_result(conn, photo, result):
    sheet_id, photo_id, layout = photo["sheet_id"], photo["id"], result["layout"]
    known_page = {row["field"] for row in get_page_values(conn, sheet_id)}
    for field, cell in result["page"].items():
        if cell["value"] is not None and field not in known_page:
            upsert_page_value(conn, sheet_id, field, cell["value"], source_photo_id=photo_id,
                              flag="doubt" if cell["doubt"] else None)
    start = len(get_rows_for_sheet(conn, sheet_id, include_invalid=True))
    fill_roll_codes(result["rows"], start)
    flagged = 0
    for offset, row in enumerate(result["rows"]):
        roll = row["cells"].get("roll_code", {}).get("value")
        position = start + offset
        row_id = create_row(conn, sheet_id, str(roll) if roll is not None else str(offset + 1), position)
        flags = _cell_flags(row, layout)
        flagged += any(flag != "none" for flag, _ in flags.values())
        for field, cell in row["cells"].items():
            flag, note = flags[field]
            upsert_cell(conn, row_id, field, _text(cell["value"]), inherited=int(bool(cell.get("inherited"))),
                        source="ai", source_photo_id=photo_id, flag=flag, note=note)
    with conn:
        conn.execute("UPDATE sheets SET layout = ? WHERE id = ?", (layout, sheet_id))
    if result["rows"]:
        increment_sheet_version(conn, sheet_id)
    create_event(conn, None, "read_done", sheet_id=sheet_id,
                 detail=f"{result['reader_name']} rows={len(result['rows'])} flagged={flagged}")


def _cell_flags(row, layout):
    """Each cell gets one flag: unreadable, then rule, then doubt. A rule note names the failing rule."""
    rule_note = {field: name for name in rules.failures(row, layout) for field in RULE_FIELDS[name]}
    flags = {}
    for field, cell in row["cells"].items():
        if cell.get("unreadable"):
            flags[field] = ("unreadable", None)
        elif field in rule_note:
            flags[field] = ("rule", rule_note[field])
        elif cell.get("doubt"):
            flags[field] = ("doubt", None)
        else:
            flags[field] = ("none", None)
    return flags


def _text(value):
    return None if value is None else f"{value:g}" if isinstance(value, float) else str(value)
