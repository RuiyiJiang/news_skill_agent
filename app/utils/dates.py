from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo


CHINESE_DATE_RE = re.compile(
    r"(?P<year>20\d{2})[年/-](?P<month>\d{1,2})[月/-](?P<day>\d{1,2})日?"
    r"(?:\s+(?P<hour>\d{1,2})(?::|点)(?P<minute>\d{1,2})?(?::(?P<second>\d{1,2}))?)?"
)

COMPACT_DATE_RE = re.compile(
    r"(?P<year>20\d{2})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})"
    r"(?:[ T](?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?"
)


def parse_datetime_text(
    raw_value: str | None,
    timezone_name: str,
    date_format_hint: str | None = None,
) -> datetime | None:
    if not raw_value:
        return None

    value = raw_value.strip()
    zone = ZoneInfo(timezone_name)

    explicit_candidates = []
    if date_format_hint:
        explicit_candidates.append(date_format_hint)
    explicit_candidates.extend(
        [
            "%d %B %Y",
            "%d %b %Y",
            "%B %d, %Y",
            "%b %d, %Y",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d",
        ]
    )

    for fmt in explicit_candidates:
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=zone)
        except ValueError:
            continue

    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=zone)
        return parsed.astimezone(zone)
    except (TypeError, ValueError, IndexError):
        pass

    normalized = value.replace("Z", "+00:00")
    for candidate in (normalized, normalized.split(".")[0]):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=zone)
            return parsed.astimezone(zone)
        except ValueError:
            continue

    for pattern in (CHINESE_DATE_RE, COMPACT_DATE_RE):
        match = pattern.search(value)
        if not match:
            continue
        groups = match.groupdict(default="0")
        try:
            return datetime(
                int(groups["year"]),
                int(groups["month"]),
                int(groups["day"]),
                int(groups.get("hour") or 0),
                int(groups.get("minute") or 0),
                int(groups.get("second") or 0),
                tzinfo=zone,
            )
        except ValueError:
            continue

    return None


def to_source_timezone(value: datetime, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def build_yesterday_today_window(
    now: datetime,
    timezone_name: str,
    window_days: int = 2,
) -> tuple[datetime, datetime]:
    local_now = to_source_timezone(now, timezone_name)
    today = local_now.date()
    start_day = today - timedelta(days=window_days - 1)
    start = datetime.combine(start_day, time.min, tzinfo=local_now.tzinfo)
    end = datetime.combine(today, time.max, tzinfo=local_now.tzinfo)
    return start, end


def is_in_yesterday_today_window(
    published_at: datetime | None,
    now: datetime,
    timezone_name: str,
    window_days: int = 2,
) -> bool | None:
    if published_at is None:
        return None
    start, end = build_yesterday_today_window(now, timezone_name, window_days=window_days)
    localized = to_source_timezone(published_at, timezone_name)
    return start <= localized <= end
