from __future__ import annotations

from datetime import datetime

from app.utils.dates import (
    build_yesterday_today_window,
    is_in_yesterday_today_window,
    parse_datetime_text,
)


def test_parse_iso_date() -> None:
    parsed = parse_datetime_text("2026-04-01T09:30:00+08:00", "Asia/Shanghai")
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.month == 4
    assert parsed.day == 1


def test_parse_chinese_date() -> None:
    parsed = parse_datetime_text("2026年4月1日 09:30", "Asia/Shanghai")
    assert parsed is not None
    assert parsed.hour == 9
    assert parsed.minute == 30


def test_parse_english_date() -> None:
    parsed = parse_datetime_text("Wed, 01 Apr 2026 01:30:00 GMT", "Asia/Shanghai")
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_yesterday_today_window() -> None:
    now = datetime.fromisoformat("2026-04-01T09:00:00+08:00")
    start, end = build_yesterday_today_window(now, "Asia/Shanghai")
    assert start.isoformat() == "2026-03-31T00:00:00+08:00"
    assert end.isoformat() == "2026-04-01T23:59:59.999999+08:00"


def test_custom_window_days() -> None:
    now = datetime.fromisoformat("2026-04-07T09:00:00+08:00")
    start, end = build_yesterday_today_window(now, "Asia/Shanghai", window_days=3)
    assert start.isoformat() == "2026-04-05T00:00:00+08:00"
    assert end.isoformat() == "2026-04-07T23:59:59.999999+08:00"


def test_date_in_window_and_outside() -> None:
    now = datetime.fromisoformat("2026-04-01T09:00:00+08:00")
    inside = datetime.fromisoformat("2026-03-31T12:00:00+08:00")
    outside = datetime.fromisoformat("2026-03-30T23:59:59+08:00")
    assert is_in_yesterday_today_window(inside, now, "Asia/Shanghai") is True
    assert is_in_yesterday_today_window(outside, now, "Asia/Shanghai") is False


def test_date_in_custom_window() -> None:
    now = datetime.fromisoformat("2026-04-07T09:00:00+08:00")
    inside = datetime.fromisoformat("2026-04-05T12:00:00+08:00")
    outside = datetime.fromisoformat("2026-04-04T23:59:59+08:00")
    assert is_in_yesterday_today_window(inside, now, "Asia/Shanghai", window_days=3) is True
    assert is_in_yesterday_today_window(outside, now, "Asia/Shanghai", window_days=3) is False


def test_unparseable_returns_none() -> None:
    assert parse_datetime_text("not a date", "Asia/Shanghai") is None
