from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.models import PipelineResult


LOGGER = logging.getLogger(__name__)

FILE_TYPE_BY_SUFFIX = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".csv": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}

MIME_TYPE_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class FeishuPushError(RuntimeError):
    """Raised when Feishu push fails."""


class FeishuNotifier:
    AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    FILE_UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/files"
    MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

    def __init__(
        self,
        webhook_url: str,
        secret: str = "",
        timeout: float = 10.0,
        *,
        app_id: str = "",
        app_secret: str = "",
        receive_id_type: str = "",
        receive_id: str = "",
    ) -> None:
        self.webhook_url = webhook_url.strip()
        self.secret = secret
        self.timeout = timeout
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self.receive_id_type = receive_id_type.strip().lower()
        self.receive_id = receive_id.strip()

    def send_text_summary(self, result: PipelineResult) -> dict[str, Any] | None:
        if not self.webhook_url:
            LOGGER.info("Feishu webhook is empty, skipping message push.")
            return None

        payload = build_success_payload(result)
        return self._post(payload)

    def send_report_files(self, result: PipelineResult) -> list[dict[str, Any]]:
        if not self.can_send_files():
            LOGGER.info(
                "Feishu file delivery is not configured, skipping report file push. "
                "Configure FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_RECEIVE_ID_TYPE, FEISHU_RECEIVE_ID."
            )
            return []

        tenant_access_token = self._get_tenant_access_token()
        responses: list[dict[str, Any]] = []
        for report_file in (result.raw_output_file, result.filtered_output_file):
            file_key = self._upload_file(report_file, tenant_access_token)
            responses.append(self._send_file_message(file_key, tenant_access_token))
        return responses

    def send_error_summary(
        self,
        error_message: str,
        started_at: datetime,
        output_file: Path | None = None,
        selected_groups: list[str] | None = None,
    ) -> dict[str, Any] | None:
        if not self.webhook_url:
            LOGGER.info("Feishu webhook is empty, skipping error push.")
            return None
        payload = build_error_payload(error_message, started_at, output_file, selected_groups)
        return self._post(payload)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(self.webhook_url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            LOGGER.exception("Failed to send Feishu webhook request.")
            raise FeishuPushError(f"Failed to send Feishu webhook request: {exc}") from exc

        if data.get("code") != 0:
            raise FeishuPushError(
                f"Feishu webhook returned error, code={data.get('code')}, msg={data.get('msg')}"
            )
        return data

    def can_send_files(self) -> bool:
        return all(
            (
                self.app_id,
                self.app_secret,
                self.receive_id_type,
                self.receive_id,
            )
        )

    def _get_tenant_access_token(self) -> str:
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}
        try:
            response = httpx.post(self.AUTH_URL, json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            LOGGER.exception("Failed to fetch Feishu tenant access token.")
            raise FeishuPushError(f"Failed to fetch Feishu tenant access token: {exc}") from exc

        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise FeishuPushError(
                "Feishu tenant access token request failed, "
                f"code={data.get('code')}, msg={data.get('msg')}"
            )
        return str(data["tenant_access_token"])

    def _upload_file(self, file_path: Path, tenant_access_token: str) -> str:
        headers = {"Authorization": f"Bearer {tenant_access_token}"}
        data = {
            "file_type": _get_feishu_file_type(file_path),
            "file_name": file_path.name,
        }
        try:
            with file_path.open("rb") as file_handle:
                files = {
                    "file": (
                        file_path.name,
                        file_handle,
                        _get_mime_type(file_path),
                    )
                }
                response = httpx.post(
                    self.FILE_UPLOAD_URL,
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=self.timeout,
                )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            LOGGER.exception("Failed to upload file to Feishu. file=%s", file_path)
            raise FeishuPushError(f"Failed to upload file to Feishu: {exc}") from exc

        file_key = payload.get("data", {}).get("file_key")
        if payload.get("code") != 0 or not file_key:
            raise FeishuPushError(
                f"Feishu file upload failed, code={payload.get('code')}, msg={payload.get('msg')}"
            )
        return str(file_key)

    def _send_file_message(self, file_key: str, tenant_access_token: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {tenant_access_token}"}
        payload = {
            "receive_id": self.receive_id,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
        }
        try:
            response = httpx.post(
                f"{self.MESSAGE_URL}?receive_id_type={self.receive_id_type}",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            LOGGER.exception("Failed to send Feishu file message. file_key=%s", file_key)
            raise FeishuPushError(f"Failed to send Feishu file message: {exc}") from exc

        if data.get("code") != 0:
            raise FeishuPushError(
                f"Feishu file message failed, code={data.get('code')}, msg={data.get('msg')}"
            )
        return data


class FeishuFileSender:
    def send_file(self, file_path: Path) -> None:
        raise NotImplementedError(
            "Feishu file sending is not enabled in v1. Use the Excel path from the text message."
        )


def _get_feishu_file_type(file_path: Path) -> str:
    return FILE_TYPE_BY_SUFFIX.get(file_path.suffix.lower(), "stream")


def _get_mime_type(file_path: Path) -> str:
    return MIME_TYPE_BY_SUFFIX.get(file_path.suffix.lower(), "application/octet-stream")


def build_success_payload(result: PipelineResult) -> dict[str, Any]:
    lines = ["资讯抓取任务完成"]
    if result.selected_groups:
        lines.append(f"任务分组: {', '.join(result.selected_groups)}")
    lines.extend(
        [
            f"执行时间: {result.finished_at.isoformat()}",
            f"抓取站点数: {result.total_sources}",
            f"成功站点数: {result.successful_sources}",
            f"失败站点数: {result.failed_sources}",
            f"全量新闻条数: {result.raw_total_items}",
            f"筛选后新闻条数: {result.filtered_total_items}",
            f"发布时间未解析条数: {result.unresolved_date_items}",
            f"全量 Excel: {result.raw_output_file.name}",
            f"全量文件路径: {result.raw_output_file}",
            f"筛选后 Excel: {result.filtered_output_file.name}",
            f"筛选后文件路径: {result.filtered_output_file}",
        ]
    )
    if result.failed_source_names:
        lines.append(f"失败站点: {', '.join(result.failed_source_names)}")
    return {"msg_type": "text", "content": {"text": "\n".join(lines)}}


def build_error_payload(
    error_message: str,
    started_at: datetime,
    output_file: Path | None = None,
    selected_groups: list[str] | None = None,
) -> dict[str, Any]:
    lines = ["资讯抓取任务失败"]
    if selected_groups:
        lines.append(f"任务分组: {', '.join(selected_groups)}")
    lines.extend(
        [
            f"开始时间: {started_at.isoformat()}",
            f"错误信息: {error_message}",
        ]
    )
    if output_file:
        lines.append(f"已生成文件: {output_file}")
    return {"msg_type": "text", "content": {"text": "\n".join(lines)}}
