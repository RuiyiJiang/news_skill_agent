from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import AsahiNewsroomParser
from app.models import SourceConfig


def test_asahi_newsroom_parser_reads_latest_news_cards(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = AsahiNewsroomParser(settings)
    source = SourceConfig(
        name="朝日集团",
        base_url="https://www.asahigroup-holdings.com",
        list_urls=["https://www.asahigroup-holdings.com/newsroom/"],
        parser_type="custom_asahi_newsroom",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=4,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    html = """
    <html><body>
      <section id="01" class="newsroom-list">
        <ul class="rt_bn_news_list_is_not_0">
          <li class="mod-newsList03 rt_bn_news_list">
            <a href="https://www.asahiinryo.co.jp/company/newsrelease/2026/pick_0408.html" class="mod-newsList03-a rt_cf_n_href_newsroom" target="_blank">
              <div class="mod-newsList03-txt">
                <p class="mod-newsList03-txt-inner">
                  <span class="__tit rt_cf_n_title">『ひと搾りレモンのラッシー&amp;カルピス』4月21日期間限定発売</span>
                </p>
                <span class="cf_n_tags"><span class="mod-label rt_cf_n_tags_business">アサヒ飲料</span></span>
                <div class="mod-newsList03-inner">
                  <time class="__date rt_cf_n_date" datetime="2026-04-08">2026.04.08</time>
                  <ul class="mod-newsList03-tag">
                    <li class="cf_n_tags"><span class="__tag rt_cf_n_tags_category">商品・サービス</span></li>
                  </ul>
                </div>
              </div>
            </a>
          </li>
          <li class="mod-newsList03 rt_bn_news_list">
            <a href="https://www.asahibeer.co.jp/news/2026/0406.html" class="mod-newsList03-a rt_cf_n_href_newsroom" target="_blank">
              <div class="mod-newsList03-txt">
                <p class="mod-newsList03-txt-inner">
                  <span class="__tit rt_cf_n_title">「アサヒGINON」オリジナル 阪神タイガース応援ドリンク「トラピカルサワー」</span>
                </p>
                <span class="cf_n_tags"><span class="mod-label rt_cf_n_tags_business">アサヒビール</span></span>
                <div class="mod-newsList03-inner">
                  <time class="__date rt_cf_n_date" datetime="2026-04-06">2026.04.06</time>
                  <ul class="mod-newsList03-tag">
                    <li class="cf_n_tags"><span class="__tag rt_cf_n_tags_category">商品・サービス</span></li>
                    <li class="cf_n_tags"><span class="__tag rt_cf_n_tags_category">プロモーション</span></li>
                  </ul>
                </div>
              </div>
            </a>
          </li>
          <li class="mod-newsList03 rt_bn_news_list">
            <a href="https://www.asahibeer.co.jp/news/2026/0401.html" class="mod-newsList03-a rt_cf_n_href_newsroom" target="_blank">
              <div class="mod-newsList03-txt">
                <p class="mod-newsList03-txt-inner">
                  <span class="__tit rt_cf_n_title">old item</span>
                </p>
                <div class="mod-newsList03-inner">
                  <time class="__date rt_cf_n_date" datetime="2026-04-01">2026.04.01</time>
                </div>
              </div>
            </a>
          </li>
        </ul>
      </section>
    </body></html>
    """

    monkeypatch.setattr(parser.client, "get", lambda url: SimpleNamespace(text=html, raise_for_status=lambda: None))

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "『ひと搾りレモンのラッシー&カルピス』4月21日期間限定発売",
        "「アサヒGINON」オリジナル 阪神タイガース応援ドリンク「トラピカルサワー」",
    ]
    assert str(items[0].url) == "https://www.asahiinryo.co.jp/company/newsrelease/2026/pick_0408.html"
    assert items[0].published_at == datetime.fromisoformat("2026-04-08T00:00:00+09:00")
    assert items[0].summary == "アサヒ飲料 | 商品・サービス"
    assert items[1].summary == "アサヒビール | 商品・サービス / プロモーション"
