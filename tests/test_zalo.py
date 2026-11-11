"""Zalo inbox (LysStack roadmap, Zalo staging integration): receive-only bot, inbox, AI-page section.

Made-up events, group IDs and photos only; no request reaches Zalo.
"""
from datetime import datetime, timedelta, timezone
import gc
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error

from fastapi.testclient import TestClient
from PIL import Image

from hasc.db import create_user, get_connection
from hasc.web.app import create_app
from hasc.web.security import hash_password
from hasc.web.zalo_inbox import receiver_health, zalo_overview
from hasc.zalo import receiver
from hasc.zalo.__main__ import main
from hasc.zalo.receiver import ZaloError, beat, deliver_pending, poll_once, store_event

STAGING, PILOT, OTHER = "g-staging-000001", "g-pilot-000002", "g-other-000003"
TESTER, WORKER, STRANGER = "u-tester-0000a", "u-worker-0000b", "u-stranger-000c"
SOURCES = {("GROUP", STAGING): 1, ("GROUP", PILOT): 0, ("PRIVATE", TESTER): 1, ("PRIVATE", WORKER): 0}
TOKEN = "123456:secret-token"
SIGNED = "https://zalo.example/photo.jpg?signature=abc"


def jpeg(color=(200, 200, 200)):
    out = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(out, "JPEG")
    return out.getvalue()


def image_event(message_id, chat_id=STAGING, caption=None, chat_type="GROUP", event="message.image.received",
                sender="u-1"):
    message = {"message_id": message_id, "date": 1762850000000, "message_type": "CHAT_PHOTO",
               "from": {"id": sender, "display_name": "Tổ trưởng", "is_bot": False},
               "chat": {"id": chat_id, "chat_type": chat_type}, "photo_url": SIGNED}
    if caption is not None:
        message["caption"] = caption
    return {"event_name": event, "message": message}


class ZaloTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.photos = Path(self.temp_dir.name) / "photos"
        self.conn = get_connection(self.db_path)
        beat(self.conn, started=True)

    def tearDown(self):
        self.conn.close()
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def inbox(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM zalo_inbox ORDER BY id")]

    def store(self, event):
        return store_event(self.conn, event, SOURCES)


class TestReceiver(ZaloTestCase):
    def test_keeps_only_image_events_from_allowed_groups(self):
        self.assertEqual(self.store(image_event("m1")), "new")
        self.assertEqual(self.store(image_event("m2", chat_id=PILOT)), "new")
        self.assertEqual(self.store(image_event("m3", chat_id=OTHER)), "ignored")
        self.assertEqual(self.store(image_event("m4", chat_type="PRIVATE")), "ignored")
        text = {"event_name": "message.text.received",
                "message": {"message_id": "m5", "chat": {"id": STAGING, "chat_type": "GROUP"}, "text": "chuyện riêng"}}
        self.assertEqual(self.store(text), "ignored")
        rows = self.inbox()
        self.assertEqual([(r["message_id"], r["staging"], r["status"]) for r in rows],
                         [("m1", 1, "received"), ("m2", 0, "received")])
        self.assertEqual(rows[0]["sender_id"], "u-1")
        ignored = self.conn.execute("SELECT ignored_events FROM zalo_receiver").fetchone()[0]
        self.assertEqual(ignored, 3)

    def test_private_photos_only_from_allowed_senders(self):
        private = dict(chat_type="PRIVATE")
        self.assertEqual(self.store(image_event("p1", chat_id=TESTER, sender=TESTER, **private)), "new")
        self.assertEqual(self.store(image_event("p2", chat_id=WORKER, sender=WORKER, **private)), "new")
        self.assertEqual(self.store(image_event("p3", chat_id=STRANGER, sender=STRANGER, **private)), "ignored")
        # A worker allowed in private chat does not make an unknown group allowed, and the reverse.
        self.assertEqual(self.store(image_event("p4", chat_id=OTHER, sender=WORKER)), "ignored")
        self.assertEqual(self.store(image_event("p5", chat_id=STAGING, sender=STAGING, **private)), "ignored")
        rows = self.inbox()
        self.assertEqual([(r["message_id"], r["chat_type"], r["staging"], r["sender_id"]) for r in rows],
                         [("p1", "PRIVATE", 1, TESTER), ("p2", "PRIVATE", 0, WORKER)])

    def test_ignored_log_masks_ids_unless_asked(self):
        event = image_event("p1", chat_id=STRANGER, sender=STRANGER, chat_type="PRIVATE")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            store_event(self.conn, event, SOURCES)
            store_event(self.conn, image_event("g1", chat_id=OTHER), SOURCES)
        self.assertNotIn(STRANGER, out.getvalue())
        self.assertNotIn(OTHER, out.getvalue())
        self.assertIn("PRIVATE chat from sender …-000c, not allowed", out.getvalue())
        self.assertIn("group …00003, not allowed", out.getvalue())
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            store_event(self.conn, event, SOURCES, show_ids=True)
        self.assertIn(STRANGER, out.getvalue())
        self.assertNotIn("Tổ trưởng", out.getvalue())

    def test_settings_need_a_source_and_map_lists(self):
        from hasc.zalo.__main__ import settings_from_env
        env = Path(self.temp_dir.name) / "zalo.env"
        env.write_text(f"ZALO_BOT_TOKEN={TOKEN}\nZALO_STAGING_SENDER_IDS={TESTER}\n"
                       f"ZALO_PILOT_SENDER_IDS={WORKER}, u-x\nZALO_PILOT_GROUP_IDS={PILOT}\n", encoding="utf-8")
        token, sources = settings_from_env(env)
        self.assertEqual(token, TOKEN)
        self.assertEqual(sources, {("PRIVATE", TESTER): 1, ("PRIVATE", WORKER): 0, ("PRIVATE", "u-x"): 0,
                                   ("GROUP", PILOT): 0})
        env.write_text(f"ZALO_BOT_TOKEN={TOKEN}\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as stop:
            settings_from_env(env)
        self.assertNotIn(TOKEN, str(stop.exception.code))

    def test_chat_text_and_captions_are_never_stored(self):
        self.store(image_event("m1", caption="@bot /capture ghi chú riêng tư"))
        self.store({"event_name": "message.text.received",
                    "message": {"message_id": "m2", "chat": {"id": STAGING, "chat_type": "GROUP"}, "text": "bí mật"}})
        dump = "\n".join(self.conn.iterdump())
        self.assertNotIn("riêng tư", dump)
        self.assertNotIn("bí mật", dump)
        self.assertEqual(self.inbox()[0]["command"], 1)

    def test_ordinary_photo_is_not_a_command(self):
        self.store(image_event("m1"))
        self.store(image_event("m2", caption="ca 1"))
        self.assertEqual([r["command"] for r in self.inbox()], [0, 0])

    def test_redelivered_event_is_kept_once_and_counted(self):
        self.store(image_event("m1"))
        self.assertEqual(self.store(image_event("m1")), "redelivered")
        rows = self.inbox()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["redeliveries"], 1)

    def test_delivery_saves_the_original_and_creates_no_sheet_or_read(self):
        self.store(image_event("m1"))
        data = jpeg()
        deliver_pending(self.conn, self.photos, fetch=lambda url: data)
        row = self.inbox()[0]
        self.assertEqual(row["status"], "delivered")
        self.assertIsNone(row["photo_url"])
        self.assertEqual(Path(row["path"]).read_bytes(), data)
        self.assertEqual(Path(row["path"]).parent, (self.photos / "zalo").resolve())
        for table in ("sheets", "photos", "read_jobs"):
            self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)

    def test_same_photo_in_a_second_message_is_a_duplicate(self):
        data = jpeg()
        for mid in ("m1", "m2"):
            self.store(image_event(mid))
        deliver_pending(self.conn, self.photos, fetch=lambda url: data)
        rows = self.inbox()
        self.assertEqual([r["status"] for r in rows], ["delivered", "duplicate"])
        self.assertEqual(rows[0]["path"], rows[1]["path"])
        self.assertEqual(len(list((self.photos / "zalo").iterdir())), 1)

    def test_not_an_image_or_too_big_is_rejected(self):
        self.store(image_event("m1"))
        self.store(image_event("m2"))
        answers = iter([b"not an image", b"x" * (receiver.MAX_FILE_SIZE + 1)])
        deliver_pending(self.conn, self.photos, fetch=lambda url: next(answers))
        rows = self.inbox()
        self.assertEqual([r["status"] for r in rows], ["rejected", "rejected"])
        self.assertEqual([r["last_error"] for r in rows], ["không phải tệp ảnh", "ảnh lớn hơn 20 MB"])

    def test_failed_download_is_retried_then_kept_as_failed(self):
        self.store(image_event("m1"))

        def broken(url):
            raise ZaloError("tải ảnh: HTTP 403")

        for expected in ("received", "received", "failed"):
            deliver_pending(self.conn, self.photos, fetch=broken)
            self.assertEqual(self.inbox()[0]["status"], expected)
        row = self.inbox()[0]
        self.assertEqual((row["attempts"], row["last_error"]), (3, "tải ảnh: HTTP 403"))
        self.assertEqual(row["photo_url"], SIGNED)  # kept for an admin retry
        deliver_pending(self.conn, self.photos, fetch=broken)
        self.assertEqual(self.inbox()[0]["attempts"], 3)  # a failed photo waits for an admin

    def test_a_failing_event_or_save_does_not_stop_the_rest(self):
        events = [image_event("m1"), {"event_name": "message.image.received", "message": "not a dict"},
                  image_event("m2")]
        poll_once(self.conn, TOKEN, SOURCES, self.photos,
                  call=lambda token, method, payload=None: events, fetch=lambda url: jpeg())
        self.assertEqual([r["status"] for r in self.inbox()], ["delivered", "duplicate"])
        self.assertEqual(receiver_health(self.conn, datetime.now(timezone.utc))["state"], "error")
        self.store(image_event("m3"))
        with mock.patch("hasc.zalo.receiver.save_photo_file", side_effect=OSError("disk full")):
            deliver_pending(self.conn, self.photos, fetch=lambda url: jpeg((9, 9, 9)))
        row = self.inbox()[-1]
        self.assertEqual((row["status"], row["attempts"], row["last_error"]), ("received", 1, "lỗi lưu ảnh: OSError"))

    def test_staging_photo_never_hides_the_same_pilot_photo(self):
        data = jpeg()
        self.store(image_event("m1"))
        self.store(image_event("m2", chat_id=PILOT))
        deliver_pending(self.conn, self.photos, fetch=lambda url: data)
        self.assertEqual([r["status"] for r in self.inbox()], ["delivered", "delivered"])

    def test_receiver_keeps_beating_while_it_waits_after_an_error(self):
        calls = {"n": 0}

        def flaky(token, method, payload=None):
            if method == "getUpdates":
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("boom")
                raise KeyboardInterrupt
            return {}

        beats = []
        with mock.patch.dict("os.environ", {"HASC_DB_PATH": str(self.db_path)}):
            code = main([], settings=(TOKEN, SOURCES), call=flaky, sleep=beats.append)
        self.assertEqual((code, beats), (0, [5]))
        state = self.conn.execute("SELECT * FROM zalo_receiver").fetchone()
        self.assertEqual(state["last_error"], "lỗi nội bộ: RuntimeError")
        self.assertGreater(state["heartbeat_at"], state["last_error_at"])

    def test_more_than_ten_photos_in_one_poll_are_all_kept(self):
        events = [image_event(f"m{i}") for i in range(12)]
        colors = iter(range(12))
        poll_once(self.conn, TOKEN, SOURCES, self.photos,
                  call=lambda token, method, payload=None: events,
                  fetch=lambda url: jpeg((next(colors) * 20, 0, 0)))
        self.assertEqual([r["status"] for r in self.inbox()], ["delivered"] * 12)

    def test_receiver_only_calls_receive_methods(self):
        methods = []

        def fake_call(token, method, payload=None):
            methods.append(method)
            if method == "getUpdates":
                return image_event("m1")
            return {}

        with mock.patch.dict("os.environ", {"HASC_DB_PATH": str(self.db_path)}):
            code = main(["--once", "--photos-dir", str(self.photos)], settings=(TOKEN, SOURCES),
                        call=fake_call, fetch=lambda url: jpeg())
        self.assertEqual(code, 0)
        self.assertEqual(methods, ["getMe", "getWebhookInfo", "getUpdates"])
        self.assertEqual(self.inbox()[0]["status"], "delivered")
        source = "".join(p.read_text(encoding="utf-8") for p in Path(receiver.__file__).parent.glob("*.py"))
        for send in ("sendMessage", "sendPhoto", "sendSticker", "sendVoice", "sendChatAction", "deleteWebhook"):
            self.assertNotIn(send, source)

    def test_webhook_in_use_stops_without_changing_it(self):
        def fake_call(token, method, payload=None):
            return {"url": "https://example.com/hook"} if method == "getWebhookInfo" else {}

        with mock.patch.dict("os.environ", {"HASC_DB_PATH": str(self.db_path)}):
            code = main(["--once"], settings=(TOKEN, SOURCES), call=fake_call)
        self.assertEqual(code, 1)
        state = self.conn.execute("SELECT * FROM zalo_receiver").fetchone()
        self.assertIn("webhook", state["last_error"])

    def test_api_errors_never_carry_the_token(self):
        error = urllib.error.HTTPError(f"https://bot-api.zaloplatforms.com/bot{TOKEN}/getMe", 401,
                                       f"bad token {TOKEN}", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ZaloError) as caught:
                receiver.call(TOKEN, "getMe")
        self.assertEqual(str(caught.exception), "getMe: HTTP 401")
        self.assertIsNone(caught.exception.__cause__)
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError(f"host bot{TOKEN}")):
            with self.assertRaises(ZaloError) as caught:
                receiver.call(TOKEN, "getUpdates")
        self.assertNotIn(TOKEN, str(caught.exception))
        with self.assertRaises(ZaloError) as caught:
            receiver.call("bad\ntoken-secret", "getMe")  # urllib's own message would hold the URL
        self.assertNotIn("token-secret", str(caught.exception))

    def test_no_webhook_404_and_empty_poll_408_are_not_errors(self):
        def answer(code):
            body = io.BytesIO(('{"ok": false, "error_code": %d, "description": "x"}' % code).encode())
            return mock.MagicMock(__enter__=lambda self: body)

        with mock.patch("urllib.request.urlopen", return_value=answer(404)):
            self.assertIsNone(receiver.call(TOKEN, "getWebhookInfo"))
        with mock.patch("urllib.request.urlopen", return_value=answer(408)):
            self.assertIsNone(receiver.call(TOKEN, "getUpdates"))
        with mock.patch("urllib.request.urlopen", return_value=answer(404)):
            with self.assertRaises(ZaloError):
                receiver.call(TOKEN, "getMe")

    def test_empty_long_poll_is_not_an_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError()):
            self.assertIsNone(receiver.call(TOKEN, "getUpdates"))
        self.assertEqual(receiver.events_of(None), [])
        self.assertEqual(len(receiver.events_of([image_event("a"), image_event("b")])), 2)


class TestHealth(ZaloTestCase):
    def test_health_tells_idle_from_dead_or_failing(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(receiver_health(self.conn, now)["state"], "ok")  # started, no photo yet: idle, not failed
        self.assertEqual(receiver_health(self.conn, now + timedelta(minutes=5))["state"], "stale")
        beat(self.conn, error="getUpdates: HTTP 500")
        self.assertEqual(receiver_health(self.conn, datetime.now(timezone.utc))["state"], "error")
        beat(self.conn)
        self.assertEqual(receiver_health(self.conn, datetime.now(timezone.utc))["state"], "ok")
        self.conn.execute("DELETE FROM zalo_receiver")
        self.assertEqual(receiver_health(self.conn, now)["state"], "never")

    def test_pilot_usage_excludes_staging_and_counts_vietnam_days(self):
        data = iter([jpeg((i * 30, 0, 0)) for i in range(4)])
        for mid, chat in (("a", STAGING), ("b", PILOT), ("c", PILOT), ("d", PILOT)):
            self.store(image_event(mid, chat_id=chat))
        self.store(image_event("b", chat_id=PILOT))  # redelivered
        deliver_pending(self.conn, self.photos, fetch=lambda url: next(data))
        # 17:30 UTC on 10/11 is 00:30 on 11/11 in Vietnam; 16:30 UTC is still 10/11.
        self.conn.execute("UPDATE zalo_inbox SET received_at = '2026-11-10T17:30:00+00:00' WHERE message_id IN ('a', 'b', 'c')")
        self.conn.execute("UPDATE zalo_inbox SET received_at = '2026-11-10T16:30:00+00:00' WHERE message_id = 'd'")
        self.conn.commit()
        overview = zalo_overview(self.conn, datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc))
        month = overview["usage"]["month"]
        self.assertEqual((month["pilot"]["events"], month["pilot"]["delivered"], month["pilot"]["redeliveries"]), (4, 3, 1))
        self.assertEqual(month["staging"]["events"], 1)
        days = {d["day"].isoformat(): d for d in overview["usage"]["days"]}
        self.assertEqual(len(days), 11)  # every day of the month so far, quiet ones included
        self.assertEqual(days["2026-11-11"]["pilot"]["events"], 3)
        self.assertEqual(days["2026-11-10"]["pilot"]["events"], 1)
        self.assertEqual(days["2026-11-01"]["pilot"]["events"], 0)
        self.assertEqual(overview["outgoing"], 0)
        self.assertEqual(overview["staging_test"]["saved"], 1)


class TestAiPageZalo(ZaloTestCase):
    def setUp(self):
        super().setUp()
        self.app = create_app(db_path=self.db_path, photos_dir=self.photos)
        for name, role in (("emp_user", "employee"), ("boss_user", "boss"), ("admin_user", "admin")):
            create_user(self.conn, name, hash_password("pass"), role=role, active=1)

    def client(self, name):
        client = TestClient(self.app)
        self.assertEqual(client.post("/api/login", json={"username": name, "password": "pass"}).status_code, 200)
        return client

    def test_page_shows_both_sections_without_secrets(self):
        self.store(image_event("m1", caption="/capture riêng tư"))
        self.store(image_event("m2"))
        deliver_pending(self.conn, self.photos, fetch=lambda url: jpeg())
        self.store(image_event("m3"))  # still waiting: its signed URL is in the database
        for name in ("boss_user", "admin_user"):
            with self.subTest(name=name):
                client = self.client(name)
                for url in ("/ai", "/ai/partial"):
                    text = client.get(url).text
                    self.assertIn("OpenRouter", text)
                    self.assertIn("Zalo Bot", text)
                    self.assertIn("Lượt đọc mỗi ngày", text)  # the existing views stay
                    self.assertIn("Đang nhận", text)
                    self.assertIn("Ảnh trùng", text)
                    self.assertNotIn("signature", text)
                    self.assertNotIn("riêng tư", text)
                    self.assertNotIn(TOKEN, text)
                self.assertEqual(client.get("/ai/zalo/1/photo").content, jpeg())
                self.assertNotIn("signature", client.get("/api/ai/stats").text)
        self.assertEqual(self.client("emp_user").get("/ai").status_code, 403)
        self.assertEqual(self.client("emp_user").get("/ai/zalo/1/photo").status_code, 403)

    def test_only_admins_retry_a_failed_photo(self):
        self.store(image_event("m1"))
        self.conn.execute("UPDATE zalo_inbox SET status = 'failed', attempts = 3, last_error = 'tải ảnh: HTTP 403'")
        self.conn.commit()
        boss = self.client("boss_user")
        self.assertNotIn("Thử tải lại", boss.get("/ai").text)
        self.assertEqual(boss.post("/ai/zalo/1/retry", follow_redirects=False).status_code, 403)
        admin = self.client("admin_user")
        self.assertIn("Thử tải lại", admin.get("/ai").text)
        self.assertEqual(admin.post("/ai/zalo/1/retry", follow_redirects=False).status_code, 303)
        row = self.inbox()[0]
        self.assertEqual((row["status"], row["attempts"], row["last_error"]), ("received", 0, None))
        self.assertEqual(admin.post("/ai/zalo/1/retry", follow_redirects=False).status_code, 409)
        deliver_pending(self.conn, self.photos, fetch=lambda url: jpeg())
        self.assertEqual(self.inbox()[0]["status"], "delivered")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM read_jobs").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
