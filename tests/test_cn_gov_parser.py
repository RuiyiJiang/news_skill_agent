from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import ChineseGovernmentPagedListParser
from app.models import SourceConfig


def test_cn_gov_parser_uses_browser_fetch_and_collects_paginated_items(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=20,
        include_items_without_parsed_date=True,
    )
    parser = ChineseGovernmentPagedListParser(settings)
    source = SourceConfig(
        name="国家市场监督管理总局食品生产经营安全监督管理司政策文件",
        base_url="https://www.samr.gov.cn",
        list_urls=["https://www.samr.gov.cn/spscs/zcfg/index.html"],
        parser_type="custom_cn_gov_paged_list",
        timezone="Asia/Shanghai",
        max_items=20,
        window_days=2,
        query_params={
            "fetch_mode": "browser",
            "browser_wait_selector": "li.content-3-left-text, .pagination",
            "max_pages": "4",
        },
    )
    now = datetime.fromisoformat("2026-04-10T12:00:00+08:00")

    page_1_html = """
    <html><body>
      <div class="list-content">
        <ul>
          <li class="content-3-left-text">
            <a href="/spscs/zcfg/art/2026/art_recent_1.html">两天内第一条政策</a>
            <span>2026-04-10</span>
          </li>
          <li class="content-3-left-text">
            <a href="/spscs/zcfg/art/2026/art_recent_2.html">两天内第二条政策</a>
            <span>2026-04-09</span>
          </li>
        </ul>
      </div>
      <div class="pagination">1 2 下一页</div>
    </body></html>
    """
    page_2_html = """
    <html><body>
      <div class="list-content">
        <ul>
          <li class="content-3-left-text">
            <a href="/spscs/zcfg/art/2026/art_old_1.html">窗口外旧政策</a>
            <span>2026-04-07</span>
          </li>
        </ul>
      </div>
      <div class="pagination">1 2 下一页</div>
    </body></html>
    """

    seen_urls: list[str] = []

    def fake_browser_fetch(url: str, **kwargs) -> str:
        seen_urls.append(url)
        if url.endswith("/spscs/zcfg/index.html"):
            return page_1_html
        if url.endswith("/spscs/zcfg/index_2.html"):
            return page_2_html
        return "<html><body></body></html>"

    monkeypatch.setattr("app.crawlers.custom_parsers.fetch_html_with_playwright", fake_browser_fetch)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["两天内第一条政策", "两天内第二条政策"]
    assert seen_urls == [
        "https://www.samr.gov.cn/spscs/zcfg/index.html",
        "https://www.samr.gov.cn/spscs/zcfg/index_2.html",
    ]


def test_cn_gov_parser_browser_wait_selector_defaults_when_missing() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=20,
        include_items_without_parsed_date=True,
    )
    parser = ChineseGovernmentPagedListParser(settings)
    source = SourceConfig(
        name="国家卫健委法律法规",
        base_url="https://www.nhc.gov.cn",
        list_urls=["https://www.nhc.gov.cn/wjw/flfg/list.shtml"],
        parser_type="custom_cn_gov_paged_list",
        timezone="Asia/Shanghai",
        query_params={"fetch_mode": "browser"},
    )

    assert parser._should_use_browser_fetch(source) is True
    assert parser._browser_wait_selector(source) == "li.content-3-left-text, .pagination, body"
    assert parser._max_pages(source) == 8


def test_cn_gov_parser_ignores_footer_links_when_list_nodes_exist() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=20,
        include_items_without_parsed_date=True,
    )
    parser = ChineseGovernmentPagedListParser(settings)
    source = SourceConfig(
        name="市场监管总局",
        base_url="https://www.samr.gov.cn",
        list_urls=["https://www.samr.gov.cn/tssps/zcwj/index.html"],
        parser_type="custom_cn_gov_paged_list",
        timezone="Asia/Shanghai",
        window_days=2,
    )
    now = datetime.fromisoformat("2026-04-10T12:00:00+08:00")
    soup_html = """
    <html><body>
      <div class="footer"><a href="https://beian.miit.gov.cn/">京ICP备18022388号-1</a></div>
      <div class="list-content">
        <ul>
          <li class="content-3-left-text">
            <a href="/tssps/zcwj/art/2026/art_real.html">真实政策文件</a>
            <span>2026-04-10</span>
          </li>
        </ul>
      </div>
    </body></html>
    """

    from bs4 import BeautifulSoup

    items = parser._extract_page_items(BeautifulSoup(soup_html, "lxml"), source, now, "https://www.samr.gov.cn/tssps/zcwj/index.html")

    assert len(items) == 1
    assert items[0].title == "真实政策文件"
    assert str(items[0].url) == "https://www.samr.gov.cn/tssps/zcwj/art/2026/art_real.html"
