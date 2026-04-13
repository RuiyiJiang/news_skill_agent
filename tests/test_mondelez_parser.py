from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import MondelezNewsParser
from app.models import SourceConfig


def test_mondelez_news_parser_prefers_rendered_dom_when_available(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = MondelezNewsParser(settings)
    source = SourceConfig(
        name="亿滋国际",
        base_url="https://www.mondelezinternational.com",
        list_urls=["https://www.mondelezinternational.com/news/"],
        parser_type="custom_mondelez_news",
        timezone="America/Chicago",
        max_items=10,
        window_days=30,
    )
    now = datetime.fromisoformat("2026-02-10T12:00:00-06:00")

    rendered_html = """
    <html><body>
      <div class="newsMainWrapContainer">
        <div class="NewsStoryWrapper">
          <div>
            <div>
              <a href="/news?filter=sustainability/">Sustainability</a>
            </div>
            <div>
              <div><p>Friday, January 16, 2026</p></div>
              <div><a href="/news/mondelez-all-a-scores-cdp-reporting/">MONDELĒZ INTERNATIONAL SCORES ALL A'S – A COMPANY FIRST IN CDP SUSTAINABILITY REPORTING</a></div>
              <div><p>Proud to be helping build an Earth-positive future</p></div>
            </div>
          </div>
          <div>
            <div>
              <a href="/news?filter=business/">Business</a>
            </div>
            <div>
              <div><p>Tuesday, February 3, 2026</p></div>
              <div><a href="/news/2025_q4_fy_earnings/">REPORTING FOURTH QUARTER &amp; FY 2025 EARNINGS</a></div>
              <div><p>Quarterly earnings update.</p></div>
            </div>
          </div>
        </div>
      </div>
    </body></html>
    """

    requested_urls: list[str] = []

    def fake_fetch_text(url: str) -> str:
        requested_urls.append(url)
        if url == "https://www.mondelezinternational.com/news/":
            return rendered_html
        raise AssertionError(f"unexpected fetch url: {url}")

    monkeypatch.setattr(parser, "_fetch_text", fake_fetch_text)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "MONDELĒZ INTERNATIONAL SCORES ALL A'S – A COMPANY FIRST IN CDP SUSTAINABILITY REPORTING",
        "REPORTING FOURTH QUARTER & FY 2025 EARNINGS",
    ]
    assert [item.raw_date_text for item in items] == [
        "January 16, 2026",
        "February 3, 2026",
    ]
    assert [item.summary for item in items] == [
        "Proud to be helping build an Earth-positive future",
        "Quarterly earnings update.",
    ]
    assert requested_urls == ["https://www.mondelezinternational.com/news/"]


def test_mondelez_news_parser_uses_page_data_and_filters_by_window(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = MondelezNewsParser(settings)
    source = SourceConfig(
        name="亿滋国际",
        base_url="https://www.mondelezinternational.com",
        list_urls=["https://www.mondelezinternational.com/news/"],
        parser_type="custom_mondelez_news",
        timezone="America/Chicago",
        max_items=10,
        window_days=4,
    )
    now = datetime.fromisoformat("2026-04-09T12:00:00-05:00")

    payload = {
        "result": {
            "pageContext": {
                "componentProps": [
                    {
                        "ListPaginationFilterWrapper": {
                            "newsCardsListCollection": {
                                "items": [
                                    {
                                        "date": "Wednesday, April 09, 2026",
                                        "description": "Newest update.",
                                        "titleLink": {
                                            "label": "Mondelez shares spring update",
                                            "url": "/News/spring-update/",
                                        },
                                    },
                                    {
                                        "date": "Monday, April 07, 2026",
                                        "description": "Second update.",
                                        "titleLink": {
                                            "title": "Mondelez opens new innovation center",
                                            "url": "/News/innovation-center/",
                                        },
                                    },
                                    {
                                        "date": "Friday, April 04, 2026",
                                        "description": "Too old for the configured window.",
                                        "titleLink": {
                                            "name": "Outside the time window",
                                            "url": "/News/outside-window/",
                                        },
                                    },
                                ]
                            }
                        }
                    }
                ]
            }
        }
    }

    requested_urls: list[str] = []

    def fake_fetch_text(url: str) -> str:
        requested_urls.append(url)
        if url == "https://www.mondelezinternational.com/news/":
            return "<html><body><div>No rendered cards</div></body></html>"
        assert url == "https://www.mondelezinternational.com/page-data/news/page-data.json"
        return json.dumps(payload)

    monkeypatch.setattr(parser, "_fetch_text", fake_fetch_text)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "Mondelez shares spring update",
        "Mondelez opens new innovation center",
    ]
    assert [item.raw_date_text for item in items] == [
        "April 09, 2026",
        "April 07, 2026",
    ]
    assert [str(item.url) for item in items] == [
        "https://www.mondelezinternational.com/News/spring-update/",
        "https://www.mondelezinternational.com/News/innovation-center/",
    ]
    assert requested_urls == [
        "https://www.mondelezinternational.com/news/",
        "https://www.mondelezinternational.com/page-data/news/page-data.json",
    ]


def test_mondelez_news_parser_skips_missing_date_items(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = MondelezNewsParser(settings)
    source = SourceConfig(
        name="亿滋国际",
        base_url="https://www.mondelezinternational.com",
        list_urls=["https://www.mondelezinternational.com/news/"],
        parser_type="custom_mondelez_news",
        timezone="America/Chicago",
        max_items=10,
        window_days=4,
    )
    now = datetime.fromisoformat("2026-04-09T12:00:00-05:00")

    payload = {
        "result": {
            "pageContext": {
                "componentProps": [
                    {
                        "ListPaginationFilterWrapper": {
                            "newsCardsListCollection": {
                                "items": [
                                    {
                                        "date": None,
                                        "description": "Profile story without a date.",
                                        "titleLink": {
                                            "label": "Meet the Mondelez team",
                                            "url": "/News/meet-the-team/",
                                        },
                                    }
                                ]
                            }
                        }
                    }
                ]
            }
        }
    }

    def fake_fetch_text(url: str) -> str:
        if url == "https://www.mondelezinternational.com/news/":
            return "<html><body><div>No rendered cards</div></body></html>"
        return json.dumps(payload)

    monkeypatch.setattr(parser, "_fetch_text", fake_fetch_text)

    items = parser.fetch_recent(source, now)

    assert items == []
