from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import FerreroNewsParser
from app.models import SourceConfig


def test_ferrero_parser_reads_search_endpoint_hits() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = FerreroNewsParser(settings)
    source = SourceConfig(
        name="费列罗",
        base_url="https://www.ferrero.com",
        list_urls=["https://www.ferrero.com/int/en/news-stories/news"],
        parser_type="custom_ferrero_news",
        timezone="Europe/Rome",
        max_items=10,
        window_days=30,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+02:00")

    payload = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "url": ["/int/en/news-stories/news/ferrero-group-acquires-bold-snacks-a-leading-brazilian-protein-snack-company"],
                        "article_type": ["news"],
                        "date": [1773835200],
                        "created": [1773821907],
                        "field_title": ["ferrero group acquires bold snacks, a leading brazilian protein snack company"],
                        "search_result": [
                            """
                            <article class="search-result node-article">
                              <a class="search-result--content" href="/int/en/news-stories/news/ferrero-group-acquires-bold-snacks-a-leading-brazilian-protein-snack-company">
                                <div class="search-result--title">Ferrero Group acquires Bold Snacks, a leading Brazilian protein snack company</div>
                                <div class="search-result--text">Ferrero Group announced it has signed an agreement to acquire Bold Snacks, a leading Brazilian premium protein snack company.</div>
                              </a>
                            </article>
                            """
                        ],
                        "rendered_item": [
                            """
                            <article class="node-article node-article-teaser node-teaser">
                              <a class="node-article-teaser--content" href="/int/en/news-stories/news/ferrero-group-acquires-bold-snacks-a-leading-brazilian-protein-snack-company">
                                <div class="node-article-teaser--info">
                                  <div class="node-article-teaser--date date">18 Mar 2026</div>
                                  <h2 class="node-article-teaser--title title">FERRERO GROUP ACQUIRES BOLD SNACKS, A LEADING BRAZILIAN PROTEIN SNACK COMPANY</h2>
                                </div>
                              </a>
                            </article>
                            """
                        ],
                        "summary": [""],
                    }
                },
                {
                    "_source": {
                        "url": ["/int/en/news-stories/news/old-item"],
                        "article_type": ["news"],
                        "date": [1768608000],
                        "field_title": ["old item"],
                        "search_result": [
                            """
                            <article class="search-result node-article">
                              <a class="search-result--content" href="/int/en/news-stories/news/old-item">
                                <div class="search-result--title">Old item</div>
                                <div class="search-result--text">Old summary.</div>
                              </a>
                            </article>
                            """
                        ],
                    }
                },
            ]
        }
    }

    parser._fetch_hits = lambda limit: payload  # type: ignore[method-assign]

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "Ferrero Group acquires Bold Snacks, a leading Brazilian protein snack company"
    ]
    assert str(items[0].url) == (
        "https://www.ferrero.com/int/en/news-stories/news/"
        "ferrero-group-acquires-bold-snacks-a-leading-brazilian-protein-snack-company"
    )
    assert items[0].published_at == datetime.fromisoformat("2026-03-18T13:00:00+01:00")
    assert items[0].summary == (
        "Ferrero Group announced it has signed an agreement to acquire Bold Snacks, "
        "a leading Brazilian premium protein snack company."
    )
