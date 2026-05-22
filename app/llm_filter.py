from __future__ import annotations

import json
import logging
import re
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
            except LLMFilterError:
                LOGGER.warning(
                    "LLM output malformed, discarding item. title=%s",
                    item.title,
                )
                continue
            except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError):
                LOGGER.error(
                    "LLM API call failed, discarding item. title=%s",
                    item.title,
                    exc_info=True,
                )
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
        response = self._post_chat_completions(payload)
        response.raise_for_status()
        data = response.json()

        # Parse chat completion response
        choices = data.get("choices", [])
        for choice in choices:
            message = choice.get("message") or {}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                parsed = _parse_json_robustly(content)
                is_food_related = parsed.get("is_food_related")
                if is_food_related is None:
                    is_food_related = parsed.get("is_food_beverage_restaurant_related")
                return FoodIndustryDecision(
                    is_food_related=bool(is_food_related),
                    reason=str(parsed.get("reason") or parsed.get("why") or ""),
                    topic=str(parsed.get("topic") or parsed.get("category") or ""),
                )

        raise LLMFilterError("No content found in chat completion response")

    def _post_chat_completions(self, payload: dict[str, Any]) -> httpx.Response:
        """Make API call to LLM chat/completions endpoint with retry logic."""
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with self._build_client() as client:
                    return client.post("/chat/completions", json=payload)
            except httpx.HTTPStatusError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError) as exc:
                last_error = exc
                if attempt == 3:
                    break
                sleep_seconds = 0.8 * attempt
                LOGGER.warning(
                    "LLM filter request failed, retrying. attempt=%s error=%s sleep=%.1fs",
                    attempt,
                    exc,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)

        raise LLMFilterError(f"LLM filter request failed after retries: {last_error}")

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
    """Build payload for chat/completions endpoint."""
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    
                    "你正在对一条中文商业新闻进行分类，判断其是否与食品、饮料、餐饮、"
                    "零售食品、乳制品、咖啡、茶饮、烘焙、零食、酒类、包装食品、保健食品、"
                    "营养补充剂、益生菌、餐饮供应链、食品原料、冷链、食品加工设备或食品零售"
                    "行业有实质性关联。\n\n"
                    "分类策略偏向召回（宁可误判也不要漏判）：如果新闻可能涉及食品相关公司、"
                    "品牌、渠道、供应链、监管政策、产品、原料、包装、设备或投资，请将其判定为"
                    "相关。\n\n"
                    "以下类别一律视为食品相关：咖啡连锁、茶饮品牌、乳制品公司、瓶装水、"
                    "功能饮料、零食、酒类、烘焙、预制菜、便利店食品零售、保健食品以及特殊医学/"
                    "健康营养产品。\n\n"
                    "只有当新闻主题明显不属于食品行业时，才判定为不相关。例如：汽车、半导体、"
                    "通用软件、房地产或与食品无关的纯金融话题。\n\n"
                    "请只返回 JSON，且必须严格使用以下三个键：is_food_related、topic。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请仅根据以下标题判断该资讯是否与食品、饮料、咖啡茶饮、乳品、保健营养、食品供应链、食品包装、餐饮行业相关。只返回true/false，不要参考其他信息"
                    f"来源：{item.source}\n"
                    f"标题：{item.title}\n"
                    "只返回 JSON：{\"is_food_related\": true 或 false}"
                ),
            },
        ],
        "max_tokens": 200,
    }


def passes_source_specific_guard(item: NewsItem, decision: FoodIndustryDecision) -> bool:
    """
    Source-specific guard for 36 氪.
    For 36 氪, we require strict signals in the title to avoid false positives.
    """
    if item.source != "36 氪":
        return False

    title = item.title.lower()
    for signal in THIRTY_SIX_KR_STRICT_SIGNALS:
        if signal.lower() in title:
            return False
    return True


def extract_output_text(response_data: dict[str, Any]) -> str:
    """Legacy function - kept for compatibility."""
    choices = response_data.get("choices", [])
    for choice in choices:
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return ""


def _parse_json_robustly(raw: str) -> dict[str, Any]:
    """
    Parse LLM JSON output with tolerance for common formatting issues.

    Handles:
    - Trailing ``}}`` (duplicate closing brace)
    - Markdown code fences (```json ... ```)
    - Leading/trailing whitespace
    - Non-JSON preamble / postamble text

    Raises LLMFilterError when no valid JSON object can be extracted.
    """
    text = raw.strip()

    # 1. Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    # 2. Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 3. Trim trailing duplicate closing braces  e.g. "..." }}
    while text.endswith("}}"):
        text = text[:-1]
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 4. Extract first { ... } via brace-matching regex
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    raise LLMFilterError(f"Cannot extract valid JSON from model output: {raw[:300]}")
