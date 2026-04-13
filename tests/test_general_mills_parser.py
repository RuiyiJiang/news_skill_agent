from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import GeneralMillsPressReleaseParser
from app.models import SourceConfig


def test_general_mills_parser_reads_featured_and_detail_items(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = GeneralMillsPressReleaseParser(settings)
    source = SourceConfig(
        name="通用磨坊",
        base_url="https://www.generalmills.com",
        list_urls=["https://www.generalmills.com/news/press-releases"],
        parser_type="custom_general_mills_press_releases",
        timezone="America/Chicago",
        max_items=10,
        window_days=4,
    )
    now = datetime.fromisoformat("2025-05-10T10:00:00-05:00")

    list_html = """
    <html><body>
      <div class="featured-story-hero">
        <div class="hero-content-inner">
          <div class="field-publishdate">May 08, 2025</div>
          <div class="field-cardtitle">Three Indulgent Flavors, One Big Launch: Pillsbury Introduces BIG COOKIES Cookie Dough</div>
          <div class="field-cardsummary"><p>Cookie dough summary.</p></div>
          <a class="cta-link" href="/news/press-releases/three-indulgent-flavors-one-big-launch-pillsbury-introduces-big-cookies-cookie-dough">Continue Reading</a>
        </div>
      </div>
    </body></html>
    """

    detail_html = """
    <html><head>
      <meta name="description" content="Detail summary." />
      <meta property="og:title" content="Answering Overwhelming Demand from Fans, Gushers Finally Drops First-Ever All Blue Pack" />
    </head><body>
      <div class="article-headline row">
        <div class="article-content col">
          <div class="field-category"><div class="field-publishdate">May 07, 2025</div></div>
          <h1 class="field-pageheading field-title">Answering Overwhelming Demand from Fans, Gushers Finally Drops First-Ever All Blue Pack</h1>
          <div class="field-cardsummary"><p>Blue pack summary.</p></div>
        </div>
      </div>
    </body></html>
    """

    monkeypatch.setattr(
        parser,
        "_fetch_result_entries",
        lambda limit: [
            {
                "url": "https://www.generalmills.com/news/press-releases/answering-overwhelming-demand-from-fans-gushers-finally-drops-first-ever-all-blue-pack",
                "title": "",
                "summary": "",
                "raw_date": "",
                "has_metadata": False,
            },
            {
                "url": "https://www.generalmills.com/news/press-releases/old-item",
                "title": "",
                "summary": "",
                "raw_date": "",
                "has_metadata": False,
            },
        ],
    )

    def fake_fetch_text(url: str) -> str:
        if url == "https://www.generalmills.com/news/press-releases":
            return list_html
        if "gushers-finally-drops-first-ever-all-blue-pack" in url:
            return detail_html
        if url.endswith("/old-item"):
            return """
            <html><body>
              <div class="article-headline row">
                <div class="article-content col">
                  <div class="field-category"><div class="field-publishdate">April 01, 2025</div></div>
                  <h1 class="field-pageheading field-title">Old item</h1>
                </div>
              </div>
            </body></html>
            """
        raise AssertionError(f"unexpected fetch url: {url}")

    monkeypatch.setattr(parser, "_fetch_text", fake_fetch_text)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "Three Indulgent Flavors, One Big Launch: Pillsbury Introduces BIG COOKIES Cookie Dough",
        "Answering Overwhelming Demand from Fans, Gushers Finally Drops First-Ever All Blue Pack",
    ]
    assert str(items[0].url) == (
        "https://www.generalmills.com/news/press-releases/"
        "three-indulgent-flavors-one-big-launch-pillsbury-introduces-big-cookies-cookie-dough"
    )
    assert items[0].summary == "Cookie dough summary."
    assert items[1].summary == "Blue pack summary."


def test_general_mills_parser_prefers_rendered_search_results_from_html(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = GeneralMillsPressReleaseParser(settings)
    source = SourceConfig(
        name="通用磨坊",
        base_url="https://www.generalmills.com",
        list_urls=["https://www.generalmills.com/news/press-releases"],
        parser_type="custom_general_mills_press_releases",
        timezone="America/Chicago",
        max_items=10,
        window_days=40,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00-05:00")

    list_html = """
    <html><body>
      <div class="featured-story-hero">
        <div class="hero-content-inner">
          <div class="field-publishdate">May 08, 2025</div>
          <div class="field-cardtitle">Old featured item</div>
          <div class="field-cardsummary"><p>Old summary.</p></div>
          <a class="cta-link" href="/news/press-releases/old-featured-item">Continue Reading</a>
        </div>
      </div>
      <div class="search-results content-cards row three-col">
        <div class="component cl-card press-release-card">
          <div class="component-content">
            <a class="card-coverLink" href="/news/press-releases/lucky-charms-and-trix-bring-bold-flavors-to-the-breakfast-table">Lucky Charms and Trix</a>
            <div class="card-content">
              <div class="card-overlayDate">
                <div class="card-overlayDate-month">March</div>
                <div class="card-overlayDate-day">26</div>
                <div class="card-overlayDate-year">2026</div>
              </div>
              <div class="card-category field-title">Food</div>
              <div class="card-title field-cardtitle">Lucky Charms™ and Trix™ Bring Bold Flavors to the Breakfast Table with New Cereal from Natural Sources</div>
              <div class="card-description field-cardsummary"><p>General Mills introduces new cereals.</p></div>
            </div>
          </div>
        </div>
        <div class="component cl-card press-release-card">
          <div class="component-content">
            <a class="card-coverLink" href="/news/press-releases/general-mills-reports-fiscal-2026-third-quarter-results-and-reaffirms-full-year-outlook">Fiscal 2026 Q3</a>
            <div class="card-content">
              <div class="card-overlayDate">
                <div class="card-overlayDate-month">March</div>
                <div class="card-overlayDate-day">18</div>
                <div class="card-overlayDate-year">2026</div>
              </div>
              <div class="card-category field-title">Business</div>
              <div class="card-title field-cardtitle">General Mills Reports Fiscal 2026 Third-quarter Results and Reaffirms Full-year Outlook</div>
              <div class="card-description field-cardsummary">General Mills, Inc. today reported results.</div>
            </div>
          </div>
        </div>
      </div>
    </body></html>
    """

    monkeypatch.setattr(parser, "_fetch_text", lambda url: list_html)
    monkeypatch.setattr(parser, "_fetch_result_entries", lambda limit: [])

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "Lucky Charms™ and Trix™ Bring Bold Flavors to the Breakfast Table with New Cereal from Natural Sources",
        "General Mills Reports Fiscal 2026 Third-quarter Results and Reaffirms Full-year Outlook",
    ]
    assert str(items[0].url) == (
        "https://www.generalmills.com/news/press-releases/"
        "lucky-charms-and-trix-bring-bold-flavors-to-the-breakfast-table"
    )
    assert items[0].published_at == datetime.fromisoformat("2026-03-26T00:00:00-05:00")


def test_general_mills_parser_uses_search_result_metadata_without_detail_fetch(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = GeneralMillsPressReleaseParser(settings)
    source = SourceConfig(
        name="通用磨坊",
        base_url="https://www.generalmills.com",
        list_urls=["https://www.generalmills.com/news/press-releases"],
        parser_type="custom_general_mills_press_releases",
        timezone="America/Chicago",
        max_items=10,
        window_days=40,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00-05:00")

    monkeypatch.setattr(parser, "_fetch_text", lambda url: "<html><body></body></html>")
    monkeypatch.setattr(
        parser,
        "_fetch_result_entries",
        lambda limit: [
            {
                "url": "https://www.generalmills.com/news/press-releases/general-mills-reports-fiscal-2026-third-quarter-results-and-reaffirms-full-year-outlook",
                "title": "General Mills Reports Fiscal 2026 Third-quarter Results and Reaffirms Full-year Outlook",
                "summary": "General Mills today reported results for the third quarter ended February 22, 2026.",
                "raw_date": "March 18, 2026",
                "has_metadata": True,
            }
        ],
    )
    monkeypatch.setattr(
        parser,
        "_parse_detail",
        lambda article_url, source, now: (_ for _ in ()).throw(AssertionError("detail fetch should not run")),
    )

    items = parser.fetch_recent(source, now)

    assert len(items) == 1
    assert items[0].title == "General Mills Reports Fiscal 2026 Third-quarter Results and Reaffirms Full-year Outlook"
    assert items[0].summary == "General Mills today reported results for the third quarter ended February 22, 2026."
    assert items[0].published_at == datetime.fromisoformat("2026-03-18T00:00:00-05:00")
