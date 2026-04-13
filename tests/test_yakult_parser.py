from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import YakultInformationParser
from app.models import SourceConfig


def test_yakult_information_parser_reads_cards_and_pager() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = YakultInformationParser(settings)
    source = SourceConfig(
        name="养乐多",
        base_url="https://www.yakult.co.jp",
        list_urls=["https://www.yakult.co.jp/information/"],
        parser_type="custom_yakult_information",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=10,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    page_one = """
    <html><body>
      <div class="information-contents-wrap">
        <a href="https://www.yakult.co.jp/company/news/article.php?num=1836" class="information-link" target="_blank">
          <div class="information-date">2026.04.01</div>
          <div class="information-text">母の日キャンペーンを実施</div>
        </a>
        <a href="article.php?num=1800" class="information-link">
          <div class="information-date">2026.03.20</div>
          <div class="information-text">old item</div>
        </a>
      </div>
      <div class="pager-wrap">
        <ul class="pagination">
          <li class="pager-next"><a href="/information/?p=2">next</a></li>
        </ul>
      </div>
    </body></html>
    """

    page_two = """
    <html><body>
      <div class="information-contents-wrap">
        <a href="/company/news/article.php?num=1700" class="information-link">
          <div class="information-date">2026.03.10</div>
          <div class="information-text">older item</div>
        </a>
      </div>
    </body></html>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    responses = {
        "https://www.yakult.co.jp/information/": DummyResponse(page_one),
        "https://www.yakult.co.jp/information/?p=2": DummyResponse(page_two),
    }
    parser.client.get = lambda url: responses[str(url)]  # type: ignore[method-assign]

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["母の日キャンペーンを実施"]
    assert str(items[0].url) == "https://www.yakult.co.jp/company/news/article.php?num=1836"
    assert items[0].published_at == datetime.fromisoformat("2026-04-01T00:00:00+09:00")
    assert items[0].summary == "お知らせ"
