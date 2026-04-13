from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import KraftHeinzPressReleaseParser
from app.models import SourceConfig


def test_kraft_heinz_parser_reads_q4_feed(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = KraftHeinzPressReleaseParser(settings)
    source = SourceConfig(
        name="卡夫亨氏",
        base_url="https://news.kraftheinzcompany.com",
        list_urls=["https://news.kraftheinzcompany.com/press-releases/default.aspx"],
        parser_type="custom_kraft_heinz_press_releases",
        timezone="America/Chicago",
        max_items=10,
        window_days=4,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00-05:00")

    payload = {
        "GetPressReleaseListResult": [
            {
                "Headline": "Oscar Mayer Unveils First Bacon Innovation in Five Years with New Maple Bourbon Bacon",
                "LinkToDetailPage": "/press-releases-details/2026/Oscar-Mayer-Unveils-First-Bacon-Innovation-in-Five-Years-with-New-Maple-Bourbon-Bacon/default.aspx",
                "PressReleaseDate": "04/08/2026 07:01:00",
                "ShortDescription": "",
                "ShortBody": None,
                "Subheadline": None,
            },
            {
                "Headline": "Kraft Heinz Inks Breakthrough Deal With National Football League as First-Ever Condiment Partner",
                "LinkToDetailPage": "/press-releases-details/2026/Kraft-Heinz-Inks-Breakthrough-Deal-With-National-Football-League-as-First-Ever-Condiment-Partner/default.aspx",
                "PressReleaseDate": "03/18/2026 11:46:00",
                "ShortDescription": "",
                "ShortBody": None,
                "Subheadline": None,
            },
        ]
    }

    monkeypatch.setattr(parser, "_fetch_feed", lambda limit: payload)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "Oscar Mayer Unveils First Bacon Innovation in Five Years with New Maple Bourbon Bacon"
    ]
    assert str(items[0].url) == (
        "https://news.kraftheinzcompany.com/press-releases-details/2026/"
        "Oscar-Mayer-Unveils-First-Bacon-Innovation-in-Five-Years-with-New-Maple-Bourbon-Bacon/default.aspx"
    )
    assert items[0].published_at == datetime.fromisoformat("2026-04-08T07:01:00-05:00")
    assert items[0].summary == ""
