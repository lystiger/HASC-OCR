"""Reading worker. Run: python -m hasc.worker [--once]

Reads HASC_DB_PATH for the database and HASC_WORKER_ENV for the reader's settings file. Without
--once it keeps polling for queued jobs. With --once it works the queue until it is empty and exits.
With HASC_GATE_MINUTES set, it also gates delivered Zalo photos before reading (zalo-intake spec R1).
"""
import argparse
from datetime import date, datetime, time as clock, timedelta, timezone
import tempfile
import time
from pathlib import Path

from hasc.console import make_output_safe
from hasc.db import get_connection, get_default_photos_dir
from hasc.intake import VN
from hasc.worker.config import cloud_reader_from_env, worker_settings
from hasc.worker.gate import run_gate_batch
from hasc.worker.jobs import recover_running_jobs, run_pending

POLL_SECONDS = 5
MORNING_BATCH = clock(8, 30)  # photos sent at the 07:00 shift change are gated before the 9:00 summary


def gate_due(last_batch: datetime | None, last_morning: date | None, now: datetime, minutes: int) -> bool:
    """A batch at start, every `minutes`, and once at the first loop at or after 08:30 Vietnam time each day."""
    if minutes <= 0:
        return False
    if last_batch is None or now - last_batch >= timedelta(minutes=minutes):
        return True
    local = now.astimezone(VN)
    return local.time() >= MORNING_BATCH and last_morning != local.date()


def main(argv=None, reader_factory=None, photos_dir=None):
    make_output_safe()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="work the queue until it is empty, then exit")
    parser.add_argument("--photos-dir", type=Path, default=None, help="directory where photos are stored")
    args = parser.parse_args(argv)
    settings = worker_settings()
    conn = get_connection()
    resolved_photos_dir = photos_dir or args.photos_dir or get_default_photos_dir()
    last_batch = last_morning = None
    try:
        reader = (reader_factory or cloud_reader_from_env)()
        recover_running_jobs(conn)
        with tempfile.TemporaryDirectory() as scratch:
            while True:
                now = datetime.now(timezone.utc)
                if gate_due(last_batch, last_morning, now, settings.gate_minutes):
                    last_batch = now
                    if now.astimezone(VN).time() >= MORNING_BATCH:
                        last_morning = now.astimezone(VN).date()
                    try:
                        counts = run_gate_batch(conn, reader.cloud, resolved_photos_dir, now,
                                                clock=lambda: datetime.now(timezone.utc))
                        print(f"Gate: {counts}", flush=True)
                    except Exception as error:  # reading must go on; the next batch retries the photos
                        print(f"Gate batch failed: {type(error).__name__}: {error}", flush=True)
                done = run_pending(conn, reader, Path(scratch), photos_dir=resolved_photos_dir,
                                   plan_dir=settings.plan_dir)
                if args.once:
                    return
                if not done:
                    time.sleep(POLL_SECONDS)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
