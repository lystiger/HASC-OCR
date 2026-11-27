"""Zalo bot receiver: keep photos from allowed senders and groups in the HASC Zalo inbox.

This module never sends a chat message; the only send is the daily summary (hasc.zalo.summary, P151).
It long-polls getUpdates, keeps image events from allowed sources, and drops every other event
without storing it.

Workers send sheet photos to the bot in a private chat. Group photos reach a bot only when they
mention it or reply to it (tested 2026-11-11: plain group photos never arrived), so group sources
stay supported but are not the intake path. A source is ("PRIVATE", sender id) or ("GROUP", chat id),
mapped to 1 for staging or 0 for pilot.
A kept event is written first and downloaded after, so a failed download stays in the inbox for
retry. Nothing here creates a sheet or a read job: the worker's Zalo gate (hasc.worker.gate) files photos.

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
from hasc.intake import MAX_FILE_SIZE, is_valid_image, save_photo_file

API = "https://bot-api.zaloplatforms.com/bot{token}/{method}"
POLL_TIMEOUT = 30  # seconds Zalo holds an empty getUpdates
MAX_ATTEMPTS = 3
EXTENSIONS = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "WEBP": ".webp", "HEIC": ".heic"}


class ZaloError(Exception):
    """A safe message: names the method and the HTTP status, never the URL or the token."""


class ZaloUncertain(ZaloError):
    """The request was sent but no usable answer came back (timeout, cut-off or unreadable reply).
    Zalo may have acted on it, so a send must not be repeated."""


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
    except urllib.error.HTTPError as error:  # Zalo answered with an error status
        if method == "getUpdates" and error.code in (408, 504):
            return None
        raise ZaloError(f"{method}: HTTP {error.code}") from None
    except urllib.error.URLError as error:  # urllib wraps failures while connecting or sending the request
        if isinstance(error.reason, (socket.gaierror, ConnectionRefusedError)):  # nothing reached Zalo
            raise ZaloError(f"{method}: không kết nối được tới Zalo") from None
        raise ZaloUncertain(f"{method}: mất kết nối khi gửi tới Zalo") from None
    except (TimeoutError, socket.timeout):  # raised after the request went out
        if method == "getUpdates":
            return None
        raise ZaloUncertain(f"{method}: hết thời gian chờ kết nối") from None
    except (OSError, http.client.HTTPException):  # e.g. cut off while reading the reply
        raise ZaloUncertain(f"{method}: mất kết nối khi chờ Zalo trả lời") from None
    try:
        data = json.loads(raw) if raw else {"ok": True, "result": None}
    except ValueError:
        raise ZaloUncertain(f"{method}: Zalo trả về dữ liệu không đọc được") from None
    if not isinstance(data, dict) or data.get("ok") is not True:
        code = data.get("error_code") if isinstance(data, dict) else None
        # Seen live 2026-11-11: getWebhookInfo answers 404 on a bot with no webhook. The docs only
        # define 404 as "not found" and 408 as a timeout; neither is a failure for these two methods.
        if (method, code) in (("getWebhookInfo", 404), ("getUpdates", 408)):
            return None
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


def mask(value):
    """A Zalo ID shortened for logs: enough to tell sources apart, not to copy one."""
    return f"…{value[-5:]}" if value else "?"


def store_event(conn, event, sources, show_ids=False):
    """Keep an image event from an allowed source. Returns 'new', 'redelivered' or 'ignored'.

    Only the event name, chat, sender, message id, time and whether the caption held /capture are
    read. The caption and any chat text are not stored.
    """
    message = event.get("message") or {}
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = str(chat.get("id") or "")
    chat_type = chat.get("chat_type")
    source_id = chat_id if chat_type == "GROUP" else str(sender.get("id") or "")
    staging = sources.get((chat_type, source_id)) if source_id else None
    message_id = str(message.get("message_id") or "")
    if event.get("event_name") != "message.image.received" or staging is None or not message_id:
        # Metadata only, so a staging test shows why an event was dropped: no text, name or URL.
        # --show-ids prints the full ID once, to copy into the settings file.
        shown = source_id if show_ids else mask(source_id)
        where = f"group {shown}" if chat_type == "GROUP" else f"{chat_type} chat from sender {shown}"
        print(f"Ignored: {event.get('event_name')} in {where}, "
              f"{'not allowed' if staging is None else 'allowed'}", flush=True)
        with conn:
            conn.execute("UPDATE zalo_receiver SET ignored_events = ignored_events + 1 WHERE id = 1")
        return "ignored"
    sent_at = message.get("date")
    with conn:
        cursor = conn.execute("""
            INSERT INTO zalo_inbox (chat_id, chat_type, message_id, sender_id, sender_name, staging, command,
                                    sent_at, received_at, photo_url, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'received')
            ON CONFLICT (chat_id, message_id) DO NOTHING
        """, (chat_id, chat_type, message_id, str(sender.get("id") or "") or None, sender.get("display_name"),
              staging, int("/capture" in str(message.get("caption") or "").lower()),
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
    # Every status a saved photo can reach: a byte-identical resend after gating is still a duplicate, never re-gated.
    same = conn.execute("""
        SELECT path FROM zalo_inbox WHERE sha256 = ? AND staging = ? AND id != ?
          AND status IN ('delivered', 'filed', 'waiting', 'not_form', 'staging_checked')
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


def poll_once(conn, token, sources, photos_dir, call=call, fetch=fetch, show_ids=False):
    """One long poll: keep the events, then download what is waiting. Raises ZaloError on API failure."""
    dropped = False
    for event in events_of(call(token, "getUpdates", {"timeout": str(POLL_TIMEOUT)})):
        try:
            store_event(conn, event, sources, show_ids)
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
