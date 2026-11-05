"""Reading worker. Run: python -m hasc.worker [--once]

Reads HASC_DB_PATH for the database and HASC_WORKER_ENV for the reader's settings file. Without
--once it keeps polling for queued jobs. With --once it works the queue until it is empty and exits.
"""
import argparse
import tempfile
import time
from pathlib import Path

from hasc.db import get_connection, get_default_photos_dir
from hasc.worker.config import cloud_reader_from_env
from hasc.worker.jobs import recover_running_jobs, run_pending

POLL_SECONDS = 5


def main(argv=None, reader_factory=None, photos_dir=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="work the queue until it is empty, then exit")
    parser.add_argument("--photos-dir", type=Path, default=None, help="directory where photos are stored")
    args = parser.parse_args(argv)
    conn = get_connection()
    resolved_photos_dir = photos_dir or args.photos_dir or get_default_photos_dir()
    try:
        reader = (reader_factory or cloud_reader_from_env)()
        recover_running_jobs(conn)
        with tempfile.TemporaryDirectory() as scratch:
            while True:
                done = run_pending(conn, reader, Path(scratch), photos_dir=resolved_photos_dir)
                if args.once:
                    return
                if not done:
                    time.sleep(POLL_SECONDS)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
