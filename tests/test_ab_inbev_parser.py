from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import ABInBevNewsMediaParser
from app.models import SourceConfig


def test_ab_inbev_parser_handles_press_releases_and_news_stories(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = ABInBevNewsMediaParser(settings)
    source = SourceConfig(
        name="百威英博",
        base_url="https://www.ab-inbev.com",
        list_urls=[
            "https://www.ab-inbev.com/news-media/press-releases",
            "https://www.ab-inbev.com/news-media/news-stories",
        ],
        parser_type="custom_ab_inbev_news_media",
        timezone="Europe/Brussels",
        max_items=10,
        window_days=3,
    )
    now = datetime.fromisoformat("2026-04-08T12:00:00+02:00")
    calls: list[tuple[str, int]] = []

    def fake_fetch_entries(*, content_type: str, limit: int, offset: int):
        calls.append((content_type, offset))
        if content_type == "press-releases":
            assert offset == 0
            return [
                {
                    "name": "Recent press release",
                    "data": {
                        "publishDate": 1775510100000,
                        "englishFile": "https://cdn.builder.io/press-release-en.pdf",
                        "frenchFile": "https://cdn.builder.io/press-release-fr.pdf",
                    },
                },
                {
                    "name": "Old press release",
                    "data": {
                        "publishDate": 1775164500000,
                        "englishFile": "https://cdn.builder.io/old-press-release-en.pdf",
                    },
                },
            ]
        if content_type == "news":
            assert offset == 0
            return [
                {
                    "name": "Recent story",
                    "data": {
                        "publishDate": 1775495700000,
                        "url": "/news-media/news-stories/recent-story",
                        "category": {"id": "fb065efe97d74c50a1999c38e8505c59"},
                        "blocks": [
                            {
                                "component": {
                                    "options": {
                                        "text": "<p>First paragraph from story.</p><p>Second paragraph from story.</p>"
                                    }
                                }
                            }
                        ],
                    },
                },
                {
                    "name": "Old story",
                    "data": {
                        "publishDate": 1775164500000,
                        "url": "/news-media/news-stories/old-story",
                        "category": {"id": "fb065efe97d74c50a1999c38e8505c59"},
                        "oldContent": "<p>Older story text.</p>",
                    },
                },
            ]
        raise AssertionError(f"unexpected content type: {content_type}")

    monkeypatch.setattr(parser, "_fetch_entries", fake_fetch_entries)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["Recent press release", "Recent story"]
    assert str(items[0].url) == "https://cdn.builder.io/press-release-en.pdf"
    assert items[0].summary == "PDF downloads: EN / FR"
    assert str(items[1].url) == "https://www.ab-inbev.com/news-media/news-stories/recent-story"
    assert items[1].summary.startswith("Company News | ")
    assert "First paragraph from story." in items[1].summary
    assert calls == [("press-releases", 0), ("news", 0)]

