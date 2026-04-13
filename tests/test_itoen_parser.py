from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import ItoEnReleaseParser
from app.models import SourceConfig


def test_itoen_release_parser_reads_cards_and_pager() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = ItoEnReleaseParser(settings)
    source = SourceConfig(
        name="伊藤园",
        base_url="https://www.itoen.co.jp",
        list_urls=["https://www.itoen.co.jp/news/release/"],
        parser_type="custom_ito_en_release",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=10,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    page_one = """
    <html><body>
      <div class="p-newsListItemHaveThumb__wrap p-newsListItemHaveThumb__wrap__typeD">
        <div class="p-newsListItemHaveThumb__inner">
          <a href="https://www.itoen.co.jp/news/article/85340/" class="p-newsListItemHaveThumb">
            <div class="p-newsListItemHaveThumb__body">
              <div class="p-newsListItemHaveThumb__info --row">
                <time class="p-newsListItemHaveThumb__pubDate" datetime="2026.04.08">2026.04.08</time>
                <ul class="p-newsListItemHaveThumb__categoryIconList">
                  <li class="p-newsListItemHaveThumb__categoryIcon__Plain">サステナビリティ</li>
                </ul>
              </div>
              <h3 class="c-heading -level5"><span class="u-fontExtended">茶殻をアップサイクルした新素材を開発</span></h3>
              <p class="p-newsListItemHaveThumb__subtitle">subtitle</p>
            </div>
          </a>
        </div>
      </div>
      <div class="wp-pagenavi"><a class="nextpostslink" href="https://www.itoen.co.jp/news/release/page/2/">次へ</a></div>
    </body></html>
    """

    page_two = """
    <html><body>
      <div class="p-newsListItemHaveThumb__wrap p-newsListItemHaveThumb__wrap__typeD">
        <div class="p-newsListItemHaveThumb__inner">
          <a href="https://www.itoen.co.jp/news/article/84453/" class="p-newsListItemHaveThumb">
            <div class="p-newsListItemHaveThumb__body">
              <div class="p-newsListItemHaveThumb__info --row">
                <time class="p-newsListItemHaveThumb__pubDate" datetime="2026.03.20">2026.03.20</time>
                <ul class="p-newsListItemHaveThumb__categoryIconList">
                  <li class="p-newsListItemHaveThumb__categoryIcon__Plain">商品</li>
                </ul>
              </div>
              <h3 class="c-heading -level5"><span class="u-fontExtended">old item</span></h3>
            </div>
          </a>
        </div>
      </div>
    </body></html>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    responses = {
        "https://www.itoen.co.jp/news/release/": DummyResponse(page_one),
        "https://www.itoen.co.jp/news/release/page/2/": DummyResponse(page_two),
    }
    parser.client.get = lambda url: responses[str(url)]  # type: ignore[method-assign]

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["茶殻をアップサイクルした新素材を開発"]
    assert str(items[0].url) == "https://www.itoen.co.jp/news/article/85340/"
    assert items[0].published_at == datetime.fromisoformat("2026-04-08T00:00:00+09:00")
    assert items[0].summary == "サステナビリティ | subtitle"
