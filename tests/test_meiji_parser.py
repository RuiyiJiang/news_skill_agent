from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import MeijiPressReleaseParser
from app.models import SourceConfig


def test_meiji_pressrelease_parser_reads_cards() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = MeijiPressReleaseParser(settings)
    source = SourceConfig(
        name="明治",
        base_url="https://www.meiji.co.jp",
        list_urls=["https://www.meiji.co.jp/corporate/pressrelease/"],
        parser_type="custom_meiji_pressrelease",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=4,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    html = """
    <html><body>
      <div class="l-list-container">
        <ul class="l-list-line" data-dir="col">
          <li><a href="/corporate/pressrelease/2026/04_05/index.html" class="l-card">
            <div class="l-card-body">
              <ul class="l-list" data-dir="row">
                <li><time class="m-txt-time">2026/04/09</time></li>
                <li><span class="m-icon" data-color="pale-orange">プレスリリース</span></li>
              </ul>
              <p class="m-txtLink-block">明治と篠原倖太朗選手がサポート契約を締結</p>
            </div>
          </a></li>
          <li><a href="https://www.meiji.com/pdf/news/2026/260323_01.pdf" class="l-card" target="_blank">
            <div class="l-card-body">
              <ul class="l-list" data-dir="row">
                <li><time class="m-txt-time">2026/03/23</time></li>
                <li><span class="m-icon" data-color="pale-blue">お知らせ</span></li>
              </ul>
              <p class="m-txtLink-block">old item</p>
            </div>
          </a></li>
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

    assert [item.title for item in items] == [
        "明治と篠原倖太朗選手がサポート契約を締結",
    ]
    assert str(items[0].url) == "https://www.meiji.co.jp/corporate/pressrelease/2026/04_05/index.html"
    assert items[0].published_at == datetime.fromisoformat("2026-04-09T00:00:00+09:00")
    assert items[0].summary == "プレスリリース"
