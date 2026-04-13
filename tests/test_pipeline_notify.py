from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from app.models import CrawlResult
from app.models import PipelineResult
from app.pipeline import _build_empty_source_rows, _notify


def make_settings() -> SimpleNamespace:
    return SimpleNamespace(
        enable_feishu=True,
        feishu_webhook_url="https://example.com/webhook",
        feishu_secret="",
        request_timeout_seconds=5.0,
        feishu_app_id="",
        feishu_app_secret="",
        feishu_receive_id_type="",
        feishu_receive_id="",
    )


def make_result(*, failed_sources: int, total_sources: int) -> PipelineResult:
    return PipelineResult(
        started_at=datetime.fromisoformat("2026-04-01T09:00:00+08:00"),
        finished_at=datetime.fromisoformat("2026-04-01T09:01:00+08:00"),
        total_sources=total_sources,
        successful_sources=total_sources - failed_sources,
        failed_sources=failed_sources,
        total_items=5,
        raw_total_items=12,
        filtered_total_items=5,
        unresolved_date_items=2,
        output_file=Path("/tmp/news_report_filtered.xlsx"),
        raw_output_file=Path("/tmp/news_report_all.xlsx"),
        filtered_output_file=Path("/tmp/news_report_filtered.xlsx"),
        failed_source_names=["Example"] if failed_sources else [],
    )


def test_notify_swallow_error_summary_failure(monkeypatch) -> None:
    class DummyNotifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def send_error_summary(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.pipeline.FeishuNotifier", DummyNotifier)
    _notify(make_settings(), make_result(failed_sources=2, total_sources=2))


def test_notify_swallow_summary_and_file_delivery_failures(monkeypatch) -> None:
    class DummyNotifier:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def send_text_summary(self, result):
            raise RuntimeError("summary failed")

        def send_report_files(self, result):
            raise RuntimeError("file failed")

    monkeypatch.setattr("app.pipeline.FeishuNotifier", DummyNotifier)
    _notify(make_settings(), make_result(failed_sources=1, total_sources=2))


def test_build_empty_source_rows_adds_status_row_for_successful_empty_source() -> None:
    rows = _build_empty_source_rows(
        [
            CrawlResult(
                source="联合利华",
                source_url="https://www.unilever.com/news/news-search/",
                items=[],
                success=True,
            ),
            CrawlResult(
                source="百事公司",
                source_url="https://www.pepsico.com/newsroom/press-releases-category",
                items=[],
                success=False,
                error_message="boom",
            ),
        ]
    )

    assert rows == [
        {
            "Source": "联合利华",
            "Title": "本次未抓到符合时间窗口的资讯",
            "Summary": "抓取成功，但当前时间窗口内没有可入表内容。",
            "Published At": "",
            "URL": "https://www.unilever.com/news/news-search/",
            "标签": "状态记录",
        }
    ]
