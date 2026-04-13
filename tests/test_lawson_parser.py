from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import LawsonNewsReleaseParser
from app.models import SourceConfig


def test_lawson_news_release_parser_reads_table_rows() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = LawsonNewsReleaseParser(settings)
    source = SourceConfig(
        name="罗森",
        base_url="https://www.lawson.co.jp",
        list_urls=["https://www.lawson.co.jp/company/news/index.html"],
        parser_type="custom_lawson_news",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=10,
    )
    now = datetime.fromisoformat("2026-04-10T10:00:00+09:00")

    html = """
    <html><body>
      <table class="newsTable">
        <tbody>
          <tr>
            <th><span>2026年4月6日</span></th>
            <td class="icon_cate">
              <img src="/company/news/img/icon_recommend.gif" alt="商品">
              <img src="/company/news/img/icon_local.gif" alt="地域の取組み">
            </td>
            <td><span><a href="/company/news/detail/1520759_2504.html">発売から40周年を迎える「からあげクン」</a></span></td>
          </tr>
          <tr>
            <th><span>2026年3月20日</span></th>
            <td class="icon_cate">
              <img src="/company/news/img/icon_management.gif" alt="経営・人事">
            </td>
            <td><span><a href="/company/news/detail/1519897_2504.html">old item</a></span></td>
          </tr>
        </tbody>
      </table>
    </body></html>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    parser.client.get = lambda url: DummyResponse(html)  # type: ignore[method-assign]

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["発売から40周年を迎える「からあげクン」"]
    assert str(items[0].url) == "https://www.lawson.co.jp/company/news/detail/1520759_2504.html"
    assert items[0].published_at == datetime.fromisoformat("2026-04-06T00:00:00+09:00")
    assert items[0].summary == "商品 / 地域の取組み"
