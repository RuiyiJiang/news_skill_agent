from __future__ import annotations

from types import SimpleNamespace

from app.crawlers.custom_parsers import ThePaperExpressNewsParser


def make_parser() -> ThePaperExpressNewsParser:
    return ThePaperExpressNewsParser(
        SimpleNamespace(
            request_timeout_seconds=15.0,
            max_items_per_source=10,
            include_items_without_parsed_date=True,
        )
    )


def test_thepaper_extract_summary_is_prefixed() -> None:
    parser = make_parser()
    entry = {
        "content": "<p>第一段快讯正文。</p>",
        "contentList": [{"content": "第二段补充说明。"}],
    }

    summary = parser._extract_summary(entry)

    assert summary.startswith("【程序生成摘要】")
    assert "第一段快讯正文" in summary


def test_thepaper_build_datetime_text() -> None:
    parser = make_parser()

    assert parser._build_datetime_text({"pubDate": "2026-04-07", "pubTime": "17:07"}) == "2026-04-07 17:07"
