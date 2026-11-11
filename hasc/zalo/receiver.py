"""Receive-only Zalo bot: keep photos from allowed groups in the HASC Zalo inbox.

The bot never sends a chat message: this module has no send call. It long-polls getUpdates, keeps
image events from the staging and pilot groups, and drops every other event without storing it.
A kept event is written first and downloaded after, so a failed download stays in the inbox for
retry. Nothing here creates a sheet or a read job: an inbox photo has no machine, date, shift or page.

The token sits in every API URL, so no URL, response body or exception text is ever logged or stored.
"""
from datetime import datetime, timezone
import hashlib
import http.client
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

from hasc.db.queries import now_iso
from hasc.web.operator import MAX_FILE_SIZE, is_valid_image, save_photo_file

API = "https://bot-api.zaloplatforms.com/bot{token}/{method}"
POLL_TIMEOUT = 30  # seconds Zalo holds an empty getUpdates
MAX_ATTEMPTS = 3
EXTENSIONS = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "WEBP": ".webp", "HEIC": ".heic"}


class ZaloError(Exception):
    """A safe message: names the method and the HTTP status, never the URL or the token."""


def call(token, method, payload=None):
    """POST one Bot API method and return its `result`. An empty long poll returns None."""
    try:
        request = urllib.request.Request(
            API.format(token=token, method=method), data=json.dumps(payload or {}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
    except ValueError:  # its message holds the URL, so the token
        raise ZaloError(f"{method}: token có ký tự không hợp lệ") from None
    try:
        with urllib.request.urlopen(request, timeout=POLL_TIMEOUT + 10) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        if method == "getUpdates" and error.code in (408, 504):
            return None
        raise ZaloError(f"{method}: HTTP {error.code}") from None
    except (TimeoutError, socket.timeout):
        if method == "getUpdates":
            return None
        raise ZaloError(f"{method}: hết thời gian chờ kết nối") from None
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        raise ZaloError(f"{method}: không kết nối được tới Zalo") from None
    try:
        data = json.loads(raw) if raw else {"ok": True, "result": None}
    except ValueError:
        raise ZaloError(f"{method}: Zalo trả về dữ liệu không đọc được") from None
    if not isinstance(data, dict) or data.get("ok") is not True:
        code = data.get("error_code") if isinstance(data, dict) else None
        raise ZaloError(f"{method}: Zalo từ chối yêu cầu (mã {code})")
    return data.get("result")


def fetch(url):
    """Download a photo, at most MAX_FILE_SIZE + 1 bytes so an oversize file is caught."""
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read(MAX_FILE_SIZE + 1)
    except urllib.error.HTTPError as error:
        raise ZaloError(f"tải ảnh: HTTP {error.code}") from None
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError):
        raise ZaloError("tải ảnh: không kết nối được") from None


def events_of(result):
    """getUpdates may answer one event, a list of them or nothing."""
    if isinstance(result, list):
        return [e for e in result if isinstance(e, dict)]
    return [result] if isinstance(result, dict) else []


def store_event(conn, event, staging_group, pilot_groups):
    """Keep an image event from an allowed group. Returns 'new', 'redelivered' or 'ignored'.

    Only the event name, chat, sender, message id, time and whether the caption held /capture are
    read. The caption and any chat text are not stored.
    """
    message = event.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    allowed = chat.get("chat_type") == "GROUP" and chat_id and chat_id in ({staging_group} | set(pilot_groups))
    message_id = str(message.get("message_id") or "")
    if event.get("event_name") != "message.image.received" or not allowed or not message_id:
        with conn:
            conn.execute("UPDATE zalo_receiver SET ignored_events = ignored_events + 1 WHERE id = 1")
        return "ignored"
    sender = message.get("from") or {}
    sent_at = message.get("date")
    with conn:
        cursor = conn.execute("""
            INSERT INTO zalo_inbox (chat_id, message_id, sender_id, sender_name, staging, command, sent_at,
                                    received_at, photo_url, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'received')
            ON CONFLICT (chat_id, message_id) DO NOTHING
        """, (chat_id, message_id, str(sender.get("id") or "") or None, sender.get("display_name"),
              int(chat_id == staging_group), int("/capture" in str(message.get("caption") or "").lower()),
              _ms_to_iso(sent_at), now_iso(), message.get("photo_url")))
        if cursor.rowcount:
            return "new"
        conn.execute("UPDATE zalo_inbox SET redeliveries = redeliveries + 1 WHERE chat_id = ? AND message_id = ?",
                     (chat_id, message_id))
    return "redelivered"


def _ms_to_iso(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def deliver_pending(conn, photos_dir, fetch=fetch):
    """Download every received photo into the inbox. Returns how many were tried."""
    pending = conn.execute("SELECT * FROM zalo_inbox WHERE status = 'received' ORDER BY id").fetchall()
    for item in pending:
        try:
            deliver(conn, item, photos_dir, fetch)
        except Exception as error:  # e.g. a full disk: count the attempt, keep the photo, go on
            status = "failed" if item["attempts"] + 1 >= MAX_ATTEMPTS else "received"
            _finish(conn, item["id"], status, item["attempts"] + 1, f"lỗi lưu ảnh: {type(error).__name__}")
        beat(conn, ok=False)  # slow downloads must not make a working receiver look dead
    return len(pending)


def deliver(conn, item, photos_dir, fetch=fetch):
    """Save one photo's original bytes, checked like an operator upload. Returns its new status."""
    attempts = item["attempts"] + 1
    try:
        if not item["photo_url"]:
            raise ZaloError("sự kiện không có đường dẫn ảnh")
        data = fetch(item["photo_url"])
    except ZaloError as error:
        status = "failed" if attempts >= MAX_ATTEMPTS else "received"
        _finish(conn, item["id"], status, attempts, str(error))
        return status
    if len(data) > MAX_FILE_SIZE:
        _finish(conn, item["id"], "rejected", attempts, "ảnh lớn hơn 20 MB", photo_url=None)
        return "rejected"
    valid, fmt = is_valid_image(data)
    if not valid:
        _finish(conn, item["id"], "rejected", attempts, "không phải tệp ảnh", photo_url=None)
        return "rejected"
    sha256 = hashlib.sha256(data).hexdigest()
    same = conn.execute("""
        SELECT path FROM zalo_inbox WHERE sha256 = ? AND status = 'delivered' AND staging = ? AND id != ?
    """, (sha256, item["staging"], item["id"])).fetchone()  # a staging test photo never hides a pilot one
    if same:
        path = same["path"]
        status = "duplicate"
    else:
        path = str(save_photo_file(Path(photos_dir) / "zalo", data, f"x{EXTENSIONS.get(fmt, '.jpg')}", sha256))
        status = "delivered"
    _finish(conn, item["id"], status, attempts, None, photo_url=None, path=path, sha256=sha256,
            size_bytes=len(data), delivered_at=now_iso())
    return status


def _finish(conn, item_id, status, attempts, error, **fields):
    fields.update(status=status, attempts=attempts, last_error=error)
    names = ", ".join(f"{name} = ?" for name in fields)
    with conn:
        conn.execute(f"UPDATE zalo_inbox SET {names} WHERE id = ?", (*fields.values(), item_id))


def beat(conn, error=None, started=False, ok=True):
    """Record that the receiver is alive; with `error`, the latest problem it hit.
    ok=False only says it is alive, for the wait after an error."""
    now = now_iso()
    with conn:
        conn.execute("INSERT INTO zalo_receiver (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
        if started:
            conn.execute("UPDATE zalo_receiver SET started_at = ? WHERE id = 1", (now,))
        if error:
            conn.execute("UPDATE zalo_receiver SET heartbeat_at = ?, last_error = ?, last_error_at = ? WHERE id = 1",
                         (now, error, now))
        elif ok:
            conn.execute("UPDATE zalo_receiver SET heartbeat_at = ?, last_ok_at = ? WHERE id = 1", (now, now))
        else:
            conn.execute("UPDATE zalo_receiver SET heartbeat_at = ? WHERE id = 1", (now,))


def poll_once(conn, token, staging_group, pilot_groups, photos_dir, call=call, fetch=fetch):
    """One long poll: keep the events, then download what is waiting. Raises ZaloError on API failure."""
    dropped = False
    for event in events_of(call(token, "getUpdates", {"timeout": str(POLL_TIMEOUT)})):
        try:
            store_event(conn, event, staging_group, pilot_groups)
        except Exception as error:  # one odd event must not lose the rest of the batch
            dropped = True
            try:
                beat(conn, error=f"không lưu được sự kiện: {type(error).__name__}")
            except Exception:
                pass  # the database itself is failing; the next events still get their try
    beat(conn, ok=not dropped)  # a dropped event keeps the page on "Đang lỗi" until a clean poll
    deliver_pending(conn, photos_dir, fetch)


def check_connection(token, call=call):
    """The token works and no webhook is set (getUpdates does not work while one is)."""
    call(token, "getMe")
    webhook = call(token, "getWebhookInfo")
    if isinstance(webhook, dict) and webhook.get("url"):
        raise ZaloError("bot đang dùng webhook; getUpdates không nhận được tin. Không tự xoá webhook.")
