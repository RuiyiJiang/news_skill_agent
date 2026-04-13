from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import AjinomotoNewsroomParser
from app.models import SourceConfig


def test_ajinomoto_newsroom_parser_reads_list_and_follows_pager(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = AjinomotoNewsroomParser(settings)
    source = SourceConfig(
        name="味之素",
        base_url="https://news.ajinomoto.co.jp",
        list_urls=["https://news.ajinomoto.co.jp"],
        parser_type="custom_ajinomoto_newsroom",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=15,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    page_one = """
    <html><body>
    <div id="newsListContainer">
      <dl class="news-list">
        <dt class="news-list__meta">
          <div class="news-list__date">2026年04月02日</div>
          <div class="news-list__ctg"><a href="/?ctg=press">プレスリリース</a></div>
          <ul class="news-list__genre">
            <li class="corporate"><a href="/?genre=corporate">コーポレート</a></li>
          </ul>
        </dt>
        <dd class="news-list__title">
          <a href="https://news.ajinomoto.co.jp/2026/04/2026_04_02.pdf" target="_blank" class="pdf">自己株式の取得状況に関するお知らせ[適時開示]</a>
        </dd>
        <dt class="news-list__meta">
          <div class="news-list__date">2026年03月30日</div>
          <div class="news-list__ctg"><a href="/?ctg=press">プレスリリース</a></div>
          <ul class="news-list__genre">
            <li class="corporate"><a href="/?genre=corporate">コーポレート</a></li>
            <li class="research"><a href="/?genre=research">研究・技術</a></li>
          </ul>
        </dt>
        <dd class="news-list__title">
          <a href="https://news.ajinomoto.co.jp/2026/03/20260330.html" target="_self">味の素㈱、パーム油フリー新製法を開発</a>
        </dd>
      </dl>
    </div>
    <ul class="news-list__pager">
      <li class="pager--next"><a href="/?page=2&kwd=">NEXT</a></li>
    </ul>
    </body></html>
    """

    page_two = """
    <html><body>
    <div id="newsListContainer">
      <dl class="news-list">
        <dt class="news-list__meta">
          <div class="news-list__date">2026年03月25日</div>
          <div class="news-list__ctg"><a href="/?ctg=press">プレスリリース</a></div>
          <ul class="news-list__genre">
            <li class="foods"><a href="/?genre=foods">食品</a></li>
          </ul>
        </dt>
        <dd class="news-list__title">
          <a href="https://news.ajinomoto.co.jp/2026/03/20260325.html" target="_self">old item</a>
        </dd>
      </dl>
    </div>
    </body></html>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str) -> DummyResponse:
        if "page=2" in url:
            return DummyResponse(page_two)
        return DummyResponse(page_one)

    monkeypatch.setattr(parser.client, "get", fake_get)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "自己株式の取得状況に関するお知らせ[適時開示]",
        "味の素㈱、パーム油フリー新製法を開発",
    ]
    assert str(items[0].url) == "https://news.ajinomoto.co.jp/2026/04/2026_04_02.pdf"
    assert items[0].published_at == datetime.fromisoformat("2026-04-02T00:00:00+09:00")
    assert items[0].summary == "プレスリリース | コーポレート"
    assert items[1].summary == "プレスリリース | コーポレート / 研究・技術"
