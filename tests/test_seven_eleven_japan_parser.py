from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import SevenElevenJapanNewsReleaseParser
from app.models import SourceConfig


def test_seven_eleven_japan_news_release_parser_reads_grouped_rows() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = SevenElevenJapanNewsReleaseParser(settings)
    source = SourceConfig(
        name="7-Eleven日本",
        base_url="https://www.sej.co.jp",
        list_urls=["https://www.sej.co.jp/company/news_release/news/2026.html"],
        parser_type="custom_seven_eleven_japan_news_releases",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=10,
    )
    now = datetime.fromisoformat("2026-04-10T10:00:00+09:00")

    html = """
    <html><body>
      <div class="section">
        <h2 class="ttl-lv2">2026年 4月</h2>
        <ul class="list-news">
          <li>
            <p class="date">2026/04/09 <span class="label--company">企業情報</span></p>
            <p>
              <a href="/company/news_release/news/2026/202604091600.html" class="link-txt" rel="noreferrer noopener">
                役員の異動に関するお知らせ
              </a>
            </p>
          </li>
          <li>
            <p class="date">2026/03/20 <span class="label--products">商品</span></p>
            <p>
              <a href="/company/news_release/news/2026/old.html" class="link-txt">
                old item
              </a>
            </p>
          </li>
        </ul>
      </div>
    </body></html>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    parser.client.get = lambda url: DummyResponse(html)  # type: ignore[method-assign]

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["役員の異動に関するお知らせ"]
    assert str(items[0].url) == "https://www.sej.co.jp/company/news_release/news/2026/202604091600.html"
    assert items[0].published_at == datetime.fromisoformat("2026-04-09T00:00:00+09:00")
    assert items[0].summary == "企業情報"
