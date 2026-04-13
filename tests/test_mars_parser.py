from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import MarsNewsAndStoriesParser
from app.models import SourceConfig


def test_mars_parser_fetches_recent_items_and_expands_truncated_titles(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        max_detail_fetch_per_source=5,
        include_items_without_parsed_date=True,
    )
    parser = MarsNewsAndStoriesParser(settings)
    source = SourceConfig(
        name="玛氏",
        base_url="https://www.mars.com",
        list_urls=["https://www.mars.com/news-and-stories/all-news-and-stories"],
        parser_type="custom_mars_news_and_stories",
        timezone="UTC",
        max_items=10,
        window_days=3,
    )
    now = datetime.fromisoformat("2026-03-26T12:00:00+00:00")
    requested_urls: list[str] = []

    page_one_html = """
    <html><body>
      <ul class="pagination js-pager__items">
        <li><a href="?page=1">2</a></li>
      </ul>
      <article>
        <span class="coh-inline-element">Press Release</span>
        <span class="coh-ce-7765ac98">March 25, 2026</span>
        <a href="/news-and-stories/press-releases-statements/ben-rice" target="_self">
          <div class="field--name-field-heading-hero"><p>Professional baseball player Ben Rice teams up with Ben's Original rice...</p></div>
        </a>
        <div class="coh-inline-element coh-ce-b7a97813"><p>First baseman and iconic rice brand unite to help fight childhood hunger.</p></div>
      </article>
      <article>
        <span class="coh-inline-element">Story</span>
        <span class="coh-ce-7765ac98">March 25, 2026</span>
        <a href="/news-and-stories/articles/tomato-talks" target="_self">
          <div class="field--name-field-heading-hero"><p>Tomato Talks: Behind-the-scenes at our King's Lynn UK factory</p></div>
        </a>
        <div class="coh-inline-element coh-ce-b7a97813"><p>Inside the King's Lynn facility.</p></div>
      </article>
      <article>
        <span class="coh-inline-element">Story</span>
        <span class="coh-ce-7765ac98">March 25, 2026</span>
        <a href="/news-and-stories/articles/tomato-talks" target="_self">
          <div class="field--name-field-heading-hero"><p>Tomato Talks: Behind-the-scenes at our King's Lynn UK factory</p></div>
        </a>
        <div class="coh-inline-element coh-ce-b7a97813"><p>Duplicate mobile card.</p></div>
      </article>
    </body></html>
    """
    page_two_html = """
    <html><body>
      <article>
        <span class="coh-inline-element">Press Release</span>
        <span class="coh-ce-7765ac98">March 24, 2026</span>
        <a href="/news-and-stories/press-releases-statements/canada-invests" target="_self">
          <div class="field--name-field-heading-hero"><p>Mars Canada invests in Ontario operations</p></div>
        </a>
        <div class="coh-inline-element coh-ce-b7a97813"><p>Expansion announcement.</p></div>
      </article>
    </body></html>
    """
    detail_html = """
    <html><head>
      <meta property="og:title" content="From the Baseball Diamond to the Kitchen Table: Professional baseball player Ben Rice teams up with Ben's Original rice for a grand slam partnership | Mars" />
    </head><body></body></html>
    """

    html_by_url = {
        "https://www.mars.com/news-and-stories/all-news-and-stories": page_one_html,
        "https://www.mars.com/news-and-stories/all-news-and-stories?page=1": page_two_html,
        "https://www.mars.com/news-and-stories/press-releases-statements/ben-rice": detail_html,
    }

    def fake_fetch_html(url: str) -> str:
        requested_urls.append(url)
        if url in html_by_url:
            return html_by_url[url]
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(parser, "_fetch_html", fake_fetch_html)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "From the Baseball Diamond to the Kitchen Table: Professional baseball player Ben Rice teams up with Ben's Original rice for a grand slam partnership",
        "Tomato Talks: Behind-the-scenes at our King's Lynn UK factory",
        "Mars Canada invests in Ontario operations",
    ]
    assert items[0].summary == "Press Release: First baseman and iconic rice brand unite to help fight childhood hunger."
    assert items[1].summary == "Story: Inside the King's Lynn facility."
    assert items[2].raw_date_text == "March 24, 2026"
    assert str(items[2].url) == "https://www.mars.com/news-and-stories/press-releases-statements/canada-invests"
    assert requested_urls == [
        "https://www.mars.com/news-and-stories/all-news-and-stories",
        "https://www.mars.com/news-and-stories/all-news-and-stories",
        "https://www.mars.com/news-and-stories/press-releases-statements/ben-rice",
        "https://www.mars.com/news-and-stories/all-news-and-stories?page=1",
    ]


def test_mars_parser_builds_paged_urls() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        max_detail_fetch_per_source=5,
        include_items_without_parsed_date=True,
    )
    parser = MarsNewsAndStoriesParser(settings)

    assert parser._build_page_url("https://www.mars.com/news-and-stories/all-news-and-stories", 0) == (
        "https://www.mars.com/news-and-stories/all-news-and-stories"
    )
    assert parser._build_page_url("https://www.mars.com/news-and-stories/all-news-and-stories", 1) == (
        "https://www.mars.com/news-and-stories/all-news-and-stories?page=1"
    )
