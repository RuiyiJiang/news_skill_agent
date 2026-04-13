from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import PepsiCoChinaMediaCenterParser, PepsiCoPressReleaseParser
from app.models import SourceConfig


def test_pepsico_parser_stops_when_batch_is_out_of_window(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = PepsiCoPressReleaseParser(settings)
    source = SourceConfig(
        name="PepsiCo",
        base_url="https://www.pepsico.com",
        list_urls=["https://www.pepsico.com/newsroom/press-releases-category"],
        parser_type="custom_pepsico_press_releases",
        timezone="America/New_York",
        max_items=10,
    )
    now = datetime.fromisoformat("2026-04-07T09:00:00-04:00")
    page_calls: list[str | None] = []

    monkeypatch.setattr(parser, "_extract_press_release_tags", lambda list_url: ["tag-1"])

    def fake_fetch_articles(*, tags, language, end_cursor, page_size):
        page_calls.append(end_cursor)
        if end_cursor is None:
            return {
                "results": [
                    {
                        "Href": "/newsroom/press-releases/2026/in-window",
                        "Title": {"value": "In window"},
                        "Tag": {"value": "Press Releases"},
                    },
                    {
                        "Href": "/newsroom/press-releases/2026/older-than-window",
                        "Title": {"value": "Older than window"},
                        "Tag": {"value": "Press Releases"},
                    },
                ],
                "pageInfo": {"hasNext": True, "endCursor": "cursor-2"},
            }
        raise AssertionError("parser should stop before requesting the next page")

    monkeypatch.setattr(parser, "_fetch_articles", fake_fetch_articles)

    def fake_fetch_detail_metadata(article_url: str) -> tuple[str | None, str]:
        if article_url.endswith("/in-window"):
            return "2026-04-07T08:00:00-04:00", "current"
        if article_url.endswith("/older-than-window"):
            return "2026-04-05T08:00:00-04:00", "old"
        raise AssertionError(f"unexpected url: {article_url}")

    monkeypatch.setattr(parser, "_fetch_detail_metadata", fake_fetch_detail_metadata)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["In window"]
    assert page_calls == [None]


def test_pepsico_generated_summary_is_prefixed() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = PepsiCoPressReleaseParser(settings)

    summary = parser._extract_summary(
        "<p>First paragraph from detail page.</p><p>Second paragraph from detail page.</p>"
    )

    assert summary.startswith("【程序生成摘要】")
    assert "First paragraph" in summary
    assert "Second paragraph" in summary


def test_pepsico_china_parser_maps_categories_and_stops_when_page_is_out_of_window(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = PepsiCoChinaMediaCenterParser(settings)
    source = SourceConfig(
        name="百事公司大中华区",
        base_url="https://www.pepsico.com.cn",
        list_urls=[
            "https://www.pepsico.com.cn/media-center/company-news",
            "https://www.pepsico.com.cn/media-center/brand-news",
        ],
        parser_type="custom_pepsico_china_media_center",
        timezone="Asia/Shanghai",
        max_items=10,
    )
    now = datetime.fromisoformat("2026-04-08T15:00:00+08:00")
    page_calls: list[tuple[int, int]] = []

    def fake_fetch_news_page(*, category_id: int, page: int, page_size: int):
        page_calls.append((category_id, page))
        if category_id == 1:
            assert page == 1
            return {
                "data": {
                    "last_page": 2,
                    "data": [
                        {
                            "id": 201,
                            "title": "公司新闻",
                            "brief": "",
                            "out_link": "",
                            "add_time": "2026-04-08 09:30:00",
                        },
                        {
                            "id": 202,
                            "title": "过窗旧稿",
                            "brief": "",
                            "out_link": "",
                            "add_time": "2026-04-05 09:30:00",
                        },
                    ],
                }
            }
        if category_id == 2:
            assert page == 1
            return {
                "data": {
                    "last_page": 1,
                    "data": [
                        {
                            "id": 301,
                            "title": "品牌新闻",
                            "brief": "来自接口原始摘要",
                            "out_link": "https://example.com/brand-story",
                            "add_time": "2026-04-08 08:30:00",
                        }
                    ],
                }
            }
        raise AssertionError(f"unexpected category/page: {(category_id, page)}")

    monkeypatch.setattr(parser, "_fetch_news_page", fake_fetch_news_page)

    def fake_fetch_detail(article_id: int) -> dict[str, object]:
        if article_id == 201:
            return {
                "content": "<p>First paragraph from detail.</p><p>Second paragraph from detail.</p>",
                "add_time": "2026-04-08 09:30:00",
            }
        if article_id == 202:
            return {
                "content": "<p>Older paragraph.</p>",
                "add_time": "2026-04-05 09:30:00",
            }
        raise AssertionError(f"unexpected detail id: {article_id}")

    monkeypatch.setattr(parser, "_fetch_detail", fake_fetch_detail)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["公司新闻", "品牌新闻"]
    assert str(items[0].url) == "https://www.pepsico.com.cn/media-center/company-news/201"
    assert items[0].summary.startswith("【程序生成摘要】")
    assert "First paragraph from detail." in items[0].summary
    assert str(items[1].url) == "https://example.com/brand-story"
    assert items[1].summary == "来自接口原始摘要"
    assert page_calls == [(1, 1), (2, 1)]
