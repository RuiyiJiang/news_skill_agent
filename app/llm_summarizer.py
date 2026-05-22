"""
LLM-powered summarizer module.

This module provides functionality to:
1. Fetch article detail pages and extract full body text
2. Generate concise summaries using LLM
3. Support customizable prompts via environment variables

Usage:
    Enable in .env:
        ENABLE_LLM_SUMMARY=true
        LLM_SUMMARY_SOURCES=界面新闻,36氪,FoodBev
        LLM_SUMMARY_MAX_ITEMS=50
        LLM_SUMMARY_PROMPT_TEMPLATE=<optional custom prompt>
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.models import NewsItem
from app.utils.browser_fetch import fetch_html_with_playwright


LOGGER = logging.getLogger(__name__)

# Default prompt template - modify via LLM_SUMMARY_PROMPT_TEMPLATE env var
DEFAULT_SUMMARY_PROMPT = """你是食品行业专业编辑。请根据给定的资讯正文（或节选），提取一段极简、客观的中文摘要

要求：
1. 提取过程要求信息密度极高，去除所有修饰性废话，直接陈述事实。
2.严禁使用“重磅、震撼、首个、唯一、第一、最”等主观夸张词汇。若原文称行业首创，必须客观描述具体创新点。
3.内容要素：
    - 新品类：必须包含品牌、新品名称、特色配料/工艺、核心卖点、渠道/价格（若有），结尾加“（来源：xx）”。
    - 财报类：必须包含财报周期、营收、利润、同比/环比变化、核心业务表现，金额建议折算人民币，结尾加“（来源：xx）”。
    - 其它类：客观陈述事实、动作与结果，结尾加“（来源：xx）”。
4.噪音过滤：忽略导航、按钮、版权尾注、订阅提示等页面噪音。

标题：{title}
文章正文：
{article_body}

请直接输出摘要，不要加前缀或额外说明。"""


@dataclass
class SummaryResult:
    """Result of LLM summarization."""
    success: bool
    summary: str
    error_message: str | None = None


class LLMSummarizerError(RuntimeError):
    """Raised when LLM summarization fails."""


class LLMSummarizer:
    """LLM-powered article summarizer."""

    # Selector patterns for extracting article body text
    BODY_SELECTORS = [
        "article",
        "main",
        "[class*='article']",
        "[class*='content']",
        "[class*='post']",
        "[class*='news']",
        "[id*='article']",
        "[id*='content']",
        "[class*='body']",
    ]

    # Tags to remove (ads, navigation, comments, etc.)
    REMOVE_SELECTORS = [
        "script", "style", "nav", "header", "footer", "aside",
        "[class*='sidebar']", "[class*='advertisement']",
        "[class*='ad-']", "[class*='related']",
        "[class*='comment']", "[class*='share']",
        "[class*='author']", "[class*='tag']",
    ]

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.prompt_template = self._load_prompt_template()

    def _load_prompt_template(self) -> str:
        """Load prompt template from settings or use default."""
        custom_template = getattr(self.settings, 'llm_summary_prompt_template', None)
        if custom_template and custom_template.strip():
            LOGGER.info("Using custom LLM summary prompt template")
            return custom_template.strip()
        return DEFAULT_SUMMARY_PROMPT

    def summarize_item(self, item: NewsItem) -> SummaryResult:
        """
        Generate a summary for a single news item by:
        1. Fetching the detail page
        2. Extracting the article body
        3. Sending to LLM for summarization
        """
        if not str(item.url):
            return SummaryResult(
                success=False,
                summary="",
                error_message="No URL available"
            )

        # Fetch detail page
        body_text = self._fetch_article_body(str(item.url))
        if not body_text:
            return SummaryResult(
                success=False,
                summary="",
                error_message="Failed to fetch article body"
            )

        if len(body_text) < 50:
            return SummaryResult(
                success=False,
                summary="",
                error_message="Article body too short"
            )

        # Truncate if too long (LLM context limits)
        max_chars = getattr(self.settings, 'llm_summary_max_chars', 8000)
        if len(body_text) > max_chars:
            body_text = body_text[:max_chars] + "..."
            LOGGER.debug("Article body truncated to %d chars", max_chars)

        # Generate summary via LLM
        try:
            summary = self._generate_summary(body_text, item.title)
            return SummaryResult(success=True, summary=summary)
        except Exception as exc:
            LOGGER.exception("Failed to generate LLM summary for: %s", item.title)
            return SummaryResult(
                success=False,
                summary="",
                error_message=str(exc)
            )

    def summarize_items(
        self,
        items: list[NewsItem],
        max_items: int | None = None,
    ) -> list[NewsItem]:
        """
        Generate LLM summaries for a list of news items.

        Args:
            items: List of NewsItem objects
            max_items: Maximum number of items to process (per source)

        Returns:
            List of NewsItem objects with updated summaries
        """
        if not self.settings.enable_llm_summary:
            LOGGER.info("LLM summary is disabled, skipping")
            return items

        if not self.settings.openai_api_key.strip():
            LOGGER.warning("LLM summary enabled but OPENAI_API_KEY is empty")
            return items

        allowed_sources = {
            name.strip()
            for name in self.settings.llm_summary_sources.split(",")
            if name.strip()
        }
        match_all = "*" in allowed_sources
        max_per_source = max_items or self.settings.llm_summary_max_items

        processed_count: dict[str, int] = {}
        results: list[NewsItem] = []

        for item in items:
            # Check if source is allowed
            if not match_all and item.source not in allowed_sources:
                results.append(item)
                continue

            # Check per-source limit
            count = processed_count.get(item.source, 0)
            if count >= max_per_source:
                LOGGER.debug("Source %s reached max items limit %d", item.source, max_per_source)
                results.append(item)
                continue

            processed_count[item.source] = count + 1

            # Generate summary
            summary_result = self.summarize_item(item)

            if summary_result.success:
                # Mark as LLM-generated summary
                item.summary = f"【LLM摘要】{summary_result.summary}"
                LOGGER.info(
                    "Generated LLM summary for: %s (source: %s)",
                    item.title[:50],
                    item.source
                )
            else:
                LOGGER.warning(
                    "LLM summary failed for %s: %s",
                    item.title[:50],
                    summary_result.error_message
                )

            results.append(item)

        return results

    def _fetch_article_body(self, url: str) -> str:
        """Fetch article page and extract main body text."""
        try:
            html = fetch_html_with_playwright(
                url,
                timeout_seconds=self.settings.request_timeout_seconds,
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            )
            if not html:
                return ""

            soup = BeautifulSoup(html, "lxml")

            # Remove unwanted elements
            for selector in self.REMOVE_SELECTORS:
                for elem in soup.select(selector):
                    elem.decompose()

            # Find main content area
            body_content = ""
            for selector in self.BODY_SELECTORS:
                elements = soup.select(selector)
                for elem in elements:
                    text = self._extract_text_from_element(elem)
                    if len(text) > 100:  # Only consider substantial content
                        body_content = text
                        break
                if body_content:
                    break

            # Fallback: try to get all paragraph text
            if not body_content:
                paragraphs = soup.find_all("p")
                texts = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]
                body_content = "\n".join(texts)

            return body_content.strip()

        except Exception as exc:
            LOGGER.debug("Failed to fetch article body from %s: %s", url, exc)
            return ""

    def _extract_text_from_element(self, element: BeautifulSoup) -> str:
        """Extract clean text from a BeautifulSoup element."""
        # Remove script and style tags first
        for tag in element.find_all(["script", "style", "noscript"]):
            tag.decompose()

        text = element.get_text(separator="\n", strip=True)
        # Clean up excessive newlines
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        return "\n".join(lines)

    # 摘要总结
    def _generate_summary(self, article_body: str, title: str) -> str:
        """Call LLM to generate a summary from article body."""
        prompt = self.prompt_template.format(
            article_body=article_body,
            title=title,
        )

        # Use chat/completions endpoint instead of responses
        payload = {
            "model": self.settings.openai_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a professional Chinese news summarizer. Generate concise, informative summaries in Chinese.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "max_tokens": 500,
        }

        response = self._post_chat_completions(payload)
        return self._parse_chat_completion_response(response)

    def _post_chat_completions(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Make API call to LLM chat/completions endpoint with retry logic."""
        last_error: Exception | None = None

        for attempt in range(1, 4):
            try:
                with self._build_client() as client:
                    resp = client.post("/chat/completions", json=payload)
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError) as exc:
                last_error = exc
                if attempt == 3:
                    break
                sleep_seconds = 0.8 * attempt
                LOGGER.warning(
                    "LLM chat request failed, retrying. attempt=%s error=%s sleep=%.1fs",
                    attempt,
                    exc,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)

        raise LLMSummarizerError(f"LLM chat request failed after retries: {last_error}")

    def _parse_chat_completion_response(self, response_data: dict[str, Any]) -> str:
        """Parse chat completion response to extract summary text."""
        choices = response_data.get("choices", [])
        for choice in choices:
            message = choice.get("message") or {}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()

        raise LLMSummarizerError("No content found in chat completion response")

    def _build_client(self) -> httpx.Client:
        """Build HTTP client for LLM API."""
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
