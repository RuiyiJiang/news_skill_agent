from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.models import NewsItem


LOGGER = logging.getLogger(__name__)
THIRTY_SIX_KR_STRICT_SIGNALS = (
    "食品",
    "饮料",
    "饮品",
    "餐饮",
    "餐厅",
    "咖啡",
    "茶饮",
    "奶茶",
    "乳业",
    "乳品",
    "牛奶",
    "酸奶",
    "冰博克",
    "火锅",
    "烘焙",
    "零食",
    "酒类",
    "白酒",
    "啤酒",
    "葡萄酒",
    "保健",
    "营养",
    "益生菌",
    "冻干粉",
    "商超",
    "超市",
    "生鲜",
    "预制菜",
    "调味",
    "食材",
    "供应链",
    "海底捞",
    "胖东来",
    "赵一鸣",
    "今麦郎",
    "伊利",
    "蒙牛",
    "优思益",
    "皮爷咖啡",
    "大排档",
    "咖啡豆",
)


class LLMFilterError(RuntimeError):
    """Raised when the LLM filter fails."""


@dataclass
class FoodIndustryDecision:
    is_food_related: bool
    reason: str
    topic: str


class OpenAIFoodIndustryFilter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def filter_items(self, items: list[NewsItem]) -> list[NewsItem]:
        if not self.settings.enable_llm_food_filter:
            return items
        if not self.settings.openai_api_key.strip():
            LOGGER.warning("LLM food filter is enabled but OPENAI_API_KEY is empty. Skipping filter.")
            return items

        allowed_sources = {
            name.strip() for name in self.settings.llm_filter_sources.split(",") if name.strip()
        }
        filtered: list[NewsItem] = []
        llm_checked_count_by_source: dict[str, int] = {}
        for item in items:
            if item.source not in allowed_sources:
                filtered.append(item)
                continue
            checked_count = llm_checked_count_by_source.get(item.source, 0)
            if checked_count >= self.settings.llm_filter_max_items:
                filtered.append(item)
                continue

            llm_checked_count_by_source[item.source] = checked_count + 1

            try:
                decision = self.classify_item(item)
            except Exception:
                LOGGER.exception("LLM classification failed, keeping original item. title=%s", item.title)
                filtered.append(item)
                continue

            if decision.is_food_related and not passes_source_specific_guard(item, decision):
                LOGGER.info(
                    "LLM decision rejected by source-specific guard. source=%s title=%s topic=%s reason=%s",
                    item.source,
                    item.title,
                    decision.topic,
                    decision.reason,
                )
                continue

            if decision.is_food_related:
                filtered.append(item)
                LOGGER.info(
                    "LLM kept item as food-related. source=%s title=%s topic=%s reason=%s",
                    item.source,
                    item.title,
                    decision.topic,
                    decision.reason,
                )
            else:
                LOGGER.info(
                    "LLM filtered out item. source=%s title=%s topic=%s reason=%s",
                    item.source,
                    item.title,
                    decision.topic,
                    decision.reason,
                )
        return filtered

    def classify_item(self, item: NewsItem) -> FoodIndustryDecision:
        payload = build_food_filter_payload(item, self.settings.openai_model)
        response = self._post_responses(payload)
        response.raise_for_status()
        data = response.json()
        raw_text = extract_output_text(data)
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise LLMFilterError(f"Invalid JSON returned from model: {raw_text}") from exc
        is_food_related = parsed.get("is_food_related")
        if is_food_related is None:
            is_food_related = parsed.get("is_food_beverage_restaurant_related")
        return FoodIndustryDecision(
            is_food_related=bool(is_food_related),
            reason=str(parsed.get("reason") or parsed.get("why") or ""),
            topic=str(parsed.get("topic") or parsed.get("category") or ""),
        )

    def _post_responses(self, payload: dict[str, Any]) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with self._build_client() as client:
                    return client.post("/responses", json=payload)
            except httpx.HTTPStatusError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError) as exc:
                last_error = exc
                if attempt == 3:
                    break
                sleep_seconds = 0.8 * attempt
                LOGGER.warning(
                    "LLM request failed, retrying. attempt=%s error=%s sleep=%.1fs",
                    attempt,
                    exc,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)

        raise LLMFilterError(f"LLM request failed after retries: {last_error}")

    def _build_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.settings.request_timeout_seconds * 2,
            base_url=self.settings.openai_base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {self.settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            http2=False,
            trust_env=False,
        )


def build_food_filter_payload(item: NewsItem, model: str) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "is_food_related": {"type": "boolean"},
            "reason": {"type": "string"},
            "topic": {"type": "string"},
        },
        "required": ["is_food_related", "reason", "topic"],
        "additionalProperties": False,
    }
    return {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You are classifying whether a Chinese business news item is materially related "
                            "to the food, beverage, restaurant, retail food, dairy, coffee, tea, bakery, "
                            "snack, alcohol, packaged food, health food, nutrition supplement, probiotic, "
                            "restaurant supply chain, food ingredient, cold chain, food processing equipment, "
                            "or food retail industry. Prefer recall over precision: if the item is plausibly "
                            "about a food-related company, brand, channel, supply chain, regulation, product, "
                            "ingredient, packaging, equipment, or investment, keep it as related. "
                            "Treat coffee chains, tea brands, dairy companies, bottled water, functional drinks, "
                            "snacks, alcohol, bakery, prepared food, convenience food retail, health supplements, "
                            "and special medical/health nutrition products as food-related. "
                            "Only mark not related when the main subject is clearly outside the food industry, "
                            "such as automobiles, semiconductors, generic software, real estate, or unrelated finance. "
                            "Return JSON only and use exactly these keys: "
                            "is_food_related, reason, topic."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"来源: {item.source}\n"
                            f"标题: {item.title}\n"
                            f"摘要: {item.summary}\n"
                            f"链接: {item.url}\n"
                            "请判断这条新闻是否和食品饮料/餐饮/咖啡茶饮/乳品/保健营养/食品供应链行业有关。"
                        ),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "food_industry_classification",
                "schema": schema,
                "strict": True,
            }
        },
    }


def extract_output_text(response_data: dict[str, Any]) -> str:
    status = str(response_data.get("status", "")).lower()
    if status == "failed":
        error = response_data.get("error") or {}
        message = error.get("message") or response_data
        raise LLMFilterError(f"Responses API returned failed status: {message}")

    for output in response_data.get("output", []):
        for content in output.get("content", []):
            if content.get("type") == "output_text":
                text = content.get("text", "")
                if text:
                    return text
            if content.get("type") == "text":
                text = content.get("text", "")
                if text:
                    return text

    output_text = response_data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    choices = response_data.get("choices", [])
    for choice in choices:
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            for chunk in content:
                text = chunk.get("text") if isinstance(chunk, dict) else None
                if text:
                    return text

    raise LLMFilterError("No output_text found in Responses API result.")


def passes_source_specific_guard(item: NewsItem, decision: FoodIndustryDecision) -> bool:
    if item.source != "36氪":
        return True

    text = " ".join(
        part for part in [item.title, item.summary, decision.topic, decision.reason] if part
    ).lower()
    return any(signal.lower() in text for signal in THIRTY_SIX_KR_STRICT_SIGNALS)
