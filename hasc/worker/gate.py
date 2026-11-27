"""Zalo gate (zalo-intake spec R1-R3): one cheap model call per delivered inbox photo, then the filing rules.

The gate reads only the photo's turn, layout, header and a fingerprint of its first rows. The model reports;
hasc.intake decides. Every dispatch is committed to gate_calls before the transport call leaves, so the daily cap
counts a call even if the process dies inside it, and survives a restart.
"""
from datetime import datetime, time, timedelta, timezone
import io
from pathlib import Path
import re
import unicodedata

from PIL import Image

from hasc import intake
from hasc.images import upright

GATE = """This photo shows a paper form on a desk. Do NOT read the table rows.
Reply with exactly one line of key=value pairs separated by spaces:
turn= none, 180, left (turn 90 degrees counter-clockwise) or right (turn 90 degrees clockwise): how the photo must be turned so the printed text reads normally.
layout= form_a for "Báo cáo chi tiết máy thổi" (columns Cả bì, Lõi, Bìa, Sau trừ bì), back for the "Màng ống" side (columns Tổng KL, Lõi, KL đã trừ), other for anything else.
code= the printed form code, as printed, with no spaces.
day= month= year= shift= from the handwritten Ngày, Tháng, Năm and Ca in the header, as written.
page= only if a page number is written.
first_code= the Mã cuộn of the first filled table row. net1= net2= net3= the net weight (Sau trừ bì, or KL đã trừ on the Màng ống side) of the first three filled table rows, as written.
Leave out any key that is blank or unreadable. Never guess."""

KEPT = ("turn", "layout", "code", "day", "month", "year", "shift", "page", "first_code", "net1", "net2", "net3")
ALIASES = {"ca": "shift", "tháng": "month", "năm": "year"}
LAYOUTS = ("form_a", "back", "other")
PAIR = re.compile(r"(?<!\S)([^\s=]+)=(\S*)")
SIZE = 1600
MAX_TOKENS = 3000
DAILY_CAP = 100
MALFORMED_LIMIT = 3
INTERRUPTED = "interrupted"  # error text of a gate_calls row whose reply never came back; its cost is unknown
COUNTED = ("new", "resend", "back", "waiting", "not_form", "staging_checked", "malformed", "over_cap", "error")


def parse_gate(text: str | None) -> dict | None:
    """The kept key=value pairs of the first line holding layout=, or None for a malformed reply (R2)."""
    line = next((l for l in unicodedata.normalize("NFC", text or "").splitlines() if "layout=" in l.lower()), None)
    if line is None:
        return None
    fields = {}
    for key, value in PAIR.findall(line):
        key = key.lower()
        key = ALIASES.get(key, key)
        if key in KEPT:
            fields.setdefault(key, value)
    return fields if fields.get("layout") in LAYOUTS else None


def run_gate_batch(conn, reader, photos_dir: Path, now: datetime, clock=None) -> dict:
    """Gate the delivered inbox rows in id order, at most DAILY_CAP dispatches per Vietnam day. Counts by outcome.

    `now` is the batch start. Each dispatch is stamped with the time `clock()` gives right before it is made, after its image is
    prepared (R1: counted by dispatch time), and the day's count is taken again whenever the Vietnam date changes inside a batch. Without a
    clock every dispatch is stamped `now`; the worker passes the real one."""
    counts = dict.fromkeys(COUNTED, 0)
    clock = clock or (lambda: now)
    day, used = None, 0
    ids = [r["id"] for r in conn.execute("SELECT id FROM zalo_inbox WHERE status = 'delivered' ORDER BY id")]
    for position, inbox_id in enumerate(ids):
        row = conn.execute("SELECT * FROM zalo_inbox WHERE id = ?", (inbox_id,)).fetchone()
        if row["status"] != "delivered":
            continue
        try:
            path = intake.inbox_file(row, photos_dir)
            if path is None:
                outcome = intake.record_wait(conn, inbox_id, intake.WAIT_NO_FILE, None, now)
            else:
                if (jpeg := _gate_image(path)) is None:
                    outcome = intake.record_wait(conn, inbox_id, intake.WAIT_BAD_IMAGE, None, now)
                else:
                    dispatched = clock()  # read after the image is prepared: the dispatch leaves at this time
                    if (today := dispatched.astimezone(intake.VN).date()) != day:
                        day, used = today, _dispatches_on(conn, dispatched)
                    if used >= DAILY_CAP:
                        counts["over_cap"] = len(ids) - position
                        break
                    used += 1
                    stamp = dispatched.astimezone(timezone.utc).isoformat()
                    gate = _dispatch(conn, reader, inbox_id, jpeg, stamp)
                    outcome = _settle(conn, row, gate, photos_dir, now)
        except Exception as error:  # one row's failure must not stop the batch; the row stays delivered
            print(f"Gate: Zalo photo {inbox_id} not filed: {type(error).__name__}: {error}", flush=True)
            outcome = "error"
        if outcome != "noop":
            counts["staging_checked" if row["staging"] and outcome not in ("malformed", "error") else outcome] += 1
    return counts


def _dispatches_on(conn, now):
    day = now.astimezone(intake.VN).date()
    start = datetime.combine(day, time(0), intake.VN)
    bounds = (start.astimezone(timezone.utc).isoformat(), (start + timedelta(days=1)).astimezone(timezone.utc).isoformat())
    return conn.execute("SELECT COUNT(*) FROM gate_calls WHERE at >= ? AND at < ?", bounds).fetchone()[0]


def _gate_image(path):
    try:
        image = Image.open(io.BytesIO(upright(path.read_bytes()))).convert("RGB")
    except Exception:
        return None
    image.thumbnail((SIZE, SIZE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def _dispatch(conn, reader, inbox_id, jpeg, stamp):
    """One transport call, recorded before it leaves and completed when the reply comes back.

    The row is committed as an interrupted, costless call first; if the process dies inside the transport it stays
    that way and still counts toward the cap. Returns the parsed reply or None (malformed)."""
    body = reader._body(reader.model, jpeg, "low", prompt=GATE)
    body["max_tokens"] = MAX_TOKENS
    with conn:
        call_id = conn.execute(
            "INSERT INTO gate_calls (inbox_id, at, ok, error) VALUES (?, ?, 0, ?)", (inbox_id, stamp, INTERRUPTED)
        ).lastrowid
    reply, error = None, None
    try:
        reply = reader.transport(body)
        text = reply["choices"][0]["message"]["content"] or ""
    except Exception as failure:  # transport errors and odd reply shapes alike are a failed dispatch
        text, error = None, type(failure).__name__
    usage = (reply.get("usage") if isinstance(reply, dict) else None) or {}
    number = lambda name: v if isinstance(v := usage.get(name), int | float) and not isinstance(v, bool) else None
    with conn:
        conn.execute("""
            UPDATE gate_calls SET ok = ?, cost_usd = ?, prompt_tokens = ?, completion_tokens = ?, error = ?
            WHERE id = ?
        """, (int(error is None), number("cost"), number("prompt_tokens"), number("completion_tokens"), error,
              call_id))
    return parse_gate(text) if error is None else None


def _settle(conn, row, gate, photos_dir, now):
    if gate is None:
        return _malformed(conn, row, now)
    if gate["layout"] == "other":
        return intake.record_wait(conn, row["id"], intake.NOT_FORM, gate, now, outcome="not_form")
    if gate["layout"] == "back":
        return intake.file_back(conn, row["id"], gate, photos_dir, now)
    key = intake.header_key(gate, intake.send_time(row))
    if isinstance(key, intake.Wait):
        return intake.record_wait(conn, row["id"], key.reason, gate, now)
    return intake.file_front(conn, row["id"], key, gate, photos_dir, now)


def _malformed(conn, row, now):
    with conn:
        attempts = conn.execute("""
            UPDATE zalo_inbox SET gate_attempts = gate_attempts + 1 WHERE id = ? AND status = 'delivered'
            RETURNING gate_attempts
        """, (row["id"],)).fetchone()
    if attempts and attempts[0] >= MALFORMED_LIMIT:
        return intake.record_wait(conn, row["id"], intake.WAIT_NO_HEADER, None, now)
    return "malformed"
