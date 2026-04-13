from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import CninfoAnnouncementParser
from app.models import SourceConfig


def make_parser() -> CninfoAnnouncementParser:
    return CninfoAnnouncementParser(
        SimpleNamespace(
            request_timeout_seconds=15.0,
            max_items_per_source=20,
            include_items_without_parsed_date=True,
        )
    )


def test_cninfo_parser_fetches_recent_announcements_and_builds_detail_url(monkeypatch) -> None:
    parser = make_parser()
    source = SourceConfig(
        name="巨潮资讯",
        base_url="https://www.cninfo.com.cn",
        list_urls=["https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"],
        parser_type="custom_cninfo_announcements",
        timezone="Asia/Shanghai",
        max_items=10,
        query_params={
            "column": "szse",
            "pageSize": "2",
            "maxPages": "3",
        },
    )
    now = datetime.fromisoformat("2026-04-08T09:00:00+08:00")
    page_calls: list[dict[str, str]] = []

    def fake_fetch_page(payload: dict[str, str], *, referer: str) -> dict[str, object]:
        page_calls.append(payload)
        if payload["pageNum"] != "1":
            raise AssertionError("parser should stop before requesting the next page")
        return {
            "announcements": [
                {
                    "announcementId": "1225083636",
                    "announcementTitle": "<em>关于签订募集资金三方监管协议的公告</em>",
                    "announcementTime": 1775607730000,
                    "orgId": "9900013508",
                    "secCode": "002452",
                    "secName": "长高电新",
                },
                {
                    "announcementId": "1224000000",
                    "announcementTitle": "过期公告",
                    "announcementTime": 1775347200000,
                    "orgId": "9900013508",
                    "secCode": "002452",
                    "secName": "长高电新",
                },
            ],
            "totalpages": 9,
        }

    monkeypatch.setattr(parser, "_fetch_page", fake_fetch_page)

    items = parser.fetch_recent(source, now)

    assert len(items) == 1
    assert items[0].title == "关于签订募集资金三方监管协议的公告"
    assert items[0].summary == "证券简称：长高电新；证券代码：002452"
    assert (
        str(items[0].url)
        == "https://www.cninfo.com.cn/new/disclosure/detail?stockCode=002452&announcementId=1225083636&announcementTime=2026-04-08&orgId=9900013508"
    )
    assert page_calls == [
        {
            "column": "szse",
            "tabName": "fulltext",
            "plate": "",
            "stock": "",
            "searchkey": "",
            "secid": "",
            "category": "",
            "trade": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
            "seDate": "2026-04-07~2026-04-08",
            "pageNum": "1",
            "pageSize": "2",
        }
    ]


def test_cninfo_parser_respects_query_param_overrides(monkeypatch) -> None:
    parser = make_parser()
    source = SourceConfig(
        name="巨潮资讯",
        base_url="https://www.cninfo.com.cn",
        list_urls=["https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"],
        parser_type="custom_cninfo_announcements",
        timezone="Asia/Shanghai",
        max_items=10,
        query_params={
            "column": "fund",
            "tabName": "fulltext",
            "plate": "fund",
            "searchkey": "食品",
            "pageSize": "5",
            "maxPages": "1",
        },
    )
    now = datetime.fromisoformat("2026-04-08T09:00:00+08:00")
    page_calls: list[dict[str, str]] = []

    def fake_fetch_page(payload: dict[str, str], *, referer: str) -> dict[str, object]:
        page_calls.append(payload)
        return {"announcements": [], "totalpages": 0}

    monkeypatch.setattr(parser, "_fetch_page", fake_fetch_page)

    items = parser.fetch_recent(source, now)

    assert items == []
    assert page_calls == [
        {
            "column": "fund",
            "tabName": "fulltext",
            "plate": "fund",
            "stock": "",
            "searchkey": "食品",
            "secid": "",
            "category": "",
            "trade": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
            "seDate": "2026-04-07~2026-04-08",
            "pageNum": "1",
            "pageSize": "5",
        }
    ]
