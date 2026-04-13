from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import MeijiRDTopicsParser
from app.models import SourceConfig


def test_meiji_rd_topics_parser_reads_simple_rows() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = MeijiRDTopicsParser(settings)
    source = SourceConfig(
        name="明治研发动态",
        base_url="https://www.meiji.co.jp",
        list_urls=["https://www.meiji.co.jp/quality/r_d/topics/"],
        parser_type="custom_meiji_rd_topics",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=60,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    html = """
    <html><body>
      <div class="js-heading-accordion-body">
        <div class="l-list-container">
          <ul class="l-list-line" data-dir="col">
            <li>
              <p><time class="m-txt-time">2026/02/26</time></p>
              <p><a href="/corporate/pressrelease/2026/02_15/index.html" class="m-txtLink">カカオ豆の種皮にセラミドが高濃度含まれていることを発見</a></p>
            </li>
            <li>
              <p><time class="m-txt-time">2025/12/01</time></p>
              <p><a href="/corporate/pressrelease/2025/12_01/index.html" class="m-txtLink">old item</a></p>
            </li>
          </ul>
        </div>
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
        "カカオ豆の種皮にセラミドが高濃度含まれていることを発見",
    ]
    assert str(items[0].url) == "https://www.meiji.co.jp/corporate/pressrelease/2026/02_15/index.html"
    assert items[0].published_at == datetime.fromisoformat("2026-02-26T00:00:00+09:00")
    assert items[0].summary == "研究所からのお知らせ"
