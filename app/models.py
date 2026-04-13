from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class Label(str, Enum):
    NEW_PRODUCT = "新品上市"
    FUNDING = "企业融投资"
    IPO = "企业上市"
    INNOVATION = "技术创新"
    POLICY = "政策法规"
    UNCATEGORIZED = "未分类"


class SourceConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1)
    base_url: HttpUrl
    list_urls: list[HttpUrl] = Field(min_length=1)
    enabled: bool = True
    parser_type: str = Field(default="generic", min_length=1)
    timezone: str = Field(default="Asia/Shanghai", min_length=1)
    date_format_hint: str | None = None
    max_items: int | None = Field(default=None, ge=1)
    window_days: int = Field(default=2, ge=1)
    groups: list[str] = Field(default_factory=list)
    query_params: dict[str, str] = Field(default_factory=dict)

    @field_validator("groups")
    @classmethod
    def validate_groups(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            group = item.strip()
            if not group:
                continue
            key = group.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(group)
        return normalized

    @field_validator("query_params", mode="before")
    @classmethod
    def validate_query_params(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("query_params must be a mapping.")

        normalized: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()
            if not key:
                continue
            if raw_value is None:
                normalized[key] = ""
            elif isinstance(raw_value, bool):
                normalized[key] = "true" if raw_value else "false"
            else:
                normalized[key] = str(raw_value).strip()
        return normalized


class NewsItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    title: str = Field(min_length=1)
    summary: str = ""
    published_at: datetime | None = None
    url: HttpUrl
    source: str = Field(min_length=1)
    label: str = Field(default=Label.UNCATEGORIZED.value, min_length=1)
    collected_at: datetime
    raw_date_text: str | None = None
    content_preview: str | None = None
    date_parse_status: str = Field(default="missing")
    date_in_scope: bool | None = None

    @field_validator("date_parse_status")
    @classmethod
    def validate_date_parse_status(cls, value: str) -> str:
        allowed = {"parsed", "missing", "failed"}
        if value not in allowed:
            raise ValueError(f"date_parse_status must be one of: {', '.join(sorted(allowed))}")
        return value


class CrawlResult(BaseModel):
    source: str
    source_url: str | None = None
    items: list[NewsItem] = Field(default_factory=list)
    success: bool = True
    error_message: str | None = None


class PipelineResult(BaseModel):
    started_at: datetime
    finished_at: datetime
    total_sources: int
    successful_sources: int
    failed_sources: int
    total_items: int
    raw_total_items: int
    filtered_total_items: int
    unresolved_date_items: int
    output_file: Path
    raw_output_file: Path
    filtered_output_file: Path
    failed_source_names: list[str] = Field(default_factory=list)
    selected_groups: list[str] = Field(default_factory=list)

    def is_successful(self) -> bool:
        return self.failed_sources < self.total_sources
