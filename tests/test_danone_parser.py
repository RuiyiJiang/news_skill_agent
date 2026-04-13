from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import DanonePressReleasesParser
from app.models import SourceConfig


def test_danone_parser_reads_algolia_results_from_page_config(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = DanonePressReleasesParser(settings)
    source = SourceConfig(
        name="达能",
        base_url="https://www.danone.com",
        list_urls=["https://www.danone.com/newsroom/press-releases.html"],
        parser_type="custom_danone_press_releases",
        timezone="Europe/Paris",
        max_items=10,
        window_days=10,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+02:00")

    list_html = """
    <html><body>
      <div
        class="instant-search-comp"
        data-searchjson='{"instantSearch":{"queryKey":"test-query-key","appId":"EH0TYTZBJR","indices":[{"indexName":"prod_DANONERENEW_news_en","filterExpression":"category.titles:\\"Press release\\"","hitsPerPage":[{"value":"20"}]}]}}'>
      </div>
    </body></html>
    """

    search_results = {
        "hits": [
            {
                "title": "Danone celebrates 200 years of evian® and opens a new chapter",
                "detailspage": "https://www.danone.com/newsroom/press-releases/evian-200-years.html",
                "date": 1775146500000,
                "subject": {"titles": ["Corporate news"]},
            },
            {
                "title": "Old Danone item",
                "detailspage": "https://www.danone.com/newsroom/press-releases/old-item.html",
                "date": 1771569000000,
                "subject": {"titles": ["Corporate news"]},
            },
        ],
        "nbPages": 1,
    }

    monkeypatch.setattr(parser, "_fetch_text", lambda url: list_html)
    monkeypatch.setattr(parser, "_search_page", lambda **kwargs: search_results)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "Danone celebrates 200 years of evian® and opens a new chapter"
    ]
    assert str(items[0].url) == "https://www.danone.com/newsroom/press-releases/evian-200-years.html"
    assert items[0].published_at == datetime.fromisoformat("2026-04-02T18:15:00+02:00")
    assert items[0].summary == "Corporate news"


def test_danone_parser_supports_relative_detail_links_and_external_links(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = DanonePressReleasesParser(settings)
    source = SourceConfig(
        name="达能科研",
        base_url="https://www.danoneresearch.com",
        list_urls=["https://www.danoneresearch.com/science-based/newsroom/press-releases/"],
        parser_type="custom_danone_press_releases",
        timezone="Europe/Paris",
        max_items=10,
        window_days=10,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+02:00")

    list_html = """
    <html><body>
      <div
        class="instant-search-comp"
        data-searchjson='{"instantSearch":{"queryKey":"test-research-query-key","appId":"EH0TYTZBJR","indices":[{"indexName":"prod_DANONERESEARCH_news_en","hitsPerPage":[{"value":"20"}]}]}}'>
      </div>
    </body></html>
    """

    search_results = {
        "hits": [
            {
                "title": "Inside the small intestine: exploring the luminal microbiome with a novel swallowable device",
                "detailspage": "/content/corp/global/research/global/en/science-based/newsroom/publications/inside-the-small-intestine--exploring-the-luminal-microbiome-wit",
                "date": 1774944000000,
                "subject": {"titles": ["Publications", "Gut Health", "Gut Microbiome"]},
            },
            {
                "title": "Fallback external link item",
                "externallink": ["https://www.danoneresearch.com/newsroom/fallback-item.html"],
                "date": 1774944000000,
                "subject": {"titles": ["Publications"]},
            },
        ],
        "nbPages": 1,
    }

    monkeypatch.setattr(parser, "_fetch_text", lambda url: list_html)
    monkeypatch.setattr(parser, "_search_page", lambda **kwargs: search_results)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "Inside the small intestine: exploring the luminal microbiome with a novel swallowable device",
        "Fallback external link item",
    ]
    assert str(items[0].url) == (
        "https://www.danoneresearch.com/content/corp/global/research/global/en/"
        "science-based/newsroom/publications/inside-the-small-intestine--exploring-the-luminal-microbiome-wit"
    )
    assert items[0].summary == "Gut Health / Gut Microbiome"
    assert str(items[1].url) == "https://www.danoneresearch.com/newsroom/fallback-item.html"


def test_danone_parser_does_not_force_main_site_filter_on_research_index(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = DanonePressReleasesParser(settings)
    source = SourceConfig(
        name="达能科研",
        base_url="https://www.danoneresearch.com",
        list_urls=["https://www.danoneresearch.com/science-based/newsroom/press-releases/"],
        parser_type="custom_danone_press_releases",
        timezone="Europe/Paris",
        max_items=10,
        window_days=10,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+02:00")

    list_html = """
    <html><body>
      <div
        class="instant-search-comp"
        data-searchjson='{"instantSearch":{"queryKey":"test-research-query-key","appId":"EH0TYTZBJR","indices":[{"indexName":"prod_DANONERESEARCH_news_en","hitsPerPage":[{"value":"20"}]}]}}'>
      </div>
    </body></html>
    """

    captured = {}

    def fake_search_page(**kwargs):
        captured.update(kwargs)
        return {
            "hits": [
                {
                    "title": "Inside the small intestine: exploring the luminal microbiome with a novel swallowable device",
                    "detailspage": "/content/corp/global/research/global/en/science-based/newsroom/publications/inside-the-small-intestine--exploring-the-luminal-microbiome-wit",
                    "date": 1774944000000,
                    "subject": {"titles": ["Publications", "Gut Health", "Gut Microbiome"]},
                }
            ],
            "nbPages": 1,
        }

    monkeypatch.setattr(parser, "_fetch_text", lambda url: list_html)
    monkeypatch.setattr(parser, "_search_page", fake_search_page)

    items = parser.fetch_recent(source, now)

    assert captured["filter_expression"] == ""
    assert [item.title for item in items] == [
        "Inside the small intestine: exploring the luminal microbiome with a novel swallowable device"
    ]
