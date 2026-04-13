from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import SuntoryNewsListParser
from app.models import SourceConfig


def test_suntory_parser_reads_grouped_articles_and_pagination(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = SuntoryNewsListParser(settings)
    source = SourceConfig(
        name="三得利",
        base_url="https://www.suntory.com",
        list_urls=["https://www.suntory.com/news/?ke=mn"],
        parser_type="custom_suntory_news_list",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=60,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    first_page_html = """
    <html><body>
      <div class="listGroup">
        <h3>March 18, 2026</h3>
        <article>
          <div class="artiBody">
            <ul class="tag"><li><a href="/news/list/category01/">Management / Finance</a></li></ul>
            <p class="read">Announcement summary.</p>
            <a href="/news/article/2026/14983-2.html" class="title">Financial Results for FY2025</a>
          </div>
        </article>
      </div>
      <div class="pageNav">
        <nav>
          <a href="/news/index_2.html">2</a>
        </nav>
      </div>
    </body></html>
    """

    second_page_html = """
    <html><body>
      <div class="listGroup">
        <h3>February 12, 2026</h3>
        <article>
          <div class="artiBody">
            <ul class="tag"><li><a href="/news/list/category06/">HR</a></li></ul>
            <a href="/news/article/2026/14982E.html" class="title">Suntory Holdings Establishes Suntory Sports</a>
          </div>
        </article>
      </div>
    </body></html>
    """

    payloads = {
        "https://www.suntory.com/news/?ke=mn": first_page_html,
        "https://www.suntory.com/news/index_2.html": second_page_html,
    }

    monkeypatch.setattr(parser, "_fetch_text", lambda url: payloads[url])

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "Financial Results for FY2025",
        "Suntory Holdings Establishes Suntory Sports",
    ]
    assert str(items[0].url) == "https://www.suntory.com/news/article/2026/14983-2.html"
    assert items[0].published_at == datetime.fromisoformat("2026-03-18T00:00:00+09:00")
    assert items[0].summary == "Management / Finance | Announcement summary."
    assert str(items[1].url) == "https://www.suntory.com/news/article/2026/14982E.html"
    assert items[1].published_at == datetime.fromisoformat("2026-02-12T00:00:00+09:00")
    assert items[1].summary == "HR"


def test_suntory_parser_filters_old_pages(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = SuntoryNewsListParser(settings)
    source = SourceConfig(
        name="三得利",
        base_url="https://www.suntory.com",
        list_urls=["https://www.suntory.com/news/?ke=mn"],
        parser_type="custom_suntory_news_list",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=30,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    html = """
    <html><body>
      <div class="listGroup">
        <h3>January 01, 2026</h3>
        <article>
          <div class="artiBody">
            <a href="/news/article/2026/old.html" class="title">Old article</a>
          </div>
        </article>
      </div>
      <div class="pageNav">
        <nav>
          <a href="/news/index_2.html">2</a>
        </nav>
      </div>
    </body></html>
    """

    called = []

    def fake_fetch(url: str) -> str:
        called.append(url)
        return html

    monkeypatch.setattr(parser, "_fetch_text", fake_fetch)

    items = parser.fetch_recent(source, now)

    assert items == []
    assert called == [
        "https://www.suntory.com/news/?ke=mn",
        "https://www.suntory.com/news/index_2.html",
    ]


def test_suntory_parser_reads_japanese_card_articles() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = SuntoryNewsListParser(settings)
    source = SourceConfig(
        name="三得利日本",
        base_url="https://www.suntory.co.jp",
        list_urls=["https://www.suntory.co.jp/news/?fromid=top"],
        parser_type="custom_suntory_news_list",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=30,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    html = """
    <html><body>
      <div id="search_body">
        <article class="article01">
          <h4 class="article01_heading">
            <a href="/news/article/2026/15023.html">サントリー大阪工場 スピリッツ・リキュール工房ツアー開始</a>
          </h4>
          <div class="article01_text"><p>“日本に、洋酒文化を。世界に、日本のものづくりを”</p></div>
          <dl class="article01_meta">
            <div class="category">
              <dd><a href="/news/search/?category10=1">その他</a></dd>
            </div>
            <div class="date">
              <dd><time datetime="2026-4-8">2026年4月8日</time></dd>
            </div>
          </dl>
        </article>
        <article class="article01">
          <h4 class="article01_heading">
            <a href="/news/article/2026/15019.html">「東京クラフト〈フルーティエール〉」数量限定新発売</a>
          </h4>
          <div class="article01_text"><p>やさしく広がる麦のうまみ、フルーティな香りが特長</p></div>
          <dl class="article01_meta">
            <div class="category">
              <dd><a href="/news/search/?category04=1">商品</a></dd>
            </div>
            <div class="date">
              <dd><time datetime="2026-4-7">2026年4月7日</time></dd>
            </div>
          </dl>
        </article>
      </div>
    </body></html>
    """

    parser._fetch_text = lambda url: html  # type: ignore[method-assign]

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "サントリー大阪工場 スピリッツ・リキュール工房ツアー開始",
        "「東京クラフト〈フルーティエール〉」数量限定新発売",
    ]
    assert items[0].published_at == datetime.fromisoformat("2026-04-08T00:00:00+09:00")
    assert str(items[0].url) == "https://www.suntory.co.jp/news/article/2026/15023.html"
    assert items[0].summary == "その他 | “日本に、洋酒文化を。世界に、日本のものづくりを”"


def test_suntory_parser_uses_browser_directly_when_configured(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = SuntoryNewsListParser(settings)
    source = SourceConfig(
        name="三得利日本",
        base_url="https://www.suntory.co.jp",
        list_urls=["https://www.suntory.co.jp/news/?fromid=top"],
        parser_type="custom_suntory_news_list",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=30,
        query_params={
            "fetch_mode": "browser",
            "browser_wait_selector": "#search_body article.article01",
        },
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    real_html = """
    <html><body>
      <div id="search_body">
        <article class="article01">
          <h4 class="article01_heading">
            <a href="/news/article/2026/15023.html">サントリー大阪工場 スピリッツ・リキュール工房ツアー開始</a>
          </h4>
          <dl class="article01_meta">
            <div class="category"><dd><a href="/news/search/?category10=1">その他</a></dd></div>
            <div class="date"><dd><time datetime="2026-4-8">2026年4月8日</time></dd></div>
          </dl>
        </article>
      </div>
    </body></html>
    """

    monkeypatch.setattr(
        parser,
        "_fetch_text",
        lambda url: (_ for _ in ()).throw(AssertionError("_fetch_text should not be called in browser mode")),
    )
    monkeypatch.setattr(parser, "_fetch_text_with_browser", lambda url, source: real_html)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "サントリー大阪工場 スピリッツ・リキュール工房ツアー開始"
    ]
