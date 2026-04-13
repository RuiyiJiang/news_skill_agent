from __future__ import annotations

from types import SimpleNamespace

from bs4 import BeautifulSoup

from app.crawlers.custom_parsers import FoodBevHomepageParser


def test_foodbev_extract_detail_date_skips_placeholder_jsonld() -> None:
    parser = FoodBevHomepageParser(
        SimpleNamespace(
            request_timeout_seconds=15.0,
            max_items_per_source=10,
            include_items_without_parsed_date=True,
        )
    )
    html = """
    <html>
      <head>
        <meta property="article:published_time" content="2026-04-06T17:00:00+00:00" />
        <script type="application/ld+json">
          {"@type":"BlogPosting","datePublished":" Date Published ","dateModified":" Date Last Modified "}
        </script>
      </head>
      <body></body>
    </html>
    """
    soup = BeautifulSoup(html, "lxml")

    assert parser._extract_detail_date(soup, html) == "2026-04-06T17:00:00+00:00"


def test_foodbev_generated_summary_is_prefixed() -> None:
    parser = FoodBevHomepageParser(
        SimpleNamespace(
            request_timeout_seconds=15.0,
            max_items_per_source=10,
            include_items_without_parsed_date=True,
        )
    )
    html = """
    <html>
      <body>
        <article>
          <p>第一段正文，用来生成程序摘要。</p>
          <p>第二段正文，补充说明。</p>
        </article>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "lxml")

    summary = parser._generate_summary_from_detail(soup)

    assert summary.startswith("【程序生成摘要】")
    assert "第一段正文" in summary
    assert "第二段正文" in summary
