from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import FoodTalksNewsParser
from app.models import SourceConfig


def make_parser() -> FoodTalksNewsParser:
    return FoodTalksNewsParser(
        SimpleNamespace(
            request_timeout_seconds=15.0,
            max_items_per_source=10,
            include_items_without_parsed_date=True,
        )
    )


def test_foodtalks_news_parser_stops_when_batch_is_out_of_window(monkeypatch) -> None:
    parser = make_parser()
    source = SourceConfig(
        name="FoodTalks News",
        base_url="https://www.foodtalks.cn",
        list_urls=["https://www.foodtalks.cn/news"],
        parser_type="custom_foodtalks_news",
        timezone="Asia/Shanghai",
        max_items=10,
        query_params={
            "pageSize": "2",
            "maxPages": "3",
        },
    )
    now = datetime.fromisoformat("2026-04-08T09:00:00+08:00")
    page_calls: list[tuple[str, int, int, str, dict[str, str]]] = []

    monkeypatch.setattr(parser, "_warm_up_source", lambda list_url: None)

    def fake_fetch_page(*, api_path, page_num, page_size, language, extra_params, referer):
        page_calls.append((api_path, page_num, page_size, language, dict(extra_params)))
        if page_num != 1:
            raise AssertionError("parser should stop before requesting the next page")
        return {
            "data": {
                "records": [
                    {
                        "id": 61915,
                        "title": "750毫升大瓶装，盼盼首推山茶花龙井，占位300亿无糖茶",
                        "summary": "100%原叶萃取还原头道鲜活，750ml大容量聚焦水替场景",
                        "publishTime": "2026-04-08 09:00:00",
                    },
                    {
                        "id": 61900,
                        "title": "过期新闻",
                        "summary": "更早的一条旧新闻",
                        "publishTime": "2026-04-05 10:00:00",
                    },
                ],
                "pages": 9,
            }
        }

    monkeypatch.setattr(parser, "_fetch_page", fake_fetch_page)

    items = parser.fetch_recent(source, now)

    assert len(items) == 1
    assert items[0].title == "750毫升大瓶装，盼盼首推山茶花龙井，占位300亿无糖茶"
    assert str(items[0].url) == "https://www.foodtalks.cn/news/61915"
    assert items[0].summary == "100%原叶萃取还原头道鲜活，750ml大容量聚焦水替场景"
    assert page_calls == [
        ("/news/news/page", 1, 2, "ZH", {})
    ]


def test_foodtalks_news_parser_respects_api_path_override(monkeypatch) -> None:
    parser = make_parser()
    source = SourceConfig(
        name="FoodTalks News",
        base_url="https://www.foodtalks.cn",
        list_urls=["https://www.foodtalks.cn/news"],
        parser_type="custom_foodtalks_news",
        timezone="Asia/Shanghai",
        max_items=10,
        query_params={
            "api_path": "/news/news/hot/page",
            "language": "EN",
            "pageSize": "5",
            "maxPages": "1",
        },
    )
    now = datetime.fromisoformat("2026-04-08T09:00:00+08:00")
    page_calls: list[tuple[str, int, int, str, dict[str, str]]] = []

    monkeypatch.setattr(parser, "_warm_up_source", lambda list_url: None)

    def fake_fetch_page(*, api_path, page_num, page_size, language, extra_params, referer):
        page_calls.append((api_path, page_num, page_size, language, dict(extra_params)))
        return {"data": {"records": [], "pages": 0}}

    monkeypatch.setattr(parser, "_fetch_page", fake_fetch_page)

    items = parser.fetch_recent(source, now)

    assert items == []
    assert page_calls == [
        ("/news/news/hot/page", 1, 5, "EN", {})
    ]
