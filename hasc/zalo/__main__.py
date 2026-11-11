"""Zalo receiver. Run: python -m hasc.zalo [--once]

Reads HASC_DB_PATH for the database and HASC_ZALO_ENV for the bot settings file, kept outside the
repository: ZALO_BOT_TOKEN, ZALO_STAGING_GROUP_ID and optionally ZALO_PILOT_GROUP_IDS (comma-separated
group IDs). The token is never printed. Run only one receiver per bot: getUpdates hands each event out once.
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


def settings_from_env(env_path=None):
    env_path = env_path or os.environ.get(ENV_VAR)
    if not env_path:
        sys.exit(f"{ENV_VAR} is not set")
    values = read_env(env_path)
    for name in ("ZALO_BOT_TOKEN", "ZALO_STAGING_GROUP_ID"):
        if not values.get(name):
            sys.exit(f"{name} missing from {env_path}")
    pilots = [g.strip() for g in values.get("ZALO_PILOT_GROUP_IDS", "").split(",") if g.strip()]
    return values["ZALO_BOT_TOKEN"], values["ZALO_STAGING_GROUP_ID"], pilots


def main(argv=None, settings=None, call=receiver.call, fetch=receiver.fetch, sleep=time.sleep):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="poll once, download what is waiting, then exit")
    parser.add_argument("--photos-dir", type=Path, default=None, help="directory where photos are stored")
    args = parser.parse_args(argv)
    token, staging, pilots = settings or settings_from_env()
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
        print(f"Receiving: staging group and {len(pilots)} pilot group(s). No chat messages are sent.", flush=True)
        while True:
            try:
                poll_once(conn, token, staging, pilots, photos_dir, call, fetch)
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
