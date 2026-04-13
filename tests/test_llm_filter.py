from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import Mock, patch

import httpx

from app.llm_filter import (
    FoodIndustryDecision,
    LLMFilterError,
    OpenAIFoodIndustryFilter,
    build_food_filter_payload,
    extract_output_text,
    passes_source_specific_guard,
)
from app.config import Settings
from app.models import NewsItem


def test_build_food_filter_payload_contains_schema_and_item_text() -> None:
    item = NewsItem(
        title="三元股份拟参股茶饮供应商必如食品",
        summary="与茶饮供应链有关的投资消息",
        published_at=datetime.fromisoformat("2026-04-01T09:00:00+08:00"),
        url="https://example.com/article",
        source="36氪",
        collected_at=datetime.fromisoformat("2026-04-01T09:00:00+08:00"),
        date_parse_status="parsed",
    )
    payload = build_food_filter_payload(item, "gpt-5.2")
    assert payload["model"] == "gpt-5.2"
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["schema"]["properties"]["topic"]["type"] == "string"
    user_text = payload["input"][1]["content"][0]["text"]
    assert "三元股份拟参股茶饮供应商必如食品" in user_text
    system_text = payload["input"][0]["content"][0]["text"]
    assert "health food" in system_text
    assert "Prefer recall over precision" in system_text


def test_extract_output_text_reads_responses_api_shape() -> None:
    response_data = {
        "output": [
            {
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "is_food_related": True,
                                "reason": "涉及茶饮供应链投资",
                                "topic": "食品饮料",
                            }
                        ),
                    }
                ]
            }
        ]
    }
    text = extract_output_text(response_data)
    parsed = json.loads(text)
    assert parsed["is_food_related"] is True


def test_passes_source_specific_guard_rejects_36kr_false_positive() -> None:
    item = NewsItem(
        title="华为2025年营收8809亿元，仅比历史峰值少105亿元",
        summary="公司财报表现强劲。",
        published_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
        url="https://example.com/article",
        source="36氪",
        collected_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
        date_parse_status="parsed",
    )
    decision = FoodIndustryDecision(
        is_food_related=True,
        reason="可能涉及零售消费场景。",
        topic="retail",
    )
    assert passes_source_specific_guard(item, decision) is False


def test_passes_source_specific_guard_keeps_36kr_food_signal() -> None:
    item = NewsItem(
        title="海底捞净利润大跌14%，这届打工人很难再为极致服务买单",
        summary="连锁火锅品牌经营承压。",
        published_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
        url="https://example.com/article",
        source="36氪",
        collected_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
        date_parse_status="parsed",
    )
    decision = FoodIndustryDecision(
        is_food_related=True,
        reason="海底捞属于餐饮连锁。",
        topic="restaurant",
    )
    assert passes_source_specific_guard(item, decision) is True


def test_filter_items_respects_llm_limit_per_source() -> None:
    settings = Settings(
        ENABLE_LLM_FOOD_FILTER=True,
        OPENAI_API_KEY="test-key",
        OPENAI_BASE_URL="https://example.com/v1",
        OPENAI_MODEL="test-model",
        LLM_FILTER_SOURCES="36氪",
        LLM_FILTER_MAX_ITEMS=1,
    )
    llm_filter = OpenAIFoodIndustryFilter(settings)
    llm_filter.client = httpx.Client()  # keep cleanup simple in test

    calls = {"count": 0}

    def fake_classify(item: NewsItem) -> FoodIndustryDecision:
        calls["count"] += 1
        return FoodIndustryDecision(
            is_food_related="海底捞" in item.title,
            reason="test",
            topic="test",
        )

    llm_filter.classify_item = fake_classify  # type: ignore[method-assign]

    items = [
        NewsItem(
            title="北京商报内容",
            summary="",
            published_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
            url="https://example.com/1",
            source="北京商报",
            collected_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
            date_parse_status="parsed",
        ),
        NewsItem(
            title="海底捞净利润大跌14%",
            summary="餐饮企业",
            published_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
            url="https://example.com/2",
            source="36氪",
            collected_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
            date_parse_status="parsed",
        ),
        NewsItem(
            title="华为营收创新高",
            summary="科技企业",
            published_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
            url="https://example.com/3",
            source="36氪",
            collected_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
            date_parse_status="parsed",
        ),
    ]

    filtered = llm_filter.filter_items(items)
    assert calls["count"] == 1
    assert [item.title for item in filtered] == ["北京商报内容", "海底捞净利润大跌14%", "华为营收创新高"]


def test_filter_items_does_not_let_one_source_consume_another_source_limit() -> None:
    settings = Settings(
        ENABLE_LLM_FOOD_FILTER=True,
        OPENAI_API_KEY="test-key",
        OPENAI_BASE_URL="https://example.com/v1",
        OPENAI_MODEL="test-model",
        LLM_FILTER_SOURCES="界面新闻,36氪",
        LLM_FILTER_MAX_ITEMS=1,
    )
    llm_filter = OpenAIFoodIndustryFilter(settings)
    llm_filter.client = httpx.Client()

    calls = {"count": 0}

    def fake_classify(item: NewsItem) -> FoodIndustryDecision:
        calls["count"] += 1
        return FoodIndustryDecision(
            is_food_related="海底捞" in item.title,
            reason="restaurant" if "海底捞" in item.title else "not food",
            topic="restaurant" if "海底捞" in item.title else "tech",
        )

    llm_filter.classify_item = fake_classify  # type: ignore[method-assign]

    items = [
        NewsItem(
            title="界面汽车新闻",
            summary="",
            published_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
            url="https://example.com/a",
            source="界面新闻",
            collected_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
            date_parse_status="parsed",
        ),
        NewsItem(
            title="海底捞净利润大跌14%",
            summary="餐饮企业",
            published_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
            url="https://example.com/b",
            source="36氪",
            collected_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
            date_parse_status="parsed",
        ),
    ]

    filtered = llm_filter.filter_items(items)
    assert calls["count"] == 2
    assert [item.title for item in filtered] == ["海底捞净利润大跌14%"]


def test_post_responses_retries_after_network_error() -> None:
    settings = Settings(
        ENABLE_LLM_FOOD_FILTER=True,
        OPENAI_API_KEY="test-key",
        OPENAI_BASE_URL="https://example.com/v1",
        OPENAI_MODEL="test-model",
    )
    llm_filter = OpenAIFoodIndustryFilter(settings)

    response = Mock()
    attempts = {"count": 0}

    class DummyClient:
        def __enter__(self) -> "DummyClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, *_args, **_kwargs) -> Mock:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise httpx.ConnectError("EOF")
            return response

    with patch.object(llm_filter, "_build_client", return_value=DummyClient()):
        with patch("app.llm_filter.time.sleep", return_value=None):
            result = llm_filter._post_responses({"model": "test"})
    assert attempts["count"] == 2
    assert result is response


def test_classify_item_parses_successful_response() -> None:
    settings = Settings(
        ENABLE_LLM_FOOD_FILTER=True,
        OPENAI_API_KEY="test-key",
        OPENAI_BASE_URL="https://example.com/v1",
        OPENAI_MODEL="test-model",
    )
    llm_filter = OpenAIFoodIndustryFilter(settings)
    item = NewsItem(
        title="海底捞净利润大跌14%",
        summary="餐饮企业",
        published_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
        url="https://example.com/2",
        source="36氪",
        collected_at=datetime.fromisoformat("2026-04-02T09:00:00+08:00"),
        date_parse_status="parsed",
    )

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "output": [
            {
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "is_food_related": True,
                                "reason": "餐饮企业",
                                "topic": "restaurant",
                            }
                        ),
                    }
                ]
            }
        ]
    }
    calls = {"count": 0}

    def fake_post(_payload: dict[str, object]) -> Mock:
        calls["count"] += 1
        return response

    llm_filter._post_responses = fake_post  # type: ignore[method-assign]
    decision = llm_filter.classify_item(item)
    assert calls["count"] == 1
    assert decision.is_food_related is True


def test_post_responses_raises_after_retry_exhausted() -> None:
    settings = Settings(
        ENABLE_LLM_FOOD_FILTER=True,
        OPENAI_API_KEY="test-key",
        OPENAI_BASE_URL="https://example.com/v1",
        OPENAI_MODEL="test-model",
    )
    llm_filter = OpenAIFoodIndustryFilter(settings)

    class DummyClient:
        def __enter__(self) -> "DummyClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, *_args, **_kwargs) -> Mock:
            raise httpx.ConnectError("EOF")

    with patch.object(llm_filter, "_build_client", return_value=DummyClient()):
        with patch("app.llm_filter.time.sleep", return_value=None):
            try:
                llm_filter._post_responses({"model": "test"})
            except LLMFilterError as exc:
                assert "after retries" in str(exc)
            else:
                raise AssertionError("Expected LLMFilterError")
