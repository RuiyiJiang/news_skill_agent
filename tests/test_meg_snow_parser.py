from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import MegSnowNewsParser
from app.models import SourceConfig


def test_meg_snow_news_parser_reads_cards() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = MegSnowNewsParser(settings)
    source = SourceConfig(
        name="雪印惠乳业",
        base_url="https://www.meg-snow.com",
        list_urls=["https://www.meg-snow.com/news/"],
        parser_type="custom_meg_snow_news",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=4,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    html = """
    <html><body>
      <ul class="p-news-list" id="js-news-list">
        <li class="p-news-item" data-categories="2-3-" data-year="2026">
          <a href="/news/detail/35112" class="p-news-item__inner">
            <div class="p-news-item__meta">
              <div class="p-news-item__date">2026年04月09日</div>
              <div class="c-tag c-tag--fill"><span>サステナビリティ</span></div>
            </div>
            <p class="p-news-item__title">従業員の健康や食生活改善を推進</p>
          </a>
        </li>
        <li class="p-news-item" data-categories="2-1-" data-year="2026">
          <a href="/news/detail/35096" class="p-news-item__inner">
            <div class="p-news-item__meta">
              <div class="p-news-item__date">2026年03月27日</div>
              <div class="c-tag c-tag--fill"><span>商品・キャンペーン</span></div>
            </div>
            <p class="p-news-item__title">old item</p>
          </a>
        </li>
      </ul>
    </body></html>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    parser.client.get = lambda url: DummyResponse(html)  # type: ignore[method-assign]

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["従業員の健康や食生活改善を推進"]
    assert str(items[0].url) == "https://www.meg-snow.com/news/detail/35112"
    assert items[0].published_at == datetime.fromisoformat("2026-04-09T00:00:00+09:00")
    assert items[0].summary == "サステナビリティ"
