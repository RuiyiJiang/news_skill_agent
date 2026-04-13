from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import LotteChilsungNewsParser
from app.models import SourceConfig


def test_lotte_chilsung_news_parser_reads_cards_and_paginates() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = LotteChilsungNewsParser(settings)
    source = SourceConfig(
        name="乐天七星饮料",
        base_url="https://company.lottechilsung.co.kr",
        list_urls=["https://company.lottechilsung.co.kr/kor/company/news/list.do"],
        parser_type="custom_lotte_chilsung_news",
        timezone="Asia/Seoul",
        max_items=10,
        window_days=10,
    )
    now = datetime.fromisoformat("2026-04-10T10:00:00+09:00")

    first_page = """
    <html><body>
      <div class="listWrap">
        <a href="./view.do?detailsKey=1615" class="list">
          <div class="img"><img alt="광고 이미지" /></div>
          <div class="txtArea"><p class="tit">첫 번째 뉴스</p></div>
          <p class="listDate">2026-04-09</p>
        </a>
      </div>
      <div class="btnArea">
        <a href="javascript:" class="roundBtn wht" data-page="1">더보기</a>
      </div>
    </body></html>
    """

    second_page = """
    <html><body>
      <div class="listWrap">
        <a href="./view.do?detailsKey=1614" class="list">
          <div class="img"><img alt="두 번째 이미지" /></div>
          <div class="txtArea"><p class="tit">두 번째 뉴스</p></div>
          <p class="listDate">2026-04-08</p>
        </a>
      </div>
    </body></html>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str) -> DummyResponse:
        if "pageIndex=2" in url:
            return DummyResponse(second_page)
        return DummyResponse(first_page)

    parser.client.get = fake_get  # type: ignore[method-assign]

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["첫 번째 뉴스", "두 번째 뉴스"]
    assert str(items[0].url) == "https://company.lottechilsung.co.kr/kor/company/news/view.do?detailsKey=1615"
    assert items[0].published_at == datetime.fromisoformat("2026-04-09T00:00:00+09:00")
    assert items[0].summary == "광고 이미지"
