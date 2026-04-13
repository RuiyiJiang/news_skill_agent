from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"
DEFAULT_SOURCES_FILE = PROJECT_ROOT / "config" / "sources.yaml"


class SettingsError(ValueError):
    """Raised when application settings are invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_PATH,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    app_timezone: str = Field(default="Asia/Shanghai", alias="APP_TIMEZONE")
    output_dir: Path = Field(default=DEFAULT_OUTPUT_DIR, alias="OUTPUT_DIR")
    sources_file: Path = Field(default=DEFAULT_SOURCES_FILE, alias="SOURCES_FILE")
    request_timeout_seconds: float = Field(default=15.0, alias="REQUEST_TIMEOUT_SECONDS")
    max_detail_fetch_per_source: int = Field(default=10, alias="MAX_DETAIL_FETCH_PER_SOURCE")
    max_items_per_source: int = Field(default=30, alias="MAX_ITEMS_PER_SOURCE")
    feishu_webhook_url: str = Field(default="", alias="FEISHU_WEBHOOK_URL")
    feishu_secret: str = Field(default="", alias="FEISHU_SECRET")
    feishu_app_id: str = Field(default="", alias="FEISHU_APP_ID")
    feishu_app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")
    feishu_receive_id_type: str = Field(default="", alias="FEISHU_RECEIVE_ID_TYPE")
    feishu_receive_id: str = Field(default="", alias="FEISHU_RECEIVE_ID")
    enable_feishu: bool = Field(default=False, alias="ENABLE_FEISHU")
    enable_llm_food_filter: bool = Field(default=False, alias="ENABLE_LLM_FOOD_FILTER")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-5.2", alias="OPENAI_MODEL")
    llm_filter_sources: str = Field(default="界面新闻,36氪", alias="LLM_FILTER_SOURCES")
    llm_filter_max_items: int = Field(default=120, alias="LLM_FILTER_MAX_ITEMS")
    include_items_without_parsed_date: bool = Field(
        default=True,
        alias="INCLUDE_ITEMS_WITHOUT_PARSED_DATE",
    )
    browser_proxy_server: str = Field(default="", alias="BROWSER_PROXY_SERVER")
    browser_proxy_username: str = Field(default="", alias="BROWSER_PROXY_USERNAME")
    browser_proxy_password: str = Field(default="", alias="BROWSER_PROXY_PASSWORD")
    schedule_hour: int = Field(default=9, alias="SCHEDULE_HOUR")
    schedule_minute: int = Field(default=0, alias="SCHEDULE_MINUTE")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        normalized = value.upper()
        if normalized not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of: {', '.join(sorted(allowed))}")
        return normalized

    @field_validator("request_timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("REQUEST_TIMEOUT_SECONDS must be positive.")
        return value

    @field_validator("max_detail_fetch_per_source", "max_items_per_source")
    @classmethod
    def validate_positive_ints(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Value must be greater than 0.")
        return value

    @field_validator("llm_filter_max_items")
    @classmethod
    def validate_llm_filter_max_items(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("LLM_FILTER_MAX_ITEMS must be greater than 0.")
        return value

    @field_validator("schedule_hour")
    @classmethod
    def validate_hour(cls, value: int) -> int:
        if not 0 <= value <= 23:
            raise ValueError("SCHEDULE_HOUR must be between 0 and 23.")
        return value

    @field_validator("schedule_minute")
    @classmethod
    def validate_minute(cls, value: int) -> int:
        if not 0 <= value <= 59:
            raise ValueError("SCHEDULE_MINUTE must be between 0 and 59.")
        return value

    @field_validator("feishu_receive_id_type")
    @classmethod
    def validate_feishu_receive_id_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            return ""
        allowed = {"open_id", "union_id", "user_id", "chat_id", "email"}
        if normalized not in allowed:
            raise ValueError(
                "FEISHU_RECEIVE_ID_TYPE must be one of: chat_id, email, open_id, union_id, user_id."
            )
        return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(ENV_PATH)
    try:
        settings = Settings()
    except ValidationError as exc:
        raise SettingsError(_format_validation_error(exc)) from exc

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _format_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(item) for item in error["loc"])
        parts.append(f"{loc}: {error['msg']}")
    return "Configuration error: " + "; ".join(parts)
