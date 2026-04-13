from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import AsahiBeerYearNewsParser
from app.models import SourceConfig


def test_asahi_beer_year_news_parser_reads_monthly_cards(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = AsahiBeerYearNewsParser(settings)
    source = SourceConfig(
        name="朝日啤酒",
        base_url="https://www.asahibeer.co.jp",
        list_urls=["https://www.asahibeer.co.jp/news/2026/"],
        parser_type="custom_asahi_beer_year_news",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=4,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    html = """
    <html><body>
      <section class="news_monthly">
        <h2 class="news_monthly_title">2026年 4月</h2>
        <div class="news_monthly_list">
          <section class="news_monthly_item" data-category="event">
            <a href="/news/2026/0408.html">
              <div class="news_monthly_item_details">
                <div class="news_monthly_item_details_header">
                  <time class="news_monthly_item_date">2026年4月8日</time>
                  <div class="news_monthly_item_category">
                    <span class="icon icon-event"></span>
                    <span>イベント・キャンペーン・CM</span>
                  </div>
                </div>
                <div class="news_monthly_item_text">
                  <p>『アサヒスーパードライ 生ジョッキ缶』 人気バンド「back number」とタイアップキャンペーン実施</p>
                </div>
              </div>
            </a>
          </section>
          <section class="news_monthly_item" data-category="product">
            <a href="/news/2026/0406.html">
              <div class="news_monthly_item_details">
                <div class="news_monthly_item_details_header">
                  <time class="news_monthly_item_date">2026年4月6日</time>
                  <div class="news_monthly_item_category">
                    <span class="icon icon-product"></span>
                    <span>商品</span>
                  </div>
                </div>
                <div class="news_monthly_item_text">
                  <p>「アサヒGINON」オリジナル 阪神タイガース応援ドリンク「トラピカルサワー」</p>
                </div>
              </div>
            </a>
          </section>
          <section class="news_monthly_item" data-category="product">
            <a href="/news/2026/0401.html">
              <div class="news_monthly_item_details">
                <div class="news_monthly_item_details_header">
                  <time class="news_monthly_item_date">2026年4月1日</time>
                  <div class="news_monthly_item_category">
                    <span class="icon icon-product"></span>
                    <span>商品</span>
                  </div>
                </div>
                <div class="news_monthly_item_text">
                  <p>old item</p>
                </div>
              </div>
            </a>
          </section>
        </div>
      </section>
    </body></html>
    """

    monkeypatch.setattr(
        parser.client,
        "get",
        lambda url: SimpleNamespace(content=html.encode("shift_jis", errors="ignore"), raise_for_status=lambda: None),
    )

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "『アサヒスーパードライ 生ジョッキ缶』 人気バンド「back number」とタイアップキャンペーン実施",
        "「アサヒGINON」オリジナル 阪神タイガース応援ドリンク「トラピカルサワー」",
    ]
    assert str(items[0].url) == "https://www.asahibeer.co.jp/news/2026/0408.html"
    assert items[0].published_at == datetime.fromisoformat("2026-04-08T00:00:00+09:00")
    assert items[0].summary == "イベント・キャンペーン・CM"
