"""Zalo receiver. Run: python -m hasc.zalo [--once]

Reads HASC_DB_PATH for the database and HASC_ZALO_ENV for the bot settings file, kept outside the
repository: ZALO_BOT_TOKEN and at least one comma-separated source list. Workers send photos to the
bot in a private chat, so the usual lists are ZALO_STAGING_SENDER_IDS and ZALO_PILOT_SENDER_IDS;
ZALO_STAGING_GROUP_ID and ZALO_PILOT_GROUP_IDS also work, but only for photos that mention the bot.
The token is never printed. Run only one receiver per bot: getUpdates hands each event out once.
"""
import argparse
import os
import sys
import time
from pathlib import Path

from hasc.db import get_connection, get_default_photos_dir
from hasc.worker.config import read_env
from hasc.zalo import receiver
from hasc.zalo.receiver import ZaloError, beat, check_connection, poll_once

ENV_VAR = "HASC_ZALO_ENV"
RETRY_SECONDS = (5, 15, 60, 300)  # waits after consecutive failures, then the last one repeats
BEAT_SECONDS = 30
SOURCE_LISTS = {
    "ZALO_STAGING_SENDER_IDS": ("PRIVATE", 1), "ZALO_PILOT_SENDER_IDS": ("PRIVATE", 0),
    "ZALO_STAGING_GROUP_ID": ("GROUP", 1), "ZALO_PILOT_GROUP_IDS": ("GROUP", 0),
}


def settings_from_env(env_path=None):
    env_path = env_path or os.environ.get(ENV_VAR)
    if not env_path:
        sys.exit(f"{ENV_VAR} is not set")
    values = read_env(env_path)
    if not values.get("ZALO_BOT_TOKEN"):
        sys.exit(f"ZALO_BOT_TOKEN missing from {env_path}")
    sources = {}
    for name, (kind, staging) in SOURCE_LISTS.items():
        for source_id in (s.strip() for s in values.get(name, "").split(",")):
            if source_id and sources.get((kind, source_id), staging) != staging:
                sys.exit(f"{source_id} is listed as both staging and pilot in {env_path}")
            if source_id:
                sources[(kind, source_id)] = staging
    if not sources:
        sys.exit(f"no sender or group IDs in {env_path}: set ZALO_STAGING_SENDER_IDS")
    return values["ZALO_BOT_TOKEN"], sources


def main(argv=None, settings=None, call=receiver.call, fetch=receiver.fetch, sleep=time.sleep):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="poll once, download what is waiting, then exit")
    parser.add_argument("--photos-dir", type=Path, default=None, help="directory where photos are stored")
    parser.add_argument("--show-ids", action="store_true",
                        help="print full sender and group IDs of ignored events, to copy into the settings file")
    args = parser.parse_args(argv)
    token, sources = settings or settings_from_env()
    conn = get_connection()
    photos_dir = args.photos_dir or get_default_photos_dir()
    failures = 0
    try:
        beat(conn, started=True)
        try:
            check_connection(token, call)
        except ZaloError as error:
            beat(conn, error=str(error))
            print(f"Stopped: {error}")
            return 1
        staging = sum(sources.values())
        print(f"Receiving from {staging} staging and {len(sources) - staging} pilot source(s). "
              "No chat messages are sent.", flush=True)
        while True:
            try:
                poll_once(conn, token, sources, photos_dir, call, fetch, args.show_ids)
                failures = 0
            except Exception as error:  # keep receiving; a non-Zalo error is named by type only
                message = str(error) if isinstance(error, ZaloError) else f"lỗi nội bộ: {type(error).__name__}"
                beat(conn, error=message)
                print(f"Error: {message}", flush=True)
                if args.once:
                    return 1
                wait = RETRY_SECONDS[min(failures, len(RETRY_SECONDS) - 1)]
                for _ in range(0, wait, BEAT_SECONDS):  # stay "alive" on the AI page while waiting
                    sleep(min(BEAT_SECONDS, wait))
                    beat(conn, ok=False)
                failures += 1
            if args.once:
                return 0
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
