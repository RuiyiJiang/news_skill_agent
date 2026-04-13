from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import UnileverNewsSearchParser
from app.models import SourceConfig


def test_unilever_parser_stops_when_oldest_card_is_out_of_window(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = UnileverNewsSearchParser(settings)
    source = SourceConfig(
        name="联合利华",
        base_url="https://www.unilever.com",
        list_urls=["https://www.unilever.com/news/news-search/"],
        parser_type="custom_unilever_news_search",
        timezone="Europe/London",
        max_items=10,
        window_days=3,
    )
    now = datetime.fromisoformat("2026-04-08T09:00:00+01:00")
    requested_urls: list[str] = []

    page_one_html = """
    <html><body>
      <a data-testid="ush-c-results-pagination-pager-link" href="/news/news-search/2/">2</a>
      <div data-testid="uol-c-collection-items">
        <div data-testid="ush-c-results-result">
          <article data-testid="uol-c-card">
            <div data-testid="uol-c-card-content">
              <header data-testid="uol-c-card-header">
                <h3 data-testid="uol-c-card-title">
                  <a data-testid="uol-c-card-title-link" href="/news/news-search/2026/in-window/">In window</a>
                </h3>
                <p class="uol-c-card__eyebrow"><time datetime="2026-04-08T00:00:00.000Z">8 April 2026</time></p>
              </header>
              <div data-testid="uol-c-card-body">First summary.</div>
            </div>
          </article>
        </div>
        <div data-testid="ush-c-results-result">
          <article data-testid="uol-c-card">
            <div data-testid="uol-c-card-content">
              <header data-testid="uol-c-card-header">
                <h3 data-testid="uol-c-card-title">
                  <a data-testid="uol-c-card-title-link" href="/news/news-search/2026/out-of-window/">Out of window</a>
                </h3>
                <p class="uol-c-card__eyebrow"><time datetime="2026-04-02T00:00:00.000Z">2 April 2026</time></p>
              </header>
              <div data-testid="uol-c-card-body">Older summary.</div>
            </div>
          </article>
        </div>
      </div>
    </body></html>
    """

    def fake_get(url: str):
        requested_urls.append(url)
        if url == "https://www.unilever.com/news/news-search/":
            return _FakeResponse(page_one_html)
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(parser.client, "get", fake_get)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["In window"]
    assert items[0].summary == "First summary."
    assert str(items[0].url) == "https://www.unilever.com/news/news-search/2026/in-window/"
    assert requested_urls == ["https://www.unilever.com/news/news-search/", "https://www.unilever.com/news/news-search/"]


def test_unilever_parser_builds_paged_urls() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = UnileverNewsSearchParser(settings)

    assert parser._build_page_url("https://www.unilever.com/news/news-search/", 1) == (
        "https://www.unilever.com/news/news-search/"
    )
    assert parser._build_page_url("https://www.unilever.com/news/news-search/", 2) == (
        "https://www.unilever.com/news/news-search/2/"
    )


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_unilever_parser_uses_browser_fallback_when_curl_is_blocked(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = UnileverNewsSearchParser(settings)
    source = SourceConfig(
        name="联合利华",
        base_url="https://www.unilever.com",
        list_urls=["https://www.unilever.com/news/news-search/"],
        parser_type="custom_unilever_news_search",
        timezone="Europe/London",
        max_items=10,
        window_days=3,
    )
    now = datetime.fromisoformat("2026-04-08T09:00:00+01:00")

    page_html = """
    <html><body>
      <article data-testid="uol-c-card">
        <a data-testid="uol-c-card-title-link" href="/news/news-search/2026/browser-fallback/">Browser fallback item</a>
        <time datetime="2026-04-08T00:00:00.000Z">8 April 2026</time>
        <div data-testid="uol-c-card-body">Recovered via browser.</div>
      </article>
    </body></html>
    """

    class Http403Response:
        text = ""

        def raise_for_status(self) -> None:
            import httpx

            request = httpx.Request("GET", "https://www.unilever.com/news/news-search/")
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError("403", request=request, response=response)

    monkeypatch.setattr(parser.client, "get", lambda url: Http403Response())

    import subprocess
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="<html><title>Access Denied</title>errors.edgesuite.net</html>", stderr=""),
    )

    called = {}

    def fake_browser_fetch(url: str, **kwargs):
        called["url"] = url
        called["kwargs"] = kwargs
        return page_html

    monkeypatch.setattr("app.crawlers.custom_parsers.fetch_html_with_playwright", fake_browser_fetch)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["Browser fallback item"]
    assert items[0].summary == "Recovered via browser."
    assert str(items[0].url) == "https://www.unilever.com/news/news-search/2026/browser-fallback/"
    assert called["url"] == "https://www.unilever.com/news/news-search/"
    assert called["kwargs"]["locale"] == "en-GB"


def test_unilever_parser_uses_browser_directly_when_configured(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = UnileverNewsSearchParser(settings)
    source = SourceConfig(
        name="联合利华",
        base_url="https://www.unilever.com",
        list_urls=["https://www.unilever.com/news/news-search/"],
        parser_type="custom_unilever_news_search",
        timezone="Europe/London",
        max_items=10,
        window_days=3,
        query_params={
            "fetch_mode": "browser",
            "browser_wait_selector": 'article[data-testid="uol-c-card"]',
        },
    )
    now = datetime.fromisoformat("2026-04-08T09:00:00+01:00")

    page_html = """
    <html><body>
      <article data-testid="uol-c-card">
        <a data-testid="uol-c-card-title-link" href="/news/news-search/2026/browser-direct/">Browser direct item</a>
        <time datetime="2026-04-08T00:00:00.000Z">8 April 2026</time>
        <div data-testid="uol-c-card-body">Recovered via browser mode.</div>
      </article>
    </body></html>
    """

    def fail_get(url: str):
        raise AssertionError("client.get should not be called in browser mode")

    monkeypatch.setattr(parser.client, "get", fail_get)

    called = {}

    def fake_browser_fetch(url: str, **kwargs):
        called["url"] = url
        called["kwargs"] = kwargs
        return page_html

    monkeypatch.setattr("app.crawlers.custom_parsers.fetch_html_with_playwright", fake_browser_fetch)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["Browser direct item"]
    assert called["url"] == "https://www.unilever.com/news/news-search/"
    assert called["kwargs"]["wait_selector"] == 'article[data-testid="uol-c-card"]'
