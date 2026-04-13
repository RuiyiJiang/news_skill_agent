from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Iterable
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.config import Settings
from app.crawlers.base import BaseNewsParser
from app.models import NewsItem, SourceConfig
from app.utils.dates import is_in_yesterday_today_window, parse_datetime_text


LOGGER = logging.getLogger(__name__)
USER_AGENT = "NewsSkillAgent/1.0"


class GenericNewsParser(BaseNewsParser):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        candidates: list[dict[str, str | None]] = []
        for list_url in source.list_urls:
            try:
                html = self._fetch_html(str(list_url))
                soup = BeautifulSoup(html, "lxml")
                candidates.extend(self._extract_list_candidates(soup, source))
            except Exception as exc:
                LOGGER.exception("Failed to fetch list page. source=%s url=%s", source.name, list_url)
                raise RuntimeError(f"Failed to fetch list page {list_url}: {exc}") from exc

        deduped_candidates = self._dedupe_candidates(candidates)[:max_items]

        items: list[NewsItem] = []
        detail_fetches = 0
        for candidate in deduped_candidates:
            article_url = candidate["url"]
            if not article_url:
                continue

            title = (candidate.get("title") or "").strip()
            summary = (candidate.get("summary") or "").strip()
            raw_date_text = (candidate.get("raw_date_text") or "").strip() or None
            published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
            date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
            date_in_scope = is_in_yesterday_today_window(
                published_at,
                now,
                source.timezone,
                window_days=source.window_days,
            )

            content_preview: str | None = None
            if (not summary or published_at is None) and detail_fetches < self.settings.max_detail_fetch_per_source:
                detail_fetches += 1
                try:
                    detail_html = self._fetch_html(article_url)
                    detail = self._extract_detail(detail_html, article_url, source)
                    title = title or detail["title"] or title
                    summary = summary or detail["summary"] or ""
                    content_preview = detail["content_preview"]
                    if published_at is None:
                        detail_raw_date = detail["raw_date_text"]
                        if detail_raw_date and not raw_date_text:
                            raw_date_text = detail_raw_date
                        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
                        date_parse_status = "parsed" if published_at else date_parse_status
                        date_in_scope = is_in_yesterday_today_window(
                            published_at,
                            now,
                            source.timezone,
                            window_days=source.window_days,
                        )
                except Exception:
                    LOGGER.warning(
                        "Failed to fetch article detail, keeping partial data. source=%s url=%s",
                        source.name,
                        article_url,
                    )

            if published_at is not None and date_in_scope is False:
                continue

            if published_at is None and not self.settings.include_items_without_parsed_date:
                LOGGER.warning(
                    "Dropping item with unresolved publish date due to settings. source=%s title=%s",
                    source.name,
                    title,
                )
                continue

            if published_at is None:
                LOGGER.warning(
                    "Keeping item with unresolved publish date. source=%s title=%s raw_date_text=%s",
                    source.name,
                    title,
                    raw_date_text,
                )

            if not title:
                continue

            item = NewsItem(
                title=title,
                summary=summary,
                published_at=published_at,
                url=article_url,
                source=source.name,
                collected_at=now,
                raw_date_text=raw_date_text,
                content_preview=content_preview,
                date_parse_status=date_parse_status,
                date_in_scope=date_in_scope,
            )
            items.append(item)

        return items

    def _fetch_html(self, url: str) -> str:
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def _extract_list_candidates(
        self,
        soup: BeautifulSoup,
        source: SourceConfig,
    ) -> list[dict[str, str | None]]:
        candidates: list[dict[str, str | None]] = []
        article_blocks = soup.select("article, li, div")
        for block in article_blocks:
            if not isinstance(block, Tag):
                continue
            link = self._find_primary_link(block, str(source.base_url))
            if not link:
                continue
            title = self._extract_title(block, link)
            if not title:
                continue
            raw_date_text = self._extract_date_text(block)
            summary = self._extract_summary(block)
            candidates.append(
                {
                    "title": title,
                    "url": link,
                    "summary": summary,
                    "raw_date_text": raw_date_text,
                }
            )
        return candidates

    def _find_primary_link(self, block: Tag, base_url: str) -> str | None:
        anchors = block.select("a[href]")
        base_host = urlparse(base_url).netloc
        for anchor in anchors:
            href = anchor.get("href", "").strip()
            if not href or href.startswith("#") or href.lower().startswith("javascript:"):
                continue
            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)
            if parsed.scheme not in {"http", "https"}:
                continue
            if parsed.netloc and parsed.netloc != base_host:
                continue
            return absolute
        return None

    def _extract_title(self, block: Tag, link: str) -> str:
        selectors = ["h1", "h2", "h3", "h4", ".title", ".headline", "a[title]"]
        for selector in selectors:
            node = block.select_one(selector)
            if node:
                title = self._clean_text(node.get_text(" ", strip=True) or node.get("title", ""))
                if title:
                    return title
        anchor = block.select_one(f'a[href="{link}"]') or block.select_one("a[href]")
        if anchor:
            return self._clean_text(anchor.get_text(" ", strip=True) or anchor.get("title", ""))
        return ""

    def _extract_date_text(self, block: Tag) -> str | None:
        for selector in ["time", ".date", ".time", ".publish-time", ".published", "[datetime]"]:
            node = block.select_one(selector)
            if node:
                value = node.get("datetime") or node.get_text(" ", strip=True)
                cleaned = self._clean_text(value)
                if cleaned:
                    return cleaned
        text = self._clean_text(block.get_text(" ", strip=True))
        match = re.search(
            r"(20\d{2}[年/-]\d{1,2}[月/-]\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
            text,
        )
        return match.group(1) if match else None

    def _extract_summary(self, block: Tag) -> str:
        for selector in ["p", ".summary", ".desc", ".description", ".excerpt"]:
            node = block.select_one(selector)
            if node:
                summary = self._clean_text(node.get_text(" ", strip=True))
                if len(summary) >= 20:
                    return summary
        return ""

    def _extract_detail(
        self,
        html: str,
        article_url: str,
        source: SourceConfig,
    ) -> dict[str, str | None]:
        soup = BeautifulSoup(html, "lxml")
        title = self._clean_text((soup.title.string if soup.title and soup.title.string else ""))
        summary = ""
        raw_date_text: str | None = None
        content_preview: str | None = None

        for selector, attr in [
            ('meta[property="og:description"]', "content"),
            ('meta[name="description"]', "content"),
        ]:
            node = soup.select_one(selector)
            if node and node.get(attr):
                summary = self._clean_text(node.get(attr, ""))
                if summary:
                    break

        if not summary:
            paragraphs = [
                self._clean_text(p.get_text(" ", strip=True))
                for p in soup.select("article p, main p, .article p, .content p, p")
            ]
            paragraphs = [paragraph for paragraph in paragraphs if len(paragraph) >= 20]
            if paragraphs:
                summary = paragraphs[0]
                content_preview = paragraphs[0][:200]

        for selector, attr in [
            ("time", "datetime"),
            ('meta[property="article:published_time"]', "content"),
            ('meta[name="pubdate"]', "content"),
            ('meta[name="publishdate"]', "content"),
        ]:
            node = soup.select_one(selector)
            if node:
                value = node.get(attr) or node.get_text(" ", strip=True)
                cleaned = self._clean_text(value)
                if cleaned:
                    raw_date_text = cleaned
                    break

        if raw_date_text is None:
            raw_date_text = self._extract_date_from_json_ld(soup)

        if raw_date_text is None:
            body_text = self._clean_text(soup.get_text(" ", strip=True))
            match = re.search(
                r"(20\d{2}[年/-]\d{1,2}[月/-]\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
                body_text,
            )
            raw_date_text = match.group(1) if match else None

        return {
            "title": title,
            "summary": summary,
            "raw_date_text": raw_date_text,
            "content_preview": content_preview,
            "url": article_url,
            "source": source.name,
        }

    def _extract_date_from_json_ld(self, soup: BeautifulSoup) -> str | None:
        scripts = soup.select('script[type="application/ld+json"]')
        for script in scripts:
            try:
                if not script.string:
                    continue
                data = json.loads(script.string)
            except json.JSONDecodeError:
                continue
            for candidate in self._walk_json(data):
                if isinstance(candidate, dict):
                    for key in ("datePublished", "dateCreated"):
                        value = candidate.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()
        return None

    def _walk_json(self, payload: object) -> Iterable[object]:
        if isinstance(payload, dict):
            yield payload
            for value in payload.values():
                yield from self._walk_json(value)
        elif isinstance(payload, list):
            for item in payload:
                yield from self._walk_json(item)

    def _dedupe_candidates(self, candidates: list[dict[str, str | None]]) -> list[dict[str, str | None]]:
        seen: set[str] = set()
        deduped: list[dict[str, str | None]] = []
        for candidate in candidates:
            key = f"{candidate.get('title', '').strip().lower()}|{candidate.get('url', '')}"
            if not candidate.get("url") or key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _clean_text(self, value: str | None) -> str:
        if not value:
            return ""
        return re.sub(r"\s+", " ", value).strip()
