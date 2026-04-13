from __future__ import annotations

from datetime import datetime

from app.models import NewsItem
from app.utils.dedupe import dedupe_news_items


def make_item(
    *,
    title: str,
    url: str,
    summary: str = "",
    source: str = "Source",
    published_at: datetime | None = None,
    raw_date_text: str | None = None,
) -> NewsItem:
    return NewsItem(
        title=title,
        summary=summary,
        published_at=published_at,
        url=url,
        source=source,
        collected_at=datetime.fromisoformat("2026-04-01T09:00:00+08:00"),
        raw_date_text=raw_date_text,
        date_parse_status="parsed" if published_at else "failed",
    )


def test_dedupe_by_url_keeps_more_complete_item() -> None:
    first = make_item(title="A", url="https://example.com/a", summary="")
    second = make_item(title="A", url="https://example.com/a", summary="summary")
    result = dedupe_news_items([first, second])
    assert len(result) == 1
    assert result[0].summary == "summary"


def test_dedupe_by_title_and_date() -> None:
    dt = datetime.fromisoformat("2026-04-01T09:00:00+08:00")
    first = make_item(title="Same", url="https://example.com/a", published_at=dt)
    second = make_item(title="Same", url="https://example.com/b", published_at=dt, summary="full")
    result = dedupe_news_items([first, second])
    assert len(result) == 1
    assert str(result[0].url) == "https://example.com/b"


def test_dedupe_with_raw_date_text_when_published_at_missing() -> None:
    first = make_item(title="Same", url="https://example.com/a", raw_date_text="2026-04-01")
    second = make_item(
        title="Same",
        url="https://example.com/b",
        raw_date_text="2026-04-01",
        summary="more",
    )
    result = dedupe_news_items([first, second])
    assert len(result) == 1
    assert result[0].summary == "more"


def test_dedupe_keeps_same_article_from_different_sources() -> None:
    dt = datetime.fromisoformat("2026-04-01T09:00:00+08:00")
    first = make_item(
        title="Same",
        url="https://example.com/a",
        published_at=dt,
        source="Source A",
    )
    second = make_item(
        title="Same",
        url="https://example.com/a",
        published_at=dt,
        source="Source B",
    )
    result = dedupe_news_items([first, second])
    assert len(result) == 2
    assert {item.source for item in result} == {"Source A", "Source B"}
