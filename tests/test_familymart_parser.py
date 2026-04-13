from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import FamilyMartNewsReleaseParser
from app.models import SourceConfig


def test_familymart_news_release_parser_reads_monthly_articles() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = FamilyMartNewsReleaseParser(settings)
    source = SourceConfig(
        name="全家便利店",
        base_url="https://www.family.co.jp",
        list_urls=["https://www.family.co.jp/company/news_releases/2026.html"],
        parser_type="custom_familymart_news_releases",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=10,
    )
    now = datetime.fromisoformat("2026-04-10T10:00:00+09:00")

    html = """
    <html><body>
      <section class="ly-wrp-section ly-wrp-list-month" id="apr">
        <div id="news" class="ly-mod-list-area ly-mod-list js-filter-content">
          <article class="row js-filter-blk js-filter-all js-filter-goods">
            <a href="https://www.family.co.jp/company/news_releases/2026/20260409_01.html">
              <img src="/content/dam/family/company/news_releases/2026/0409CW_S.jpg" class="thumb_row" alt="logo"/>
            </a>
            <div class="ly-ttl-area_row">
              <div class="ly-txt-date">2026年4月9日</div>
              <div class="ly-icn-goods">商品</div>
              <p class="ly-txt-tit">
                <a href="https://www.family.co.jp/company/news_releases/2026/20260409_01.html">母の日ギフトにもおすすめなタオルハンカチ2種を発売</a>
              </p>
            </div>
          </article>
          <article class="row js-filter-blk js-filter-all js-filter-company">
            <a href="https://www.family.co.jp/company/news_releases/2026/20260301_01.html">
              <img src="/content/dam/family/common_pic/fm-logo.svg" class="thumb_row" alt="logo"/>
            </a>
            <div class="ly-ttl-area_row">
              <div class="ly-txt-date">2026年3月1日</div>
              <div class="ly-icn-company">企業情報</div>
              <p class="ly-txt-tit">
                <a href="https://www.family.co.jp/company/news_releases/2026/20260301_01.html">old item</a>
              </p>
            </div>
          </article>
        </div>
      </section>
    </body></html>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    parser.client.get = lambda url: DummyResponse(html)  # type: ignore[method-assign]

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["母の日ギフトにもおすすめなタオルハンカチ2種を発売"]
    assert str(items[0].url) == "https://www.family.co.jp/company/news_releases/2026/20260409_01.html"
    assert items[0].published_at == datetime.fromisoformat("2026-04-09T00:00:00+09:00")
    assert items[0].summary == "商品"
