from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import AsahiRDReportParser
from app.models import SourceConfig


def test_asahi_rd_report_parser_reads_cards_and_follows_pagination(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = AsahiRDReportParser(settings)
    source = SourceConfig(
        name="朝日研发报告",
        base_url="https://www.asahigroup-holdings.com",
        list_urls=["https://www.asahigroup-holdings.com/rd/report/"],
        parser_type="custom_asahi_rd_report",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=1200,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    page_one = """
    <html><body>
      <h2 class="newsroom-result-head"><span class="__num rt_count">22</span>件の検索結果</h2>
      <ul class="pure-g newsroom-result-list">
        <li class="pure-u-1-3 pure-u-tab-1 newsroom-result-list-item rt_bn_news_list">
          <a href="/rd/report/detail/report-0022.html" class="mod-boxLink03 rt_cf_n_href_contents_report">
            <div class="mod-boxLink03-txt">
              <p class="__tit"><span class="rt_cf_n_title">乳酸菌CP3365株が歯周パラメータの悪化を抑制することを発見</span></p>
              <div class="mod-boxLink03-inner">
                <time class="__date rt_cf_n_date" datetime="2024-07-26">2024.07.26</time>
                <ul class="mod-boxLink03-tag">
                  <li><span>健康</span></li>
                  <li><span>微生物素材</span></li>
                </ul>
              </div>
            </div>
          </a>
        </li>
      </ul>
      <div class="mod-paginate">
        <a href="/rd/report/?rt_bn_news_list_skip=6" class="mod-paginate-next rt_bn_news_list_page-next">next</a>
      </div>
    </body></html>
    """

    page_two = """
    <html><body>
      <ul class="pure-g newsroom-result-list">
        <li class="pure-u-1-3 pure-u-tab-1 newsroom-result-list-item rt_bn_news_list">
          <a href="/rd/report/detail/report-0021.html" class="mod-boxLink03 rt_cf_n_href_contents_report">
            <div class="mod-boxLink03-txt">
              <p class="__tit"><span class="rt_cf_n_title">ヒト腸内細菌Bacteroides uniformisが持久運動パフォーマンスの向上に寄与することを発見</span></p>
              <div class="mod-boxLink03-inner">
                <time class="__date rt_cf_n_date" datetime="2023-06-15">2023.06.15</time>
                <ul class="mod-boxLink03-tag">
                  <li><span>健康</span></li>
                </ul>
              </div>
            </div>
          </a>
        </li>
      </ul>
    </body></html>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str) -> DummyResponse:
        if "skip=6" in url:
            return DummyResponse(page_two)
        return DummyResponse(page_one)

    monkeypatch.setattr(parser.client, "get", fake_get)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "乳酸菌CP3365株が歯周パラメータの悪化を抑制することを発見",
        "ヒト腸内細菌Bacteroides uniformisが持久運動パフォーマンスの向上に寄与することを発見",
    ]
    assert str(items[0].url) == "https://www.asahigroup-holdings.com/rd/report/detail/report-0022.html"
    assert items[0].published_at == datetime.fromisoformat("2024-07-26T00:00:00+09:00")
    assert items[0].summary == "健康 / 微生物素材"
    assert str(items[1].url) == "https://www.asahigroup-holdings.com/rd/report/detail/report-0021.html"
