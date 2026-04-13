from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import NissinNewsParser
from app.models import SourceConfig


def test_nissin_news_parser_reads_dom_and_api_pages(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = NissinNewsParser(settings)
    source = SourceConfig(
        name="日清食品集团",
        base_url="https://www.nissin.com",
        list_urls=["https://www.nissin.com/jp/company/news/"],
        parser_type="custom_nissin_news",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=4,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    html = """
    <html><body>
      <div class="ListCardNews" data-news="/api/ja_jp/news/?page=2&region=ja_jp&limit=20">
        <div class="ListCardNews__item">
          <div class="CardNews -horizontal">
            <a href="/jp/news/13722/" class="CardNews__link">
              <div class="CardNews__body">
                <div class="CardNews__metadata">
                  <div class="CardNews__date">2026.04.09</div>
                  <div class="CardNews__group">湖池屋</div>
                </div>
                <div class="CardNews__title">「湖池屋スナックブーケ」(4月9日より受注販売開始)</div>
              </div>
            </a>
          </div>
        </div>
      </div>
    </body></html>
    """

    api_payload = {
        "next_page": "",
        "news": [
            {
                "date": "202604061100",
                "title": "「日清の汁なしどん兵衛 だしソース焼うどん」(4月20日発売)",
                "lead": "新商品のお知らせです。",
                "permalink": "/jp/news/13721",
                "category": {"label": "プレスリリース"},
                "company": {"label": "日清食品"},
            },
            {
                "date": "202604011100",
                "title": "old item",
                "lead": "",
                "permalink": "/jp/news/13700",
                "category": {"label": "プレスリリース"},
                "company": {"label": "日清食品"},
            },
        ],
    }

    class DummyResponse:
        def __init__(self, *, text: str = "", json_data: dict | None = None) -> None:
            self.text = text
            self._json_data = json_data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._json_data or {}

    def fake_get(url: str) -> DummyResponse:
        if "/api/ja_jp/news/" in url:
            return DummyResponse(json_data=api_payload)
        return DummyResponse(text=html)

    monkeypatch.setattr(parser.client, "get", fake_get)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "「湖池屋スナックブーケ」(4月9日より受注販売開始)",
        "「日清の汁なしどん兵衛 だしソース焼うどん」(4月20日発売)",
    ]
    assert str(items[0].url) == "https://www.nissin.com/jp/news/13722/"
    assert items[0].summary == "湖池屋"
    assert items[1].published_at == datetime.fromisoformat("2026-04-06T11:00:00+09:00")
    assert items[1].summary == "日清食品 | プレスリリース"
    assert items[1].content_preview == "新商品のお知らせです。"
