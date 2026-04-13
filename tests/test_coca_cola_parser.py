from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import CocaColaMediaCenterParser
from app.models import SourceConfig


def test_coca_cola_media_center_parser_prefers_search_api_and_keeps_latest_items(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = CocaColaMediaCenterParser(settings)
    source = SourceConfig(
        name="可口可乐",
        base_url="https://www.coca-colacompany.com",
        list_urls=["https://www.coca-colacompany.com/media-center-"],
        parser_type="custom_coca_cola_media_center",
        timezone="America/New_York",
        max_items=10,
        window_days=10,
    )
    now = datetime.fromisoformat("2026-04-09T12:00:00-04:00")

    list_html = """
    <html><body>
      <script>
        window.tccc = window.tccc || {};
        window.tccc.cloudsearch = "company_us_en";
      </script>
      <div id="searchResult" data-params='{
        "contentTypeFilters":[
          {"contentType":"Media Center - Article","label":"News"},
          {"contentType":"Media Center - Press Release","label":"Press Release"}
        ]
      }'></div>
    </body></html>
    """

    api_payload = {
        "hits": {
            "found": 253,
            "start": 0,
            "hit": [
                {
                    "id": "1",
                    "fields": {
                        "path": "/media-center/coca-cola-unveils-uncanned-emotions-for-fifa-world-cup-2026.html",
                        "title": 'Coca-Cola Unveils "Uncanned Emotions," Portraying the Raw Passion of Football Fans Ahead of FIFA World Cup 2026™ ',
                        "description": "Experience the second film in Coca-Cola’s series, featuring Peter Drury and Luis Omar Tapia, bringing fans' raw football passion to life on April 8, 2026.",
                        "publication_date": "2026-04-08T15:27:00Z",
                        "content_type": "Media Center - Press Release",
                    },
                },
                {
                    "id": "2",
                    "fields": {
                        "path": "/media-center/coca-cola-celebrates-america250-with-community-initiatives-and-nationwide-events.html",
                        "title": "Coca-Cola Marks America’s 250th Anniversary With Nationwide Celebration and Community Initiatives",
                        "description": "Limited-edition America250 mini-cans feature unique designs for all 50 states and Washington, D.C.; Coca-Cola aims for 250,000 volunteer hours in 2026.",
                        "publication_date": "2026-04-06T17:04:00Z",
                        "content_type": "Media Center - Article",
                    },
                },
                {
                    "id": "3",
                    "fields": {
                        "path": "/media-center/coca-cola-brings-13-iconic-restaurants-together-in-and-a-coke-campaign.html",
                        "title": "Coca-Cola Unites Iconic Foodservice Partners Together for the First Time in “And a Coke” Campaign ",
                        "description": 'Experience the "And a Coke" campaign: Three short films debut in U.S. cinemas on April 3, with delivery app rollouts coming mid-April.',
                        "publication_date": "2026-04-02T10:26:00Z",
                        "content_type": "Media Center - Article",
                    },
                },
                {
                    "id": "4",
                    "fields": {
                        "path": "/media-center/timing-of-first-quarter-2026-earnings-release.html",
                        "title": "The Coca-Cola Company Announces Timing of First Quarter 2026 Earnings Release",
                        "description": "The Coca-Cola Company today announced it will release first quarter 2026 financial results April 28.",
                        "publication_date": "2026-03-31T17:39:00Z",
                        "content_type": "Media Center - Press Release",
                    },
                },
            ],
        }
    }

    requested_urls: list[str] = []

    def fake_fetch_text(url: str) -> str:
        requested_urls.append(url)
        if url == "https://www.coca-colacompany.com/media-center-":
            return list_html
        raise AssertionError(f"unexpected text fetch url: {url}")

    class DummyResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    def fake_get(url: str, params: dict | None = None) -> DummyResponse:
        requested_urls.append(url)
        assert url == "https://www.coca-colacompany.com/api/search"
        assert params == {
            "q": "(and (or content_type: 'Media Center - Article' content_type: 'Media Center - Press Release'))",
            "q.parser": "structured",
            "sort": "publication_date desc",
            "fq": "site:'company_us_en'",
            "start": 0,
            "size": 50,
        }
        return DummyResponse(api_payload)

    monkeypatch.setattr(parser, "_fetch_text", fake_fetch_text)
    monkeypatch.setattr(parser.client, "get", fake_get)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        'Coca-Cola Unveils "Uncanned Emotions," Portraying the Raw Passion of Football Fans Ahead of FIFA World Cup 2026™',
        "Coca-Cola Marks America’s 250th Anniversary With Nationwide Celebration and Community Initiatives",
        "Coca-Cola Unites Iconic Foodservice Partners Together for the First Time in “And a Coke” Campaign",
        "The Coca-Cola Company Announces Timing of First Quarter 2026 Earnings Release",
    ]
    assert [item.raw_date_text for item in items] == [
        "2026-04-08T15:27:00Z",
        "2026-04-06T17:04:00Z",
        "2026-04-02T10:26:00Z",
        "2026-03-31T17:39:00Z",
    ]
    assert requested_urls == [
        "https://www.coca-colacompany.com/media-center-",
        "https://www.coca-colacompany.com/api/search",
    ]


def test_coca_cola_media_center_detail_date_supports_publication_date_meta() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = CocaColaMediaCenterParser(settings)
    html = """
    <html><head>
      <meta name="publicationDate" content="2026-03-31T17:39:00Z"/>
    </head><body>
      <p class="cmp-publication-date">03-31-2026</p>
    </body></html>
    """

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    assert parser._extract_detail_date(soup, html) == "2026-03-31T17:39:00Z"


def test_coca_cola_media_center_parser_uses_page_specific_content_types(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = CocaColaMediaCenterParser(settings)
    source = SourceConfig(
        name="可口可乐日本",
        base_url="https://www.coca-cola.com",
        list_urls=["https://www.coca-cola.com/jp/ja/media-center"],
        parser_type="custom_coca_cola_media_center",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=10,
    )
    now = datetime.fromisoformat("2026-04-09T12:00:00+09:00")

    list_html = """
    <html><body>
      <script>
        window.tccc = window.tccc || {};
        window.tccc.cloudsearch = "onexp_jp_ja";
      </script>
      <div id="searchResult" data-params='{
        "contentTypeFilters":[
          {"contentType":"Media Center - Press Release","label":"ニュースリリース"},
          {"contentType":"Media Center - Company Statement","label":"お知らせ"}
        ]
      }'></div>
    </body></html>
    """

    api_payload = {
        "hits": {
            "found": 2,
            "start": 0,
            "hit": [
                {
                    "id": "jp1",
                    "fields": {
                        "path": "/jp/ja/media-center/news-20260406-19.html",
                        "title": "高橋文哉さん・原田泰造さん・麻生久美子さん・畑芽育さんが再集結",
                        "description": "実写化ショートムービーのシーズン2。",
                        "publication_date": "2026-04-06T00:00:00Z",
                        "content_type": "Media Center - Press Release",
                    },
                },
                {
                    "id": "jp2",
                    "fields": {
                        "path": "/jp/ja/media-center/company-statement-sample.html",
                        "title": "お知らせサンプル",
                        "description": "会社からのお知らせ。",
                        "publication_date": "2026-04-05T00:00:00Z",
                        "content_type": "Media Center - Company Statement",
                    },
                },
            ],
        }
    }

    def fake_fetch_text(url: str) -> str:
        assert url == "https://www.coca-cola.com/jp/ja/media-center"
        return list_html

    class DummyResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    def fake_get(url: str, params: dict | None = None) -> DummyResponse:
        assert url == "https://www.coca-cola.com/api/search"
        assert params == {
            "q": "(and (or content_type: 'Media Center - Press Release' content_type: 'Media Center - Company Statement'))",
            "q.parser": "structured",
            "sort": "publication_date desc",
            "fq": "site:'onexp_jp_ja'",
            "start": 0,
            "size": 50,
        }
        return DummyResponse(api_payload)

    monkeypatch.setattr(parser, "_fetch_text", fake_fetch_text)
    monkeypatch.setattr(parser.client, "get", fake_get)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "高橋文哉さん・原田泰造さん・麻生久美子さん・畑芽育さんが再集結",
        "お知らせサンプル",
    ]
