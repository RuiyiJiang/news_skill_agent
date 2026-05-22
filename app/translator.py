from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from app.config import Settings


LOGGER = logging.getLogger(__name__)


class BaseTranslator(ABC):
    """Base class for translators."""

    @abstractmethod
    def translate(self, text: str, target_lang: str = "zh") -> str:
        """Translate text to target language."""
        pass


class LLMTranslator(BaseTranslator):
    """LLM-powered translator using the configured OpenAI-compatible API."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._api_base = (settings.translation_base_url or settings.openai_base_url).rstrip("/")
        self._api_key = settings.translation_api_key or settings.openai_api_key
        self._model = settings.translation_model or settings.openai_model
        self._timeout = settings.request_timeout_seconds * 2

    def translate(self, text: str, target_lang: str = "zh") -> str:
        """Translate text using LLM."""
        if not text or not text.strip():
            return ""

        # Detect source language
        source_lang = self._detect_language(text)

        # Skip if already target language
        if source_lang == target_lang:
            return text

        source_lang_name = self._get_lang_name(source_lang)

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": f"你是专业翻译助手。请将{source_lang_name}翻译为中文。保持专业术语准确，语句通顺自然。只返回翻译结果，不要额外说明。",
                },
                {
                    "role": "user",
                    "content": text,
                },
            ],
            "max_tokens": 1000,
        }

        try:
            with self._build_client() as client:
                resp = client.post(f"{self._api_base}/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", [])
                for choice in choices:
                    message = choice.get("message") or {}
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
        except Exception as exc:
            LOGGER.warning("LLM translation failed: %s", exc)

        return ""

    def translate_title(self, title: str) -> str:
        """Translate title to Chinese."""
        return self.translate(title, "zh")

    def translate_summary(self, summary: str) -> str:
        """Translate summary to Chinese."""
        return self.translate(summary, "zh")

    def _detect_language(self, text: str) -> str:
        """Detect language based on character ranges."""
        char_counts = {"zh": 0, "ja": 0, "ko": 0, "en": 0, "other": 0}
        has_japanese_kana = False

        for char in text[:500]:  # Check first 500 chars
            code = ord(char)
            # Japanese Hiragana (3040-309F) or Katakana (30A0-30FF)
            if 0x3040 <= code <= 0x30FF:
                char_counts["ja"] += 1
                has_japanese_kana = True
            # Korean Hangul Syllables (AC00-D7A3)
            elif 0xAC00 <= code <= 0xD7A3:
                char_counts["ko"] += 1
            # Chinese CJK Unified Ideographs (4E00-9FFF)
            elif 0x4E00 <= code <= 0x9FFF:
                char_counts["zh"] += 1
            # Latin alphabet
            elif (0x41 <= code <= 0x5A) or (0x61 <= code <= 0x7A):
                char_counts["en"] += 1
            else:
                char_counts["other"] += 1

        # Priority: if Japanese kana exists, treat as Japanese regardless of kanji count
        if has_japanese_kana:
            return "ja"

        # Determine dominant language
        max_count = max(char_counts.values())
        if max_count == 0:
            return "zh"  # Default to Chinese

        for lang, count in char_counts.items():
            if count == max_count:
                return lang

        return "other"

    def _get_lang_name(self, lang: str) -> str:
        """Get language name in Chinese."""
        names = {
            "en": "英文",
            "ja": "日文",
            "ko": "韩文",
            "zh": "中文",
        }
        return names.get(lang, "外文")

    def _build_client(self) -> httpx.Client:
        """Build HTTP client for LLM API."""
        return httpx.Client(
            timeout=self._timeout,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            http2=False,
            trust_env=False,
        )


def looks_like_chinese(text: str, threshold: float = 0.5) -> bool:
    """Check if text is mostly Chinese (not Japanese or other languages)."""
    if not text or not text.strip():
        return False

    chinese_count = 0
    total_chars = min(len(text), 500)

    for char in text[:total_chars]:
        code = ord(char)
        # Check for Japanese Hiragana (3040-309F) or Katakana (30A0-30FF)
        if 0x3040 <= code <= 0x30FF:
            return False  # Contains Japanese kana, not Chinese
        # Check for Korean Hangul
        elif 0xAC00 <= code <= 0xD7A3:
            return False  # Contains Korean, not Chinese
        # Chinese CJK Unified Ideographs
        elif 0x4E00 <= code <= 0x9FFF:
            chinese_count += 1

    return (chinese_count / total_chars) >= threshold if total_chars > 0 else False
