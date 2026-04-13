from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import MorinagaMilkReleaseParser
from app.models import SourceConfig


def test_morinaga_milk_release_parser_reads_list_and_pager() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = MorinagaMilkReleaseParser(settings)
    source = SourceConfig(
        name="森永乳业",
        base_url="https://www.morinagamilk.co.jp",
        list_urls=["https://www.morinagamilk.co.jp/release/"],
        parser_type="custom_morinaga_milk_release",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=4,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    page_one = """
    <html><body>
      <dl class="news-list">
        <dt>2026年04月09日 商品情報</dt>
        <dd><a href="/release/detail/260409_01.html">「Variche」リニューアル発売</a></dd>
        <dt>2026年04月06日 サステナビリティ</dt>
        <dd><a href="/release/detail/260406_01.html">食育実践優良法人に認定</a></dd>
      </dl>
      <div class="pagerWrapper">
        <ul class="pager">
          <li><a class="pager-link" href="/release/?page=2">次へ</a></li>
        </ul>
      </div>
    </body></html>
    """

    page_two = """
    <html><body>
      <dl class="news-list">
        <dt>2026年03月30日 商品情報</dt>
        <dd><a href="/release/detail/260330_01.html">old item</a></dd>
      </dl>
    </body></html>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    responses = {
        "https://www.morinagamilk.co.jp/release/": DummyResponse(page_one),
        "https://www.morinagamilk.co.jp/release/?page=2": DummyResponse(page_two),
    }
    parser.client.get = lambda url: responses[str(url)]  # type: ignore[method-assign]

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "「Variche」リニューアル発売",
        "食育実践優良法人に認定",
    ]
    assert str(items[0].url) == "https://www.morinagamilk.co.jp/release/detail/260409_01.html"
    assert items[0].published_at == datetime.fromisoformat("2026-04-09T00:00:00+09:00")
    assert items[0].summary == "商品情報"
