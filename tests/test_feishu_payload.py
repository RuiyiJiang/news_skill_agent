from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.feishu import (
    FeishuNotifier,
    _get_feishu_file_type,
    _get_mime_type,
    build_error_payload,
    build_success_payload,
)
from app.models import PipelineResult


def test_build_success_payload() -> None:
    result = PipelineResult(
        started_at=datetime.fromisoformat("2026-04-01T09:00:00+08:00"),
        finished_at=datetime.fromisoformat("2026-04-01T09:01:00+08:00"),
        total_sources=3,
        successful_sources=2,
        failed_sources=1,
        total_items=5,
        raw_total_items=12,
        filtered_total_items=5,
        unresolved_date_items=2,
        output_file=Path("/tmp/news_report_filtered.xlsx"),
        raw_output_file=Path("/tmp/news_report_all.xlsx"),
        filtered_output_file=Path("/tmp/news_report_filtered.xlsx"),
        failed_source_names=["Example"],
        selected_groups=["新媒体"],
    )
    payload = build_success_payload(result)
    text = payload["content"]["text"]
    assert payload["msg_type"] == "text"
    assert "全量新闻条数: 12" in text
    assert "任务分组: 新媒体" in text
    assert "筛选后新闻条数: 5" in text
    assert "发布时间未解析条数: 2" in text
    assert "全量 Excel: news_report_all.xlsx" in text
    assert "筛选后 Excel: news_report_filtered.xlsx" in text


def test_build_error_payload() -> None:
    payload = build_error_payload(
        "network error",
        datetime.fromisoformat("2026-04-01T09:00:00+08:00"),
        Path("/tmp/news_report.xlsx"),
        ["国家政府"],
    )
    text = payload["content"]["text"]
    assert payload["msg_type"] == "text"
    assert "资讯抓取任务失败" in text
    assert "任务分组: 国家政府" in text
    assert "network error" in text
    assert "/tmp/news_report.xlsx" in text


def test_feishu_file_delivery_requires_full_config() -> None:
    notifier = FeishuNotifier(
        webhook_url="https://example.com/webhook",
        app_id="cli_xxx",
        app_secret="",
        receive_id_type="chat_id",
        receive_id="oc_xxx",
    )
    assert notifier.can_send_files() is False

    notifier = FeishuNotifier(
        webhook_url="https://example.com/webhook",
        app_id="cli_xxx",
        app_secret="secret",
        receive_id_type="chat_id",
        receive_id="oc_xxx",
    )
    assert notifier.can_send_files() is True


def test_feishu_upload_type_and_mime_for_excel() -> None:
    file_path = Path("/tmp/news_report_filtered.xlsx")
    assert _get_feishu_file_type(file_path) == "xls"
    assert (
        _get_mime_type(file_path)
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_feishu_upload_type_defaults_to_stream() -> None:
    file_path = Path("/tmp/archive.bin")
    assert _get_feishu_file_type(file_path) == "stream"
    assert _get_mime_type(file_path) == "application/octet-stream"
