"""The admins' update banner (spec 1.1.0 job C, P146).

The office PC's "HASC update check" task writes tools\\update-status.json once a day; the website only
reads it. A missing or broken file shows nothing: the banner is a hint, never a reason to fail a page.
"""
import json
import os
from datetime import datetime, timedelta, timezone

# An error is shown only once checks have failed for this long, so a short outage stays quiet.
STALE_AFTER = timedelta(days=3)


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def read_update_status(path: str | None = None, now: datetime | None = None) -> dict | None:
    """What the banner shows: {"available", "latest", "installed", "error"}, or None for nothing."""
    path = path if path is not None else os.environ.get("HASC_UPDATE_STATUS")
    if not path:
        return None
    try:
        # utf-8-sig: tolerate a byte order mark if a Windows tool ever adds one.
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    now = now or datetime.now(timezone.utc)
    error = data.get("error") or None
    if error:
        last_ok = _parse_time(data.get("ok_at"))
        if last_ok is not None and now - last_ok < STALE_AFTER:
            error = None
    available = bool(data.get("available")) and bool(data.get("latest"))
    if not available and not error:
        return None
    return {
        "available": available,
        "latest": data.get("latest"),
        "installed": data.get("installed"),
        "error": error,
    }
