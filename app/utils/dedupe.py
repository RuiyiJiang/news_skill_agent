from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from app.models import NewsItem


def dedupe_news_items(items: Iterable[NewsItem]) -> list[NewsItem]:
    best_by_url: dict[str, NewsItem] = {}
    for item in items:
        key = url_key(item)
        existing = best_by_url.get(key)
        if existing is None or score_item(item) > score_item(existing):
            best_by_url[key] = item

    best_by_semantic_key: dict[str, NewsItem] = {}
    for item in best_by_url.values():
        key = semantic_key(item)
        existing = best_by_semantic_key.get(key)
        if existing is None or score_item(item) > score_item(existing):
            best_by_semantic_key[key] = item

    return list(best_by_semantic_key.values())


def url_key(item: NewsItem) -> str:
    return f"{item.source.strip().lower()}|{normalize_url(str(item.url))}"


def normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def semantic_key(item: NewsItem) -> str:
    source_key = item.source.strip().lower()
    title_key = re.sub(r"\s+", " ", item.title).strip().lower()
    date_key = item.published_at.isoformat() if item.published_at else (item.raw_date_text or "")
    return f"{source_key}|{title_key}|{date_key.strip().lower()}"


def score_item(item: NewsItem) -> int:
    score = 0
    if item.summary:
        score += 3
    if item.content_preview:
        score += 2
    if item.published_at is not None:
        score += 2
    if item.raw_date_text:
        score += 1
    return score
