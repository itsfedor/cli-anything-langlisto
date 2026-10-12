"""Time parsing helpers: local wall times → UTC ISO (Z) strings for the API."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/Istanbul"

FMTS = ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"]


def default_tz():
    import os

    return os.environ.get("LANGLISTO_TZ", DEFAULT_TZ)


def parse_when(s, tz_name=None):
    """'2026-10-13 09:00' (wall time in tz_name) → '2026-10-13T06:00:00Z'."""
    tz_name = tz_name or default_tz()
    s = s.strip()
    dt = None
    for f in FMTS:
        try:
            dt = datetime.strptime(s, f)
            break
        except ValueError:
            continue
    if dt is None:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_local(utc_iso, tz_name=None):
    dt = datetime.fromisoformat(str(utc_iso).replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo(tz_name or default_tz()))
