from __future__ import annotations

import logging
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape as html_unescape
from urllib.parse import unquote, urlencode, urljoin, urlparse, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag

from app.config import Settings
from app.crawlers.base import BaseNewsParser
from app.models import NewsItem, SourceConfig
from app.utils.dates import (
    build_yesterday_today_window,
    is_in_yesterday_today_window,
    parse_datetime_text,
)
from app.utils.browser_fetch import fetch_html_with_playwright


LOGGER = logging.getLogger(__name__)
USER_AGENT = "NewsSkillAgent/1.0"


class BeijingBusinessTodayParser(BaseNewsParser):
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
        items: list[NewsItem] = []
        for list_url in source.list_urls:
            response = self.client.get(str(list_url))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            container = soup.select_one(".list-con")
            if not container:
                LOGGER.warning("BBT list container not found. source=%s url=%s", source.name, list_url)
                continue

            featured = self._parse_featured(container, source, now)
            if featured is not None:
                items.append(featured)

            for block in container.select("ul > li"):
                parsed = self._parse_list_item(block, source, now)
                if parsed is not None:
                    items.append(parsed)

        return items[:max_items]

    def _parse_featured(
        self,
        container: Tag,
        source: SourceConfig,
        now: datetime,
    ) -> NewsItem | None:
        block = container.select_one(".news-left")
        if not block:
            return None

        link_node = block.select_one('a[href*=".shtml"]')
        title_node = block.select_one(".tit a")
        summary_node = block.select_one(".words")
        article_url = link_node.get("href", "").strip() if link_node else ""
        title = self._clean_text(title_node.get_text(" ", strip=True) if title_node else "")
        summary = self._clean_text(summary_node.get_text(" ", strip=True) if summary_node else "")
        summary = summary.replace("查看详情 >>", "").replace("查看详情>>", "").strip()

        if not article_url or not title:
            return None

        raw_date_text, detail_summary = self._fetch_detail_metadata(article_url)
        summary = summary or detail_summary or ""
        return self._build_item(
            source=source,
            title=title,
            summary=summary,
            url=article_url,
            raw_date_text=raw_date_text,
            now=now,
        )

    def _parse_list_item(self, block: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        title_node = block.select_one('h4 a[href*=".shtml"]')
        if not title_node:
            return None

        article_url = title_node.get("href", "").strip()
        title = self._clean_text(title_node.get_text(" ", strip=True))
        summary_node = block.select_one("p.words")
        summary = self._clean_text(summary_node.get_text(" ", strip=True) if summary_node else "")
        summary = summary.replace("查看详情 >>", "").replace("查看详情>>", "").strip()

        raw_date_text = None
        others = block.select("p.others span")
        if others:
            raw_date_text = self._clean_text(others[-1].get_text(" ", strip=True))

        if not article_url or not title:
            return None

        return self._build_item(
            source=source,
            title=title,
            summary=summary,
            url=article_url,
            raw_date_text=raw_date_text,
            now=now,
        )

    def _fetch_detail_metadata(self, article_url: str) -> tuple[str | None, str | None]:
        try:
            response = self.client.get(article_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
        except Exception:
            LOGGER.warning("Failed to fetch BBT detail page. url=%s", article_url)
            return None, None

        raw_date_text = None
        for selector in [".time", ".article-info span", "time", ".info span"]:
            node = soup.select_one(selector)
            if node:
                text = self._clean_text(node.get_text(" ", strip=True))
                match = re.search(r"20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2})?", text)
                if match:
                    raw_date_text = match.group(0)
                    break

        summary = None
        meta = soup.select_one('meta[name="description"]')
        if meta and meta.get("content"):
            summary = self._clean_text(meta.get("content"))

        if not summary:
            paragraph = soup.select_one(".article-content p, .content p, .text p")
            if paragraph:
                summary = self._clean_text(paragraph.get_text(" ", strip=True))
        return raw_date_text, summary

    def _build_item(
        self,
        *,
        source: SourceConfig,
        title: str,
        summary: str,
        url: str,
        raw_date_text: str | None,
        now: datetime,
    ) -> NewsItem | None:
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None:
            return None
        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _clean_text(self, value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip()


class JiemianNewsflashParser(BaseNewsParser):
    API_BASE = "https://papi.jiemian.com/page/api/kuaixun"

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
        items: list[NewsItem] = []
        seen_ids: set[str] = set()
        window_start, _ = build_yesterday_today_window(now, source.timezone, window_days=source.window_days)

        for list_url in source.list_urls:
            response = self.client.get(str(list_url))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            current_page_last_time: int | None = None
            for block in soup.select("div[data-time][data-id]"):
                raw_id = str(block.get("data-id", "")).strip()
                parsed = self._parse_newsflash(block, source, now)
                if raw_id:
                    seen_ids.add(raw_id)
                if parsed is not None:
                    items.append(parsed)
                raw_time = block.get("data-time")
                if raw_time and raw_time.isdigit():
                    current_page_last_time = int(raw_time)

            cid = self._extract_channel_id(str(list_url), response.text)
            if not cid or current_page_last_time is None:
                continue

            page = 2
            last_time = current_page_last_time
            while len(items) < max_items and last_time:
                payload = self._fetch_more(cid=cid, page=page, start_time=last_time)
                result = payload.get("result", {})
                if isinstance(result, dict):
                    entries = result.get("list", [])
                    hide_button = result.get("hideBtn") is True
                elif isinstance(result, list):
                    entries = result
                    hide_button = False
                else:
                    entries = []
                    hide_button = False
                if not entries:
                    break

                oldest_in_batch: datetime | None = None
                next_last_time: int | None = None
                for entry in entries:
                    raw_id = str(entry.get("id", "")).strip()
                    if raw_id in seen_ids:
                        continue
                    seen_ids.add(raw_id)
                    parsed = self._parse_api_newsflash(entry, source, now)
                    if parsed is not None:
                        items.append(parsed)

                    publishtime = entry.get("publishtime")
                    if isinstance(publishtime, str) and publishtime.isdigit():
                        next_last_time = int(publishtime)
                        published_at = datetime.fromtimestamp(int(publishtime), tz=timezone.utc)
                        oldest_in_batch = published_at if oldest_in_batch is None else min(oldest_in_batch, published_at)

                if hide_button:
                    break
                if oldest_in_batch and oldest_in_batch.astimezone(window_start.tzinfo) < window_start:
                    break
                if next_last_time is None or next_last_time == last_time:
                    break
                last_time = next_last_time
                page += 1

        return items[:max_items]

    def _parse_newsflash(self, block: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        title_node = block.select_one('h4 a[href*="/article/"]')
        if not title_node:
            return None

        title = self._clean_text(title_node.get_text(" ", strip=True))
        article_url = title_node.get("href", "").strip()
        if not title or not article_url:
            return None

        summary_node = block.select_one(".columns-right-center__newsflash-content__summary")
        summary = self._clean_text(summary_node.get_text(" ", strip=True) if summary_node else "")
        timestamp = block.get("data-time")
        raw_date_text = None
        published_at = None
        if timestamp and timestamp.isdigit():
            published_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            raw_date_text = published_at.isoformat()
        else:
            time_node = block.select_one(".columns-right-center__newsflash-date-node")
            raw_date_text = self._clean_text(time_node.get_text(" ", strip=True) if time_node else "")
            published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)

        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None:
            return None

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _parse_api_newsflash(
        self,
        entry: dict[str, object],
        source: SourceConfig,
        now: datetime,
    ) -> NewsItem | None:
        title = self._clean_text(str(entry.get("title", "")))
        article_id = self._clean_text(str(entry.get("id", "")))
        if not title or not article_id:
            return None

        article_url = f"https://www.jiemian.com/article/{article_id}.html"
        summary = self._clean_text(str(entry.get("summary", "")))
        raw_time = self._clean_text(str(entry.get("publishtime", "")))
        published_at = None
        if raw_time.isdigit():
            published_at = datetime.fromtimestamp(int(raw_time), tz=timezone.utc)
        raw_date_text = published_at.isoformat() if published_at else raw_time or None
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None:
            return None

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _fetch_more(self, *, cid: str, page: int, start_time: int) -> dict[str, object]:
        response = self.client.get(
            f"{self.API_BASE}/getlistmore",
            params={
                "cid": cid,
                "page": page,
                "start_time": start_time,
                "tagid": "",
            },
        )
        response.raise_for_status()
        return response.json()

    def _extract_channel_id(self, list_url: str, html: str) -> str | None:
        match = re.search(r"/lists/(\d+)\.html", list_url)
        if match:
            return match.group(1)
        html_match = re.search(r"var cid = '(\d+)'", html)
        if html_match:
            return html_match.group(1)
        return None

    def _clean_text(self, value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip()


class ExampleCustomNewsParser(BaseNewsParser):
    """Fallback placeholder for unsupported custom parser types."""

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        raise NotImplementedError(
            f"Custom parser for source '{source.name}' is not implemented yet."
        )


class FeedNewsParser(BaseNewsParser):
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
        items: list[NewsItem] = []
        for list_url in source.list_urls:
            response = self.client.get(str(list_url))
            response.raise_for_status()
            root = ET.fromstring(response.text)
            for entry in root.findall("./channel/item"):
                parsed = self._parse_item(entry, source, now)
                if parsed is not None:
                    items.append(parsed)
        return items[:max_items]

    def _parse_item(self, entry: ET.Element, source: SourceConfig, now: datetime) -> NewsItem | None:
        title = self._clean_text(entry.findtext("title"))
        link = self._clean_link(entry.findtext("link"))
        summary = self._extract_summary(entry.findtext("description"))
        raw_date_text = self._clean_text(entry.findtext("pubDate"))
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if not title or not link:
            return None
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=link,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _extract_summary(self, raw_html: str | None) -> str:
        soup = BeautifulSoup(raw_html or "", "lxml")
        text = soup.get_text(" ", strip=True)
        return self._clean_text(text)

    def _clean_link(self, value: str | None) -> str:
        if not value:
            return ""
        parsed = urlsplit(value.strip())
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    def _clean_text(self, value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip()


class FoodBevHomepageParser(BaseNewsParser):
    SUMMARY_PREFIX = "【程序生成摘要】"
    GENERIC_HOME_TITLES = {
        "trending topics",
        "latest news",
        "business news",
        "new products",
        "partner content",
        "exclusives",
        "funding & investments",
        "agriculture",
        "innovation",
        "new product",
    }

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
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            next_page_url: str | None = str(list_url)
            while next_page_url and len(items) < max_items:
                response = self.client.get(next_page_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "lxml")

                page_added = 0
                for article_url, title, section_label, raw_date_text in self._extract_homepage_cards(soup):
                    if article_url in seen_urls:
                        continue
                    seen_urls.add(article_url)
                    parsed = self._build_item(source, now, article_url, title, section_label, raw_date_text)
                    if parsed is not None:
                        items.append(parsed)
                        page_added += 1
                    if len(items) >= max_items:
                        return items[:max_items]

                next_page_url = self._extract_next_page_url(soup)
                if page_added == 0:
                    break

        return items[:max_items]

    def _extract_homepage_cards(self, soup: BeautifulSoup) -> list[tuple[str, str, str | None, str | None]]:
        cards: list[tuple[str, str, str | None, str | None]] = []
        seen_urls: set[str] = set()
        for link_node in soup.select('a[href^="https://www.foodbev.com/news/"]'):
            href = self._clean_link(link_node.get("href"))
            if not href or href in seen_urls:
                continue

            title, section_label, raw_date_text = self._extract_card_metadata(link_node)
            if not title:
                title = self._extract_title_from_homepage_link(link_node)
            seen_urls.add(href)
            cards.append((href, title, section_label, raw_date_text))
        return cards

    def _extract_card_metadata(self, link_node: Tag) -> tuple[str, str | None, str | None]:
        card = link_node.find_parent("div", attrs={"role": "listitem"})
        if not isinstance(card, Tag):
            return "", None, None

        texts = [self._clean_text(text) for text in card.stripped_strings]
        texts = [text for text in texts if text]
        title = ""
        section_label = None
        raw_date_text = None

        if texts:
            first = texts[0]
            if not self._is_generic_home_title(first) or first.lower() in self.GENERIC_HOME_TITLES:
                section_label = first

        if len(texts) >= 2 and re.fullmatch(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}", texts[1]):
            raw_date_text = texts[1]

        if len(texts) >= 3:
            title = texts[2]

        return title, section_label, raw_date_text

    def _extract_title_from_homepage_link(self, link_node: Tag) -> str:
        title = self._clean_text(link_node.get_text(" ", strip=True))
        if title:
            return title

        image_node = link_node.select_one("img[alt]")
        if image_node and image_node.get("alt"):
            title = self._clean_text(image_node.get("alt"))
            if title:
                return title

        current: Tag | None = link_node
        for _ in range(6):
            current = current.parent if isinstance(current, Tag) else None
            if not isinstance(current, Tag):
                break
            image_node = current.select_one("img[alt]")
            if image_node and image_node.get("alt"):
                title = self._clean_text(image_node.get("alt"))
                if title:
                    return title
            title_node = current.select_one("h1, h2, h3, h4")
            if title_node:
                title = self._clean_text(title_node.get_text(" ", strip=True))
                if title:
                    return title

        for sibling in link_node.find_all_next(["h1", "h2", "h3", "h4"], limit=2):
            title = self._clean_text(sibling.get_text(" ", strip=True))
            if title:
                return title

        aria_label = self._clean_text(link_node.get("aria-label"))
        if aria_label:
            return aria_label

        title_attr = self._clean_text(link_node.get("title"))
        if title_attr:
            return title_attr

        return ""

    def _build_item(
        self,
        source: SourceConfig,
        now: datetime,
        article_url: str,
        homepage_title: str,
        section_label: str | None,
        list_raw_date_text: str | None,
    ) -> NewsItem | None:
        detail_raw_date_text, summary, detail_title = self._fetch_detail_metadata(article_url)
        title = homepage_title
        if self._is_generic_home_title(title):
            title = detail_title
        if not title:
            title = detail_title
        if not title:
            LOGGER.warning("FoodBev title missing, skipping item. url=%s", article_url)
            return None

        raw_date_text = list_raw_date_text or detail_raw_date_text
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None
        if published_at is None:
            LOGGER.warning("FoodBev published time missing, keeping item with empty date. url=%s", article_url)

        item_source = source.name
        if section_label:
            item_source = f"{source.name} - {section_label}"

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=article_url,
            source=item_source,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _extract_next_page_url(self, soup: BeautifulSoup) -> str | None:
        next_link = soup.select_one('link[rel="next"]')
        if next_link and next_link.get("href"):
            return str(next_link.get("href")).strip()
        return None

    def _fetch_detail_metadata(self, article_url: str) -> tuple[str | None, str, str]:
        try:
            response = self.client.get(article_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            html = response.text
        except Exception:
            LOGGER.warning("Failed to fetch FoodBev detail page. url=%s", article_url)
            return None, "", ""

        summary = self._generate_summary_from_detail(soup)

        detail_title = ""
        for selector in ['meta[property="og:title"]', 'meta[name="twitter:title"]']:
            node = soup.select_one(selector)
            if node and node.get("content"):
                detail_title = self._clean_text(node.get("content"))
                if detail_title:
                    break
        if not detail_title and soup.title and soup.title.string:
            detail_title = self._clean_text(soup.title.string.split("|")[0])
        detail_title = re.sub(r"\s*\|\s*FoodBev Media\s*$", "", detail_title).strip()

        raw_date_text = self._extract_detail_date(soup, html)

        return raw_date_text, summary, detail_title

    def _generate_summary_from_detail(self, soup: BeautifulSoup) -> str:
        # Disabled: LLM will generate summaries instead
        return ""

    def _normalize_summary_paragraph(self, text: str) -> str:
        if not text:
            return ""
        return text

    def _extract_detail_date(self, soup: BeautifulSoup, html: str) -> str | None:
        for selector, attr in [
            ('meta[property="article:published_time"]', "content"),
            ('meta[name="article:published_time"]', "content"),
            ('meta[name="publish_date"]', "content"),
            ("time", "datetime"),
        ]:
            node = soup.select_one(selector)
            if node:
                candidate = self._clean_text(node.get(attr) or node.get_text(" ", strip=True))
                if self._looks_like_real_date(candidate):
                    return candidate

        scripts = soup.select('script[type="application/ld+json"]')
        for script in scripts:
            if not script.string:
                continue
            try:
                payload = json.loads(script.string)
            except json.JSONDecodeError:
                continue
            for candidate in self._walk_json(payload):
                if not isinstance(candidate, dict):
                    continue
                for key in ("datePublished", "dateCreated", "dateModified"):
                    value = candidate.get(key)
                    if isinstance(value, str):
                        cleaned = self._clean_text(value)
                        if self._looks_like_real_date(cleaned):
                            return cleaned

        for pattern in [
            r'"publishedDate"\s*:\s*"([^"]+)"',
            r'"datePublished"\s*:\s*"([^"]+)"',
            r'"_publishDate"\s*:\s*"([^"]+)"',
        ]:
            match = re.search(pattern, html)
            if not match:
                continue
            candidate = self._clean_text(match.group(1))
            if self._looks_like_real_date(candidate):
                return candidate

        return None

    def _walk_json(self, payload: object):
        if isinstance(payload, dict):
            yield payload
            for value in payload.values():
                yield from self._walk_json(value)
        elif isinstance(payload, list):
            for item in payload:
                yield from self._walk_json(item)

    def _looks_like_real_date(self, value: str | None) -> bool:
        if not value:
            return False
        normalized = self._clean_text(value)
        if normalized in {"Date Published", "Date Last Modified"}:
            return False
        return bool(re.search(r"\d{4}-\d{2}-\d{2}", normalized) or re.search(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}", normalized))

    def _clean_link(self, value: str | None) -> str:
        if not value:
            return ""
        parsed = urlsplit(value.strip())
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    def _is_generic_home_title(self, value: str | None) -> bool:
        normalized = self._clean_text(value).lower()
        return not normalized or normalized in self.GENERIC_HOME_TITLES

    def _clean_text(self, value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip()


class ChineseGovernmentPagedListParser(BaseNewsParser):
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
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            for page_number in range(1, self._max_pages(source) + 1):
                page_url = self._build_page_url(str(list_url), page_number)
                html = self._fetch_page_html(page_url, source)
                soup = BeautifulSoup(html, "lxml")
                page_items = self._extract_page_items(soup, source, now, page_url)
                new_items = [item for item in page_items if str(item.url) not in seen_urls]
                if not new_items:
                    break
                for item in new_items:
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if len(items) >= max_items:
                        return items[:max_items]

        return items[:max_items]

    def _fetch_page_html(self, page_url: str, source: SourceConfig) -> str:
        if self._should_use_browser_fetch(source):
            return fetch_html_with_playwright(
                page_url,
                timeout_seconds=self.settings.request_timeout_seconds,
                locale="zh-CN",
                wait_selector=self._browser_wait_selector(source),
                preferred_engine=self._browser_engine(source),
                proxy_server=self._browser_proxy_server(source),
                proxy_username=self._browser_proxy_username(source),
                proxy_password=self._browser_proxy_password(source),
            )
        response = self.client.get(page_url)
        response.raise_for_status()
        return response.text

    def _should_use_browser_fetch(self, source: SourceConfig) -> bool:
        return source.query_params.get("fetch_mode", "").strip().lower() == "browser"

    def _browser_wait_selector(self, source: SourceConfig) -> str | None:
        wait_selector = source.query_params.get("browser_wait_selector", "").strip()
        if wait_selector:
            return wait_selector
        return "li.content-3-left-text, .pagination, body"

    def _browser_engine(self, source: SourceConfig) -> str | None:
        engine = source.query_params.get("browser_engine", "").strip().lower()
        return engine or None

    def _browser_proxy_server(self, source: SourceConfig) -> str | None:
        source_value = source.query_params.get("browser_proxy_server", "").strip()
        if source_value:
            return source_value
        return getattr(self.settings, "browser_proxy_server", "").strip() or None

    def _browser_proxy_username(self, source: SourceConfig) -> str | None:
        source_value = source.query_params.get("browser_proxy_username", "").strip()
        if source_value:
            return source_value
        return getattr(self.settings, "browser_proxy_username", "").strip() or None

    def _browser_proxy_password(self, source: SourceConfig) -> str | None:
        source_value = source.query_params.get("browser_proxy_password", "").strip()
        if source_value:
            return source_value
        return getattr(self.settings, "browser_proxy_password", "").strip() or None

    def _max_pages(self, source: SourceConfig) -> int:
        raw = source.query_params.get("max_pages", "").strip()
        if raw.isdigit():
            return max(1, int(raw))
        return 8

    def _build_page_url(self, base_url: str, page_number: int) -> str:
        if page_number <= 1:
            return base_url
        if base_url.endswith("index.html"):
            return base_url.replace("index.html", f"index_{page_number}.html")
        if base_url.endswith("list.shtml"):
            return base_url.replace("list.shtml", f"list_{page_number}.shtml")

        parsed = urlsplit(base_url)
        path = parsed.path
        if "." in path:
            stem, ext = path.rsplit(".", 1)
            path = f"{stem}_{page_number}.{ext}"
        else:
            path = f"{path.rstrip('/')}_{page_number}"
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    def _extract_page_items(
        self,
        soup: BeautifulSoup,
        source: SourceConfig,
        now: datetime,
        page_url: str,
    ) -> list[NewsItem]:
        items: list[NewsItem] = []
        seen_urls: set[str] = set()
        for block in self._candidate_blocks(soup):
            if not isinstance(block, Tag):
                continue
            anchor = block.select_one("a[href]")
            if not anchor:
                continue

            href = (anchor.get("href") or "").strip()
            title = self._clean_text(anchor.get_text(" ", strip=True) or anchor.get("title", ""))
            if not href or not title or len(title) < 6:
                continue

            article_url = urljoin(page_url, href)
            if article_url in seen_urls:
                continue
            raw_date_text = self._extract_date_text(block)
            published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
            date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
            date_in_scope = is_in_yesterday_today_window(
                published_at,
                now,
                source.timezone,
                window_days=source.window_days,
            )

            if published_at is not None and date_in_scope is False:
                continue
            if published_at is None and not self.settings.include_items_without_parsed_date:
                continue
            seen_urls.add(article_url)

            items.append(
                NewsItem(
                    title=title,
                    summary="",
                    published_at=published_at,
                    url=article_url,
                    source=source.name,
                    collected_at=now,
                    raw_date_text=raw_date_text,
                    date_parse_status=date_parse_status,
                    date_in_scope=date_in_scope,
                )
            )
        return items

    def _candidate_blocks(self, soup: BeautifulSoup) -> list[Tag]:
        preferred_selectors = [
            "li.content-3-left-text",
            ".list-content li",
            ".list_box li",
            ".list li",
            ".newsList li",
            ".con_list li",
        ]
        for selector in preferred_selectors:
            nodes = [node for node in soup.select(selector) if isinstance(node, Tag)]
            if nodes:
                return nodes
        return [node for node in soup.select("li, article") if isinstance(node, Tag)]

    def _extract_date_text(self, block: Tag) -> str | None:
        for selector in ["time", ".date", ".time", "span", "em"]:
            node = block.select_one(selector)
            if node:
                text = self._clean_text(node.get_text(" ", strip=True))
                if re.search(r"20\d{2}[-./年]\d{1,2}[-./月]\d{1,2}", text):
                    return text

        text = self._clean_text(block.get_text(" ", strip=True))
        match = re.search(
            r"(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
            text,
        )
        return match.group(1) if match else None

    def _clean_text(self, value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip()


class CninfoAnnouncementParser(BaseNewsParser):
    API_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    DETAIL_URL = "https://www.cninfo.com.cn/new/disclosure/detail"
    DEFAULT_PAGE_SIZE = 30
    DEFAULT_MAX_PAGES = 10
    DEFAULT_QUERY_PARAMS = {
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": "",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://www.cninfo.com.cn",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        query_params = self._build_query_params(source, now, max_items=max_items)
        page_size = self._parse_positive_int(
            query_params.pop("pageSize", None),
            default=max(1, min(max_items, self.DEFAULT_PAGE_SIZE)),
        )
        default_max_pages = max(1, min(self.DEFAULT_MAX_PAGES, ((max_items - 1) // page_size) + 2))
        max_pages = self._parse_positive_int(
            query_params.pop("maxPages", None),
            default=default_max_pages,
        )

        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for page_number in range(1, max_pages + 1):
            payload = dict(query_params)
            payload["pageNum"] = str(page_number)
            payload["pageSize"] = str(page_size)
            data = self._fetch_page(payload, referer=str(source.list_urls[0]))
            announcements = data.get("announcements", [])
            if not isinstance(announcements, list) or not announcements:
                break

            oldest_in_page: datetime | None = None
            for entry in announcements:
                if not isinstance(entry, dict):
                    continue

                published_at = self._parse_announcement_time(entry.get("announcementTime"), source.timezone)
                if published_at is not None:
                    oldest_in_page = (
                        published_at if oldest_in_page is None else min(oldest_in_page, published_at)
                    )

                item = self._parse_entry(entry, source, now, published_at=published_at)
                if item is None or str(item.url) in seen_urls:
                    continue

                seen_urls.add(str(item.url))
                items.append(item)
                if len(items) >= max_items:
                    return items[:max_items]

            total_pages = self._parse_positive_int(data.get("totalpages"), default=0)
            if total_pages and page_number >= total_pages:
                break
            if oldest_in_page is not None and not is_in_yesterday_today_window(
                oldest_in_page,
                now,
                source.timezone,
                window_days=source.window_days,
            ):
                break

        return items[:max_items]

    def _build_query_params(
        self,
        source: SourceConfig,
        now: datetime,
        *,
        max_items: int,
    ) -> dict[str, str]:
        start, end = build_yesterday_today_window(now, source.timezone, window_days=source.window_days)
        params = dict(self.DEFAULT_QUERY_PARAMS)
        params["pageSize"] = str(max(1, min(max_items, self.DEFAULT_PAGE_SIZE)))
        params["seDate"] = f"{start.strftime('%Y-%m-%d')}~{end.strftime('%Y-%m-%d')}"
        params.update(source.query_params)
        return params

    def _fetch_page(self, payload: dict[str, str], *, referer: str) -> dict[str, object]:
        response = self.client.post(
            self.API_URL,
            data=payload,
            headers={"Referer": referer},
        )
        response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise RuntimeError("Cninfo announcement API returned a non-object payload.")
        return parsed

    def _parse_entry(
        self,
        entry: dict[str, object],
        source: SourceConfig,
        now: datetime,
        *,
        published_at: datetime | None,
    ) -> NewsItem | None:
        title = self._extract_text(entry.get("announcementTitle") or entry.get("shortTitle"))
        sec_name = self._clean_text(entry.get("secName") or entry.get("tileSecName"))
        sec_code = self._clean_text(entry.get("secCode"))
        announcement_id = self._clean_text(entry.get("announcementId"))
        org_id = self._clean_text(entry.get("orgId"))
        article_url = self._build_detail_url(
            sec_code=sec_code,
            announcement_id=announcement_id,
            org_id=org_id,
            published_at=published_at,
        )
        raw_date_text = (
            published_at.astimezone(ZoneInfo(source.timezone)).strftime("%Y-%m-%d %H:%M:%S")
            if published_at is not None
            else None
        )
        date_parse_status = "parsed" if published_at else ("failed" if entry.get("announcementTime") else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if not title or not article_url:
            return None
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        return NewsItem(
            title=title,
            summary=self._build_summary(sec_name=sec_name, sec_code=sec_code),
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _build_detail_url(
        self,
        *,
        sec_code: str,
        announcement_id: str,
        org_id: str,
        published_at: datetime | None,
    ) -> str:
        if not sec_code or not announcement_id or published_at is None:
            return ""

        params = {
            "stockCode": sec_code.split(",")[0].strip(),
            "announcementId": announcement_id,
            "announcementTime": published_at.strftime("%Y-%m-%d"),
        }
        if org_id:
            params["orgId"] = org_id
        return f"{self.DETAIL_URL}?{urlencode(params)}"

    def _build_summary(self, *, sec_name: str, sec_code: str) -> str:
        parts: list[str] = []
        if sec_name:
            parts.append(f"证券简称：{sec_name}")
        if sec_code:
            parts.append(f"证券代码：{sec_code}")
        return "；".join(parts)

    def _parse_announcement_time(self, value: object, timezone_name: str) -> datetime | None:
        if value in (None, ""):
            return None
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return None
        if timestamp > 10**12:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=ZoneInfo(timezone_name))

    def _parse_positive_int(self, value: object, *, default: int) -> int:
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError, AttributeError):
            return default
        return parsed if parsed > 0 else default

    def _extract_text(self, value: object) -> str:
        text = str(value or "")
        if "<" in text and ">" in text:
            text = BeautifulSoup(text, "lxml").get_text(" ", strip=True)
        return self._clean_text(text)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class FoodTalksFlashParser(BaseNewsParser):
    API_BASE = "https://api-we.foodtalks.cn"
    SUMMARY_PREFIX = "【程序生成摘要】"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://www.foodtalks.cn/flash/",
                "Origin": "https://www.foodtalks.cn",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        page_num = 1
        page_size = min(max_items, 20)
        seen_urls: set[str] = set()
        window_start, _ = build_yesterday_today_window(now, source.timezone, window_days=source.window_days)

        for list_url in source.list_urls:
            self._warm_up_source(str(list_url))

            while len(items) < max_items:
                payload = self._fetch_page(page_num=page_num, page_size=page_size)
                data = payload.get("data", {})
                records = data.get("records", []) if isinstance(data, dict) else []
                if not isinstance(records, list) or not records:
                    break

                oldest_in_batch: datetime | None = None
                page_added = 0
                for entry in records:
                    if not isinstance(entry, dict):
                        continue
                    parsed = self._parse_record(entry, source, now)
                    if parsed is None or str(parsed.url) in seen_urls:
                        continue
                    seen_urls.add(str(parsed.url))
                    items.append(parsed)
                    page_added += 1
                    if parsed.published_at is not None:
                        oldest_in_batch = (
                            parsed.published_at
                            if oldest_in_batch is None
                            else min(oldest_in_batch, parsed.published_at)
                        )
                    if len(items) >= max_items:
                        return items[:max_items]

                total_pages = data.get("pages") if isinstance(data, dict) else None
                if page_added == 0:
                    break
                if isinstance(total_pages, int) and page_num >= total_pages:
                    break
                if oldest_in_batch is not None and oldest_in_batch.astimezone(window_start.tzinfo) < window_start:
                    break
                page_num += 1

        return items[:max_items]

    def _warm_up_source(self, list_url: str) -> None:
        try:
            self.client.get(list_url)
        except Exception:
            LOGGER.warning("Failed to warm up FoodTalks flash page. url=%s", list_url)

    def _fetch_page(self, *, page_num: int, page_size: int) -> dict:
        response = self.client.get(
            f"{self.API_BASE}/news/short/news/page",
            params={
                "pageNum": page_num,
                "pageSize": page_size,
                "language": "ZH",
            },
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("FoodTalks API returned non-dict payload")
        return data

    def _parse_record(self, entry: dict, source: SourceConfig, now: datetime) -> NewsItem | None:
        title = self._clean_text(entry.get("title"))
        raw_date_text = self._clean_text(entry.get("publishTime"))
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        record_id = entry.get("id")
        source_url = self._clean_text(entry.get("sourceUrl"))
        if source_url:
            article_url = source_url
        elif record_id is not None:
            article_url = f"https://www.foodtalks.cn/flash/{record_id}"
        else:
            return None

        summary = self._extract_summary(entry.get("content"))
        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            content_preview=summary[:200] or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _extract_summary(self, raw_html: str | None) -> str:
        # Disabled: LLM will generate summaries instead
        return ""

    def _clean_text(self, value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip()


class FoodTalksNewsParser(BaseNewsParser):
    API_BASE = "https://api-we.foodtalks.cn"
    DEFAULT_API_PATH = "/news/news/page"
    DEFAULT_PAGE_SIZE = 20
    DEFAULT_MAX_PAGES = 10
    DEFAULT_LANGUAGE = "ZH"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://www.foodtalks.cn/news",
                "Origin": "https://www.foodtalks.cn",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        query_params = dict(source.query_params)
        api_path = self._normalize_api_path(query_params.pop("api_path", self.DEFAULT_API_PATH))
        language = self._clean_text(query_params.pop("language", self.DEFAULT_LANGUAGE)) or self.DEFAULT_LANGUAGE
        page_size = self._parse_positive_int(
            query_params.pop("pageSize", None),
            default=max(1, min(max_items, self.DEFAULT_PAGE_SIZE)),
        )
        default_max_pages = max(1, min(self.DEFAULT_MAX_PAGES, ((max_items - 1) // page_size) + 2))
        max_pages = self._parse_positive_int(
            query_params.pop("maxPages", None),
            default=default_max_pages,
        )

        items: list[NewsItem] = []
        seen_urls: set[str] = set()
        window_start, _ = build_yesterday_today_window(now, source.timezone, window_days=source.window_days)

        for list_url in source.list_urls:
            self._warm_up_source(str(list_url))
            page_num = 1

            while len(items) < max_items and page_num <= max_pages:
                payload = self._fetch_page(
                    api_path=api_path,
                    page_num=page_num,
                    page_size=page_size,
                    language=language,
                    extra_params=query_params,
                    referer=str(list_url),
                )
                data = payload.get("data", {})
                records = data.get("records", []) if isinstance(data, dict) else []
                if not isinstance(records, list) or not records:
                    break

                oldest_in_batch: datetime | None = None
                page_added = 0
                for entry in records:
                    if not isinstance(entry, dict):
                        continue
                    entry_published_at = parse_datetime_text(
                        self._clean_text(entry.get("publishTime")),
                        source.timezone,
                        source.date_format_hint,
                    )
                    if entry_published_at is not None:
                        oldest_in_batch = (
                            entry_published_at
                            if oldest_in_batch is None
                            else min(oldest_in_batch, entry_published_at)
                        )
                    parsed = self._parse_record(entry, source, now)
                    if parsed is None or str(parsed.url) in seen_urls:
                        continue

                    seen_urls.add(str(parsed.url))
                    items.append(parsed)
                    page_added += 1
                    if len(items) >= max_items:
                        return items[:max_items]

                total_pages = data.get("pages") if isinstance(data, dict) else None
                if page_added == 0:
                    break
                if isinstance(total_pages, int) and page_num >= total_pages:
                    break
                if oldest_in_batch is not None and oldest_in_batch.astimezone(window_start.tzinfo) < window_start:
                    break
                page_num += 1

        return items[:max_items]

    def _warm_up_source(self, list_url: str) -> None:
        try:
            self.client.get(list_url)
        except Exception:
            LOGGER.warning("Failed to warm up FoodTalks news page. url=%s", list_url)

    def _fetch_page(
        self,
        *,
        api_path: str,
        page_num: int,
        page_size: int,
        language: str,
        extra_params: dict[str, str],
        referer: str,
    ) -> dict:
        response = self.client.get(
            f"{self.API_BASE}{api_path}",
            params={
                **extra_params,
                "pageNum": page_num,
                "pageSize": page_size,
                "language": language,
            },
            headers={"Referer": referer},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("FoodTalks news API returned non-dict payload")
        return data

    def _parse_record(self, entry: dict, source: SourceConfig, now: datetime) -> NewsItem | None:
        title = self._clean_text(entry.get("title"))
        raw_date_text = self._clean_text(entry.get("publishTime"))
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        record_id = entry.get("id")
        if record_id is None:
            return None

        article_url = f"https://www.foodtalks.cn/news/{record_id}"
        summary = self._extract_summary(entry.get("summary"))
        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            content_preview=summary[:200] or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _extract_summary(self, value: object) -> str:
        text = str(value or "")
        if "<" in text and ">" in text:
            text = BeautifulSoup(text, "lxml").get_text(" ", strip=True)
        return self._clean_text(text)

    def _normalize_api_path(self, value: str) -> str:
        path = self._clean_text(value) or self.DEFAULT_API_PATH
        return path if path.startswith("/") else f"/{path}"

    def _parse_positive_int(self, value: object, *, default: int) -> int:
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError, AttributeError):
            return default
        return parsed if parsed > 0 else default

    def _clean_text(self, value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip()


class ThePaperExpressNewsParser(BaseNewsParser):
    """澎湃快讯解析器 - 从 Next.js __NEXT_DATA__ 中提取数据"""

    SUMMARY_PREFIX = "【程序生成摘要】"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []

        for list_url in source.list_urls:
            html = self._fetch_page_html(str(list_url), source)
            soup = BeautifulSoup(html, "lxml")

            # 从 __NEXT_DATA__ script 标签中提取 JSON 数据
            next_data_script = soup.select_one("script#__NEXT_DATA__")
            if not next_data_script:
                raise RuntimeError(
                    f"澎湃快讯页面未找到 __NEXT_DATA__ 数据，页面结构可能已变化。url={list_url}"
                )

            try:
                next_data = json.loads(next_data_script.string)
                init_ssr_data = next_data.get("props", {}).get("pageProps", {}).get("initSsrData", {})
                date_list = init_ssr_data.get("dateList", [])
            except (json.JSONDecodeError, AttributeError) as e:
                raise RuntimeError(f"澎湃快讯 __NEXT_DATA__ JSON 解析失败: {e}")

            # 遍历日期列表和新闻列表
            for day_block in date_list:
                if not isinstance(day_block, dict):
                    continue

                pub_date = day_block.get("pubDate", "")
                cont_list = day_block.get("contList", [])

                for entry in cont_list:
                    if not isinstance(entry, dict):
                        continue

                    parsed = self._parse_entry(entry, source, now, pub_date)
                    if parsed is not None:
                        items.append(parsed)
                    if len(items) >= max_items:
                        return items[:max_items]

        # 如果一条都没爬到，说明数据提取失败
        if not items:
            raise RuntimeError(
                f"澎湃快讯爬取未获取到任何新闻条目，"
                f"页面可能结构已变化或无数据。url={source.list_urls}"
            )

        return items[:max_items]

    def _fetch_page_html(self, page_url: str, source: SourceConfig) -> str:
        if self._should_use_browser_fetch(source):
            return fetch_html_with_playwright(
                page_url,
                timeout_seconds=self.settings.request_timeout_seconds,
                locale="zh-CN",
                wait_selector=self._browser_wait_selector(source),
                preferred_engine=self._browser_engine(source),
            )
        response = self.client.get(page_url)
        response.raise_for_status()
        return response.text

    def _should_use_browser_fetch(self, source: SourceConfig) -> bool:
        return source.query_params.get("fetch_mode", "").strip().lower() == "browser"

    def _browser_wait_selector(self, source: SourceConfig) -> str | None:
        wait_selector = source.query_params.get("browser_wait_selector", "").strip()
        if wait_selector:
            return wait_selector
        return "script#__NEXT_DATA__, body"

    def _browser_engine(self, source: SourceConfig) -> str | None:
        engine = source.query_params.get("browser_engine", "").strip().lower()
        return engine or None

    def _parse_entry(self, entry: dict, source: SourceConfig, now: datetime, pub_date: str) -> NewsItem | None:
        """解析单条新闻条目"""
        cont_id = entry.get("contId", "")
        title = self._clean_text(entry.get("name", ""))
        pub_time = entry.get("pubTime", "")

        if not title or not cont_id:
            return None

        # 构建日期时间字符串
        raw_date_text = f"{pub_date} {pub_time}" if pub_date and pub_time else pub_date or pub_time

        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        article_url = f"https://www.thepaper.cn/newsDetail_forward_{cont_id}"

        return NewsItem(
            title=title,
            summary="",
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            content_preview=title[:200] if title else None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class PepsiCoPressReleaseParser(BaseNewsParser):
    API_URL = "https://www.pepsico.com/api/articles"
    DEFAULT_PRESS_RELEASE_TAG = "efeec358-5084-465f-b1b9-a43e61218ded"
    SUMMARY_PREFIX = "【程序生成摘要】"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
                "Referer": "https://www.pepsico.com/newsroom/press-releases-category",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            tags = self._extract_press_release_tags(str(list_url))
            end_cursor: str | None = None

            while len(items) < max_items:
                payload = self._fetch_articles(tags=tags, language="en", end_cursor=end_cursor, page_size=12)
                results = payload.get("results", [])
                if not isinstance(results, list) or not results:
                    break

                oldest_in_batch: datetime | None = None
                for entry in results:
                    if not isinstance(entry, dict):
                        continue
                    parsed, published_at = self._parse_api_entry(entry, source, now)
                    if published_at is not None:
                        oldest_in_batch = (
                            published_at if oldest_in_batch is None else min(oldest_in_batch, published_at)
                        )
                    if parsed is None or str(parsed.url) in seen_urls:
                        continue
                    seen_urls.add(str(parsed.url))
                    items.append(parsed)
                    if len(items) >= max_items:
                        break

                page_info = payload.get("pageInfo", {})
                has_next = bool(page_info.get("hasNext")) if isinstance(page_info, dict) else False
                next_cursor = page_info.get("endCursor") if isinstance(page_info, dict) else None
                end_cursor = str(next_cursor).strip() if next_cursor else None

                if not has_next or not end_cursor:
                    break
                if oldest_in_batch is not None and not is_in_yesterday_today_window(
                    oldest_in_batch,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break

        return items[:max_items]

    def _extract_press_release_tags(self, list_url: str) -> list[str]:
        try:
            response = self.client.get(list_url)
            response.raise_for_status()
        except Exception:
            LOGGER.warning("Failed to load PepsiCo list page, using default tag. url=%s", list_url)
            return [self.DEFAULT_PRESS_RELEASE_TAG]

        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', response.text)
        if not match:
            return [self.DEFAULT_PRESS_RELEASE_TAG]

        try:
            data = json.loads(match.group(1))
            route = data["props"]["pageProps"]["layoutData"]["sitecore"]["route"]
            components = route.get("placeholders", {}).get("Main", [])
        except (KeyError, TypeError, json.JSONDecodeError):
            return [self.DEFAULT_PRESS_RELEASE_TAG]

        for component in components:
            if component.get("componentName") != "DynamicArticleGrid":
                continue
            category_list = component.get("fields", {}).get("CategoryList", [])
            tags: list[str] = []
            for category in category_list:
                fields = category.get("fields", {}) if isinstance(category, dict) else {}
                for tag in fields.get("CategoryTags", []):
                    tag_id = str(tag.get("id", "")).strip()
                    if tag_id:
                        tags.append(tag_id)
            if tags:
                return tags

        return [self.DEFAULT_PRESS_RELEASE_TAG]

    def _fetch_articles(
        self,
        *,
        tags: list[str],
        language: str,
        end_cursor: str | None,
        page_size: int,
    ) -> dict[str, object]:
        params: list[tuple[str, str | int]] = [("language", language), ("pageSize", page_size)]
        for tag in tags:
            params.append(("tag", tag))
        if end_cursor:
            params.append(("endCursor", end_cursor))

        response = self.client.get(self.API_URL, params=params)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _parse_api_entry(
        self,
        entry: dict[str, object],
        source: SourceConfig,
        now: datetime,
    ) -> tuple[NewsItem | None, datetime | None]:
        raw_href = self._clean_text(entry.get("Href"))
        title = self._extract_value(entry.get("Title"))
        if not raw_href or not title:
            return None, None

        article_url = urljoin(str(source.base_url), raw_href)
        raw_date_text, summary = self._fetch_detail_metadata(article_url)
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if published_at is not None and date_in_scope is False:
            return None, published_at
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None, None

        tag_text = self._extract_value(entry.get("Tag"))
        if tag_text and not summary:
            summary = tag_text

        return (
            NewsItem(
                title=title,
                summary=summary,
                published_at=published_at,
                url=article_url,
                source=source.name,
                collected_at=now,
                raw_date_text=raw_date_text,
                date_parse_status=date_parse_status,
                date_in_scope=date_in_scope,
            ),
            published_at,
        )

    def _fetch_detail_metadata(self, article_url: str) -> tuple[str | None, str]:
        try:
            response = self.client.get(article_url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
            response.raise_for_status()
        except Exception:
            LOGGER.warning("Failed to fetch PepsiCo detail page. url=%s", article_url)
            return None, ""

        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', response.text)
        if not match:
            return None, ""

        try:
            data = json.loads(match.group(1))
            fields = data["props"]["pageProps"]["layoutData"]["sitecore"]["route"]["fields"]
        except (KeyError, TypeError, json.JSONDecodeError):
            return None, ""

        raw_date_text = self._extract_value(fields.get("Date")) or self._extract_value(fields.get("ArticleReleaseDate"))
        summary_html = self._extract_value(fields.get("Lede")) or self._extract_value(fields.get("Body"))
        summary = self._extract_summary(summary_html)
        return raw_date_text or None, summary

    def _extract_summary(self, value: str) -> str:
        # Disabled: LLM will generate summaries instead
        return ""

    def _extract_value(self, value: object) -> str:
        if isinstance(value, dict):
            return self._clean_text(value.get("value"))
        return self._clean_text(value)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class PepsiCoChinaMediaCenterParser(BaseNewsParser):
    API_BASE = "https://www.pepsico.com.cn/api"
    SUMMARY_PREFIX = "【程序生成摘要】"
    CATEGORY_BY_SLUG = {
        "company-news": 1,
        "brand-news": 2,
        "pepsico-positive": 3,
        "social-impact": 4,
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
                "Content-Type": "application/json",
                "Referer": "https://www.pepsico.com.cn/media-center/company-news",
            },
            trust_env=False,
        )
        self._detail_cache: dict[int, dict[str, object]] = {}

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            category_slug, category_id = self._resolve_category(list_url)
            if category_id is None:
                LOGGER.warning("Unsupported PepsiCo China category. url=%s", list_url)
                continue

            page = 1
            while len(items) < max_items:
                payload = self._fetch_news_page(
                    category_id=category_id,
                    page=page,
                    page_size=min(20, max_items),
                )
                page_data = payload.get("data", {})
                entries = page_data.get("data", []) if isinstance(page_data, dict) else []
                if not isinstance(entries, list) or not entries:
                    break

                oldest_in_page: datetime | None = None
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    parsed, published_at = self._parse_list_entry(
                        entry=entry,
                        category_slug=category_slug,
                        source=source,
                        now=now,
                    )
                    if published_at is not None:
                        oldest_in_page = published_at if oldest_in_page is None else min(oldest_in_page, published_at)
                    if parsed is None or str(parsed.url) in seen_urls:
                        continue
                    seen_urls.add(str(parsed.url))
                    items.append(parsed)
                    if len(items) >= max_items:
                        break

                last_page = 1
                if isinstance(page_data, dict):
                    try:
                        last_page = int(page_data.get("last_page") or 1)
                    except (TypeError, ValueError):
                        last_page = 1

                if page >= last_page:
                    break
                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break
                page += 1

        return items[:max_items]

    def _resolve_category(self, list_url: object) -> tuple[str, int | None]:
        path = urlparse(str(list_url)).path.rstrip("/")
        slug = path.split("/")[-1].strip().lower()
        return slug, self.CATEGORY_BY_SLUG.get(slug)

    def _fetch_news_page(self, *, category_id: int, page: int, page_size: int) -> dict[str, object]:
        response = self.client.post(
            f"{self.API_BASE}/getNews",
            json={
                "title": "",
                "nid": category_id,
                "page": page,
                "list_rows": page_size,
                "order": "add_time",
                "sort": "desc",
            },
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _parse_list_entry(
        self,
        *,
        entry: dict[str, object],
        category_slug: str,
        source: SourceConfig,
        now: datetime,
    ) -> tuple[NewsItem | None, datetime | None]:
        title = self._clean_text(entry.get("title"))
        if not title:
            return None, None

        raw_date_text = self._clean_text(entry.get("add_time")) or None
        summary = self._clean_text(entry.get("brief"))
        out_link = self._clean_text(entry.get("out_link"))
        article_id = self._extract_int(entry.get("id"))

        article_url = out_link
        if not article_url and article_id is not None:
            article_url = urljoin(str(source.base_url), f"/media-center/{category_slug}/{article_id}")
        if not article_url:
            return None, None

        detail = {}
        if not summary and not out_link and article_id is not None:
            detail = self._fetch_detail(article_id)
            summary = self._extract_detail_summary(detail)
            if raw_date_text is None:
                raw_date_text = (
                    self._clean_text(detail.get("add_time"))
                    or self._clean_text(detail.get("update_time"))
                    or None
                )

        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )
        if published_at is not None and date_in_scope is False:
            return None, published_at
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None, None

        return (
            NewsItem(
                title=title,
                summary=summary,
                published_at=published_at,
                url=article_url,
                source=source.name,
                collected_at=now,
                raw_date_text=raw_date_text,
                date_parse_status=date_parse_status,
                date_in_scope=date_in_scope,
            ),
            published_at,
        )

    def _fetch_detail(self, article_id: int) -> dict[str, object]:
        cached = self._detail_cache.get(article_id)
        if cached is not None:
            return cached

        try:
            response = self.client.post(f"{self.API_BASE}/getNewDetail", json={"id": article_id})
            response.raise_for_status()
            payload = response.json()
        except Exception:
            LOGGER.warning("Failed to fetch PepsiCo China detail. id=%s", article_id)
            self._detail_cache[article_id] = {}
            return {}

        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        normalized = data if isinstance(data, dict) else {}
        self._detail_cache[article_id] = normalized
        return normalized

    def _extract_detail_summary(self, detail: dict[str, object]) -> str:
        # Disabled: LLM will generate summaries instead
        return ""

    def _extract_int(self, value: object) -> int | None:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class UnileverNewsSearchParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            base_url = str(list_url).rstrip("/") + "/"
            last_page = self._discover_last_page(base_url, source)

            for page in range(1, last_page + 1):
                page_url = self._build_page_url(base_url, page)
                try:
                    soup = BeautifulSoup(self._fetch_html(page_url, source), "lxml")
                except Exception:
                    LOGGER.warning("Failed to fetch Unilever search page. url=%s", page_url)
                    break
                cards = soup.select('article[data-testid="uol-c-card"]')
                if not cards:
                    LOGGER.warning("Unilever result cards not found. url=%s", page_url)
                    break

                oldest_in_page: datetime | None = None
                for card in cards:
                    parsed, published_at = self._parse_card(card, source, now)
                    if published_at is not None:
                        oldest_in_page = (
                            published_at if oldest_in_page is None else min(oldest_in_page, published_at)
                        )
                    if parsed is None or str(parsed.url) in seen_urls:
                        continue
                    seen_urls.add(str(parsed.url))
                    items.append(parsed)
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break

        return items[:max_items]

    def _discover_last_page(self, base_url: str, source: SourceConfig) -> int:
        try:
            soup = BeautifulSoup(self._fetch_html(base_url, source), "lxml")
        except Exception:
            LOGGER.warning("Failed to discover Unilever pagination, falling back to page 1. url=%s", base_url)
            return 1

        max_page = 1
        for link in soup.select('a[data-testid="ush-c-results-pagination-pager-link"][href]'):
            href = (link.get("href") or "").strip().rstrip("/")
            if not href:
                continue
            page_segment = href.split("/")[-1]
            if page_segment.isdigit():
                max_page = max(max_page, int(page_segment))
        return max_page

    def _build_page_url(self, base_url: str, page: int) -> str:
        if page <= 1:
            return base_url
        return urljoin(base_url, f"{page}/")

    def _fetch_html(self, url: str, source: SourceConfig) -> str:
        if self._should_use_browser_fetch(source):
            return fetch_html_with_playwright(
                url,
                timeout_seconds=self.settings.request_timeout_seconds * 2,
                user_agent=self.BROWSER_USER_AGENT,
                locale="en-GB",
                wait_selector=self._browser_wait_selector(source),
            )
        try:
            response = self.client.get(url)
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 403:
                raise
            LOGGER.warning("Unilever httpx request returned 403, retrying with curl. url=%s", url)
        except Exception:
            raise

        result = subprocess.run(
            [
                "curl",
                "-L",
                "--max-time",
                str(int(max(5, self.settings.request_timeout_seconds))),
                "-A",
                self.BROWSER_USER_AGENT,
                url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() and not self._looks_like_access_denied(result.stdout):
            return result.stdout

        LOGGER.warning("Unilever curl fallback was blocked, retrying with browser. url=%s", url)
        return fetch_html_with_playwright(
            url,
            timeout_seconds=self.settings.request_timeout_seconds,
            user_agent=self.BROWSER_USER_AGENT,
            locale="en-GB",
            wait_selector='article[data-testid="uol-c-card"], a[data-testid="ush-c-results-pagination-pager-link"][href]',
        )

    def _should_use_browser_fetch(self, source: SourceConfig) -> bool:
        mode = source.query_params.get("fetch_mode", "").strip().lower()
        return mode == "browser"

    def _browser_wait_selector(self, source: SourceConfig) -> str:
        wait_selector = source.query_params.get("browser_wait_selector", "").strip()
        return wait_selector or 'article[data-testid="uol-c-card"], a[data-testid="ush-c-results-pagination-pager-link"][href]'

    def _looks_like_access_denied(self, html: str) -> bool:
        normalized = html.casefold()
        markers = [
            "access denied",
            "errors.edgesuite.net",
            "akamai",
            "you don't have permission to access",
        ]
        return any(marker in normalized for marker in markers)

    def _parse_card(self, card: Tag, source: SourceConfig, now: datetime) -> tuple[NewsItem | None, datetime | None]:
        link_node = card.select_one('a[data-testid="uol-c-card-title-link"][href]')
        if not link_node:
            return None, None

        article_url = urljoin(str(source.base_url), link_node.get("href", "").strip())
        title = self._clean_text(link_node.get_text(" ", strip=True))
        if not article_url or not title:
            return None, None

        time_node = card.select_one("time[datetime], time")
        raw_date_text = ""
        if time_node is not None:
            raw_date_text = self._clean_text(time_node.get("datetime") or time_node.get_text(" ", strip=True))

        summary_node = card.select_one('div[data-testid="uol-c-card-body"]')
        summary = self._clean_text(summary_node.get_text(" ", strip=True) if summary_node else "")

        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if published_at is not None and date_in_scope is False:
            return None, published_at
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None, None

        return (
            NewsItem(
                title=title,
                summary=summary,
                published_at=published_at,
                url=article_url,
                source=source.name,
                collected_at=now,
                raw_date_text=raw_date_text or None,
                date_parse_status=date_parse_status,
                date_in_scope=date_in_scope,
            ),
            published_at,
        )

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class ABInBevNewsMediaParser(BaseNewsParser):
    API_KEY = "2e5c7fb020194c1a8ee80f743d0b923e"
    API_BASE = "https://cdn.builder.io/api/v3/content"
    CATEGORY_NAME_BY_ID = {
        "fb065efe97d74c50a1999c38e8505c59": "Company News",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            content_type = self._resolve_content_type(list_url)
            if content_type is None:
                LOGGER.warning("Unsupported AB InBev news-media URL. url=%s", list_url)
                continue

            offset = 0
            page_size = min(max_items, 20)
            while len(items) < max_items:
                entries = self._fetch_entries(content_type=content_type, limit=page_size, offset=offset)
                if not entries:
                    break

                oldest_in_batch: datetime | None = None
                for entry in entries:
                    parsed, published_at = self._parse_entry(
                        entry=entry,
                        content_type=content_type,
                        source=source,
                        now=now,
                    )
                    if published_at is not None:
                        oldest_in_batch = published_at if oldest_in_batch is None else min(oldest_in_batch, published_at)
                    if parsed is None or str(parsed.url) in seen_urls:
                        continue
                    seen_urls.add(str(parsed.url))
                    items.append(parsed)
                    if len(items) >= max_items:
                        return items[:max_items]

                if len(entries) < page_size:
                    break
                if oldest_in_batch is not None and not is_in_yesterday_today_window(
                    oldest_in_batch,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break
                offset += page_size

        return items[:max_items]

    def _resolve_content_type(self, list_url: object) -> str | None:
        path = urlparse(str(list_url)).path.rstrip("/")
        slug = path.split("/")[-1].strip().lower()
        if slug == "news-stories":
            return "news"
        if slug == "press-releases":
            return "press-releases"
        return None

    def _fetch_entries(self, *, content_type: str, limit: int, offset: int) -> list[dict[str, object]]:
        response = self.client.get(
            f"{self.API_BASE}/{content_type}",
            params={
                "apiKey": self.API_KEY,
                "sort.data.publishDate": "-1",
                "limit": limit,
                "offset": offset,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return []
        results = payload.get("results", [])
        return [entry for entry in results if isinstance(entry, dict)]

    def _parse_entry(
        self,
        *,
        entry: dict[str, object],
        content_type: str,
        source: SourceConfig,
        now: datetime,
    ) -> tuple[NewsItem | None, datetime | None]:
        title = self._clean_text(entry.get("name"))
        data = entry.get("data", {})
        if not isinstance(data, dict) or not title:
            return None, None

        published_at = self._extract_published_at(data, source.timezone)
        raw_date_text = self._clean_text(data.get("publishDate")) or None
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )
        if published_at is not None and date_in_scope is False:
            return None, published_at
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None, None

        article_url = self._build_entry_url(data=data, entry=entry, content_type=content_type, source=source)
        if not article_url:
            return None, published_at

        summary = self._build_summary(data=data, content_type=content_type)
        return (
            NewsItem(
                title=title,
                summary=summary,
                published_at=published_at,
                url=article_url,
                source=source.name,
                collected_at=now,
                raw_date_text=raw_date_text,
                date_parse_status=date_parse_status,
                date_in_scope=date_in_scope,
            ),
            published_at,
        )

    def _extract_published_at(self, data: dict[str, object], timezone_name: str) -> datetime | None:
        raw_value = data.get("publishDate")
        if isinstance(raw_value, (int, float)):
            timestamp = float(raw_value)
            if timestamp > 1_000_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        if isinstance(raw_value, str) and raw_value.strip().isdigit():
            timestamp = float(raw_value.strip())
            if timestamp > 1_000_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return parse_datetime_text(self._clean_text(raw_value), timezone_name, None)

    def _build_entry_url(
        self,
        *,
        data: dict[str, object],
        entry: dict[str, object],
        content_type: str,
        source: SourceConfig,
    ) -> str:
        if content_type == "press-releases":
            for key in ("englishFile", "frenchFile", "dutchFile"):
                candidate = self._clean_text(data.get(key))
                if candidate:
                    return candidate
            return ""

        raw_url = self._clean_text(data.get("url"))
        if raw_url:
            return urljoin(str(source.base_url), raw_url)

        for query in entry.get("query", []) if isinstance(entry.get("query"), list) else []:
            if not isinstance(query, dict):
                continue
            value = self._clean_text(query.get("value"))
            if value:
                return urljoin(str(source.base_url), value)
        return ""

    def _build_summary(self, *, data: dict[str, object], content_type: str) -> str:
        if content_type == "press-releases":
            languages = []
            if self._clean_text(data.get("englishFile")):
                languages.append("EN")
            if self._clean_text(data.get("frenchFile")):
                languages.append("FR")
            if self._clean_text(data.get("dutchFile")):
                languages.append("NL")
            return f"PDF downloads: {' / '.join(languages)}" if languages else ""

        category = self._extract_category_name(data.get("category"))
        text = self._extract_news_text(data)
        if category and text:
            return f"{category} | {text}"
        return category or text

    def _extract_category_name(self, value: object) -> str:
        if isinstance(value, dict):
            category_id = self._clean_text(value.get("id"))
            if category_id:
                return self.CATEGORY_NAME_BY_ID.get(category_id, "")
        return ""

    def _extract_news_text(self, data: dict[str, object]) -> str:
        html = self._clean_text(data.get("oldContent"))
        if not html:
            blocks = data.get("blocks", [])
            if isinstance(blocks, list):
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    component = block.get("component", {})
                    if not isinstance(component, dict):
                        continue
                    options = component.get("options", {})
                    if not isinstance(options, dict):
                        continue
                    html = self._clean_text(options.get("text"))
                    if html:
                        break
        if not html:
            return ""

        soup = BeautifulSoup(html, "lxml")
        snippets: list[str] = []
        for node in soup.select("p, li"):
            text = self._clean_text(node.get_text(" ", strip=True))
            if text:
                snippets.append(text)
            if len(snippets) >= 2:
                break
        if not snippets:
            text = self._clean_text(soup.get_text(" ", strip=True))
        else:
            text = " ".join(snippets).strip()
        if len(text) > 180:
            text = text[:177].rstrip() + "..."
        return text

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class NestleMediaNewsSitemapParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        entries = self._extract_entries(self._fetch_sitemap_xml(source), source)
        items: list[NewsItem] = []

        for entry in entries:
            parsed = self._build_item(entry=entry, source=source, now=now)
            if parsed is None:
                continue
            items.append(parsed)
            if len(items) >= max_items:
                break
        return items[:max_items]

    def _fetch_sitemap_xml(self, source: SourceConfig) -> str:
        sitemap_url = self._resolve_sitemap_url(source)
        try:
            response = self.client.get(sitemap_url)
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 403:
                raise
            LOGGER.warning("Nestle sitemap httpx request returned 403, retrying with curl. url=%s", sitemap_url)
        except Exception:
            raise

        result = subprocess.run(
            [
                "curl",
                "-L",
                "--max-time",
                str(int(max(5, self.settings.request_timeout_seconds))),
                "-A",
                self.BROWSER_USER_AGENT,
                sitemap_url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            stderr = result.stderr.strip()
            raise RuntimeError(f"curl fallback failed for {sitemap_url}: {stderr or f'exit {result.returncode}'}")
        return result.stdout

    def _extract_entries(self, sitemap_xml: str, source: SourceConfig) -> list[dict[str, str]]:
        try:
            root = ET.fromstring(sitemap_xml)
        except ET.ParseError:
            LOGGER.warning("Failed to parse Nestle sitemap XML.")
            return []

        namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        prefixes = self._build_target_prefixes(source)
        entries: list[dict[str, str]] = []
        for node in root.findall("sm:url", namespace):
            loc = self._clean_text(node.findtext("sm:loc", default="", namespaces=namespace))
            lastmod = self._clean_text(node.findtext("sm:lastmod", default="", namespaces=namespace))
            if not any(loc.startswith(prefix) for prefix in prefixes):
                continue
            entries.append({"loc": loc, "lastmod": lastmod})

        entries.sort(key=lambda item: item.get("lastmod", ""), reverse=True)
        return entries

    def _resolve_sitemap_url(self, source: SourceConfig) -> str:
        parsed = urlsplit(str(source.base_url))
        return urlunsplit((parsed.scheme, parsed.netloc, "/sitemap.xml", "", ""))

    def _build_target_prefixes(self, source: SourceConfig) -> list[str]:
        prefixes: list[str] = []
        for list_url in source.list_urls:
            parsed = urlsplit(str(list_url))
            path = parsed.path.rstrip("/")
            if not path:
                continue
            prefixes.append(urlunsplit((parsed.scheme, parsed.netloc, f"{path}/", "", "")))
        return prefixes

    def _build_item(
        self,
        *,
        entry: dict[str, str],
        source: SourceConfig,
        now: datetime,
    ) -> NewsItem | None:
        article_url = self._clean_text(entry.get("loc"))
        raw_date_text = self._clean_text(entry.get("lastmod")) or None
        published_at = self._parse_lastmod(raw_date_text, source.timezone)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        title = self._slug_to_title(article_url)
        if not article_url or not title:
            return None

        return NewsItem(
            title=title,
            summary="",
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _parse_lastmod(self, value: str | None, timezone_name: str) -> datetime | None:
        cleaned = self._clean_text(value)
        if not cleaned:
            return None
        try:
            return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            return parse_datetime_text(cleaned, timezone_name, None)

    def _slug_to_title(self, url: str) -> str:
        slug = urlparse(url).path.rstrip("/").split("/")[-1].strip()
        if not slug:
            return ""
        normalized_slug = unquote(slug)
        normalized_slug = normalized_slug.replace("_", "-")
        words = [part for part in normalized_slug.split("-") if part]
        if words and re.fullmatch(r"\d{8}", words[0]):
            words = words[1:]
        if not words:
            return slug
        return " ".join(self._humanize_slug_word(word) for word in words)

    def _humanize_slug_word(self, word: str) -> str:
        cleaned = word.strip()
        if not cleaned:
            return ""
        if cleaned.isupper() or cleaned.isdigit():
            return cleaned
        return cleaned[:1].upper() + cleaned[1:]

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class NestleChinaMediaListParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            for page_html, page_url in self._iter_page_htmls(str(list_url), max_items):
                for parsed in self._extract_items_from_html(page_html, page_url, source, now):
                    article_url = str(parsed.url)
                    if article_url in seen_urls:
                        continue
                    seen_urls.add(article_url)
                    items.append(parsed)
                    if len(items) >= max_items:
                        return items[:max_items]
        return items[:max_items]

    def _iter_page_htmls(self, start_url: str, max_items: int) -> list[tuple[str, str]]:
        pages: list[tuple[str, str]] = []
        next_url = start_url
        seen_page_urls: set[str] = set()
        max_pages = max(1, min(5, max_items))

        while next_url and next_url not in seen_page_urls and len(pages) < max_pages:
            seen_page_urls.add(next_url)
            html = self._fetch_html(next_url)
            pages.append((html, next_url))
            next_url = self._extract_next_page_url(html, next_url)

        return pages

    def _fetch_html(self, url: str) -> str:
        try:
            response = self.client.get(url)
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 403:
                raise
            LOGGER.warning("Nestle China list request returned 403, retrying with curl. url=%s", url)
        except Exception:
            raise

        result = subprocess.run(
            [
                "curl",
                "-L",
                "--max-time",
                str(int(max(5, self.settings.request_timeout_seconds))),
                "-A",
                self.BROWSER_USER_AGENT,
                "-H",
                "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "-H",
                "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
                url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            stderr = result.stderr.strip()
            raise RuntimeError(f"curl fallback failed for {url}: {stderr or f'exit {result.returncode}'}")
        return result.stdout

    def _extract_next_page_url(self, html: str, current_url: str) -> str | None:
        soup = BeautifulSoup(html, "lxml")
        next_link = soup.select_one("ul.pager a[rel='next'], ul.pager li.pager__item a[href]")
        if not next_link:
            return None
        href = self._clean_text(next_link.get("href"))
        if not href:
            return None
        return urljoin(current_url, href)

    def _extract_items_from_html(
        self,
        html: str,
        page_url: str,
        source: SourceConfig,
        now: datetime,
    ) -> list[NewsItem]:
        soup = BeautifulSoup(html, "lxml")
        rows = soup.select("div.view-article-list table.table tbody tr")
        if not rows:
            LOGGER.warning("Nestle China list rows not found. source=%s url=%s", source.name, page_url)
            return []

        items: list[NewsItem] = []
        for row in rows:
            link_node = row.select_one("td.views-field-title a[href]")
            title = self._clean_text(link_node.get_text(" ", strip=True) if link_node else "")
            href = self._clean_text(link_node.get("href") if link_node else "")
            time_node = row.select_one("td.views-field-published-at time")
            raw_date_text = self._clean_text(time_node.get("datetime") if time_node else "")
            if not raw_date_text and time_node is not None:
                raw_date_text = self._clean_text(time_node.get_text(" ", strip=True))
            article_url = urljoin(page_url, href) if href else ""
            if not article_url or not title:
                continue

            published_at = self._parse_date(raw_date_text, source.timezone)
            date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
            date_in_scope = is_in_yesterday_today_window(
                published_at,
                now,
                source.timezone,
                window_days=source.window_days,
            )
            if published_at is not None and date_in_scope is False:
                continue
            if published_at is None and not self.settings.include_items_without_parsed_date:
                continue

            items.append(
                NewsItem(
                    title=title,
                    summary="",
                    published_at=published_at,
                    url=article_url,
                    source=source.name,
                    collected_at=now,
                    raw_date_text=raw_date_text or None,
                    date_parse_status=date_parse_status,
                    date_in_scope=date_in_scope,
                )
            )
        return items

    def _parse_date(self, value: str | None, timezone_name: str) -> datetime | None:
        cleaned = self._clean_text(value)
        if not cleaned:
            return None
        try:
            return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            return parse_datetime_text(cleaned, timezone_name, None)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class NestleHealthScienceNewsroomParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        detail_budget = max(0, getattr(self.settings, "max_detail_fetch_per_source", 0))
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            base_url = str(list_url)
            last_page = self._discover_last_page(base_url)

            for page in range(0, last_page + 1):
                page_url = self._build_page_url(base_url, page)
                try:
                    soup = BeautifulSoup(self._fetch_html(page_url), "lxml")
                except Exception:
                    LOGGER.warning("Failed to fetch Nestle Health Science newsroom page. url=%s", page_url)
                    break

                cards = soup.select("div.latest-news-item")
                if not cards:
                    LOGGER.warning("Nestle Health Science newsroom cards not found. url=%s", page_url)
                    break

                oldest_in_page: datetime | None = None
                for card in cards:
                    parsed, published_at = self._parse_card(card, source, now)
                    if published_at is not None:
                        oldest_in_page = published_at if oldest_in_page is None else min(oldest_in_page, published_at)
                    if parsed is None or str(parsed.url) in seen_urls:
                        continue

                    if detail_budget > 0 and self._needs_detail_title(parsed.title):
                        detail_title = self._fetch_detail_title(str(parsed.url))
                        if detail_title:
                            parsed.title = detail_title
                        detail_budget -= 1

                    seen_urls.add(str(parsed.url))
                    items.append(parsed)
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break

        return items[:max_items]

    def _discover_last_page(self, base_url: str) -> int:
        try:
            soup = BeautifulSoup(self._fetch_html(base_url), "lxml")
        except Exception:
            LOGGER.warning(
                "Failed to discover Nestle Health Science newsroom pagination, falling back to page 0. url=%s",
                base_url,
            )
            return 0

        max_page = 0
        for link in soup.select("ul.pagination a.page-link[href]"):
            href = self._clean_text(link.get("href"))
            match = re.search(r"[?&]page=(\d+)", href)
            if match:
                max_page = max(max_page, int(match.group(1)))
        return max_page

    def _build_page_url(self, base_url: str, page: int) -> str:
        clean_base = base_url.rstrip("/")
        if page <= 0:
            return clean_base
        return f"{clean_base}?page={page}"

    def _fetch_html(self, url: str) -> str:
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def _parse_card(self, card: Tag, source: SourceConfig, now: datetime) -> tuple[NewsItem | None, datetime | None]:
        link_node = card.select_one("h5 a[href]")
        if not link_node:
            return None, None

        article_url = urljoin(str(source.base_url), self._clean_text(link_node.get("href")))
        title = self._clean_text(link_node.get_text(" ", strip=True))
        if not article_url or not title:
            return None, None

        time_node = card.select_one("time[datetime], time")
        raw_date_text = self._clean_text(time_node.get("datetime") if time_node else "")
        if not raw_date_text and time_node is not None:
            raw_date_text = self._clean_text(time_node.get_text(" ", strip=True))

        expertise_node = card.select_one(".area-of-expertise")
        summary = self._clean_text(expertise_node.get_text(" ", strip=True) if expertise_node else "")
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if published_at is not None and date_in_scope is False:
            return None, published_at
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None, None

        return (
            NewsItem(
                title=title,
                summary=summary,
                published_at=published_at,
                url=article_url,
                source=source.name,
                collected_at=now,
                raw_date_text=raw_date_text or None,
                date_parse_status=date_parse_status,
                date_in_scope=date_in_scope,
            ),
            published_at,
        )

    def _needs_detail_title(self, title: str) -> bool:
        cleaned = self._clean_text(title)
        return cleaned.endswith("…") or cleaned.endswith("...")

    def _fetch_detail_title(self, url: str) -> str:
        try:
            soup = BeautifulSoup(self._fetch_html(url), "lxml")
        except Exception:
            LOGGER.warning("Failed to fetch Nestle Health Science detail page. url=%s", url)
            return ""

        heading = soup.select_one("h1.h1-heading, h1")
        if heading:
            title = self._clean_text(heading.get_text(" ", strip=True))
            if title:
                return title

        title_node = soup.select_one('meta[property="og:title"], meta[name="twitter:title"]')
        if title_node:
            title = self._clean_text(title_node.get("content"))
            if title:
                return title

        if soup.title:
            title = self._clean_text(soup.title.get_text(" ", strip=True))
            if title:
                return re.sub(r"\s*\|\s*Nestl[eé]\s+Health\s+Science\s*$", "", title, flags=re.IGNORECASE)
        return ""

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class MarsNewsAndStoriesParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        detail_budget = max(0, getattr(self.settings, "max_detail_fetch_per_source", 0))
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            base_url = str(list_url)
            last_page = self._discover_last_page(base_url)

            for page in range(0, last_page + 1):
                page_url = self._build_page_url(base_url, page)
                try:
                    soup = BeautifulSoup(self._fetch_html(page_url), "lxml")
                except Exception:
                    LOGGER.warning("Failed to fetch Mars news page. url=%s", page_url)
                    break

                cards = soup.select("article")
                if not cards:
                    LOGGER.warning("Mars article cards not found. url=%s", page_url)
                    break

                oldest_in_page: datetime | None = None
                for card in cards:
                    parsed, published_at = self._parse_card(card, source, now)
                    if published_at is not None:
                        oldest_in_page = published_at if oldest_in_page is None else min(oldest_in_page, published_at)
                    if parsed is None or str(parsed.url) in seen_urls:
                        continue

                    if detail_budget > 0 and self._needs_detail_title(parsed.title):
                        detail_title = self._fetch_detail_title(str(parsed.url))
                        if detail_title:
                            parsed.title = detail_title
                        detail_budget -= 1

                    seen_urls.add(str(parsed.url))
                    items.append(parsed)
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break

        return items[:max_items]

    def _discover_last_page(self, base_url: str) -> int:
        try:
            soup = BeautifulSoup(self._fetch_html(base_url), "lxml")
        except Exception:
            LOGGER.warning("Failed to discover Mars pagination, falling back to page 0. url=%s", base_url)
            return 0

        max_page = 0
        for link in soup.select("ul.pagination a[href]"):
            href = self._clean_text(link.get("href"))
            match = re.search(r"[?&]page=(\d+)", href)
            if match:
                max_page = max(max_page, int(match.group(1)))
        return max_page

    def _build_page_url(self, base_url: str, page: int) -> str:
        clean_base = base_url.rstrip("/")
        if page <= 0:
            return clean_base
        return f"{clean_base}?page={page}"

    def _fetch_html(self, url: str) -> str:
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def _parse_card(self, card: Tag, source: SourceConfig, now: datetime) -> tuple[NewsItem | None, datetime | None]:
        link_node = card.select_one('a[href^="/news-and-stories/"]')
        if not link_node:
            return None, None

        title_node = card.select_one(".field--name-field-heading-hero p, .field--name-field-heading-hero h1")
        date_node = card.select_one("span.coh-ce-7765ac98")
        type_node = card.select_one("span.coh-inline-element")
        summary_node = card.select_one(".coh-inline-element.coh-ce-b7a97813 p")

        article_url = urljoin(str(source.base_url), self._clean_text(link_node.get("href")))
        title = self._clean_text(title_node.get_text(" ", strip=True) if title_node else "")
        raw_date_text = self._clean_text(date_node.get_text(" ", strip=True) if date_node else "")
        content_type = self._clean_text(type_node.get_text(" ", strip=True) if type_node else "")
        summary = self._clean_text(summary_node.get_text(" ", strip=True) if summary_node else "")
        if content_type:
            summary = f"{content_type}: {summary}" if summary else content_type

        if not article_url or not title or not raw_date_text:
            return None, None

        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if published_at is not None and date_in_scope is False:
            return None, published_at
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None, None

        return (
            NewsItem(
                title=title,
                summary=summary,
                published_at=published_at,
                url=article_url,
                source=source.name,
                collected_at=now,
                raw_date_text=raw_date_text,
                date_parse_status=date_parse_status,
                date_in_scope=date_in_scope,
            ),
            published_at,
        )

    def _needs_detail_title(self, title: str) -> bool:
        cleaned = self._clean_text(title)
        return cleaned.endswith("...") or cleaned.endswith("…")

    def _fetch_detail_title(self, url: str) -> str:
        try:
            soup = BeautifulSoup(self._fetch_html(url), "lxml")
        except Exception:
            LOGGER.warning("Failed to fetch Mars detail page. url=%s", url)
            return ""

        title_node = soup.select_one('meta[property="og:title"], meta[name="twitter:title"]')
        if title_node:
            title = self._clean_text(title_node.get("content"))
            if title:
                return re.sub(r"\s*\|\s*Mars\s*$", "", title, flags=re.IGNORECASE)

        heading = soup.select_one("h1")
        if heading:
            title = self._clean_text(heading.get_text(" ", strip=True))
            if title:
                return title

        if soup.title:
            title = self._clean_text(soup.title.get_text(" ", strip=True))
            if title:
                return re.sub(r"\s*\|\s*Mars(?:\s+Global)?\s*$", "", title, flags=re.IGNORECASE)
        return ""

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class CocaColaMediaCenterParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        candidate_limit = max(max_items * 5, 40)
        api_items = self._fetch_api_items(source, now, candidate_limit)
        if api_items:
            return api_items[:max_items]

        links = self._extract_sitemap_links(source)
        if not links:
            list_url = str(source.list_urls[0])
            soup = BeautifulSoup(self._fetch_text(list_url), "lxml")
            links = self._extract_homepage_links(soup)

        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for article_url in links[:candidate_limit]:
            if article_url in seen_urls:
                continue
            seen_urls.add(article_url)
            parsed = self._build_item(article_url, source, now)
            if parsed is None:
                continue
            items.append(parsed)
        items.sort(
            key=lambda item: (
                item.published_at is not None,
                item.published_at or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        return items[:max_items]

    def _fetch_api_items(self, source: SourceConfig, now: datetime, candidate_limit: int) -> list[NewsItem]:
        list_url = str(source.list_urls[0])
        try:
            list_html = self._fetch_text(list_url)
        except Exception:
            LOGGER.warning("Failed to fetch Coca-Cola media center page for API config. url=%s", list_url)
            return []

        site_key = self._extract_cloudsearch_site_key(list_html)
        content_types = self._extract_content_types(list_html)
        if not site_key or not content_types:
            return []

        api_url = urljoin(str(source.base_url), "/api/search")
        params = {
            "q": self._build_content_types_query(content_types),
            "q.parser": "structured",
            "sort": "publication_date desc",
            "fq": f"site:'{site_key}'",
            "start": 0,
            "size": candidate_limit,
        }

        try:
            response = self.client.get(api_url, params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            LOGGER.warning("Failed to fetch Coca-Cola media center API. url=%s site=%s", api_url, site_key)
            return []

        hits = payload.get("hits", {}).get("hit", [])
        items: list[NewsItem] = []
        for hit in hits:
            fields = hit.get("fields", {})
            article_path = self._clean_text(fields.get("path"))
            title = self._clean_text(fields.get("title"))
            if not article_path or not title:
                continue

            raw_date_text = self._clean_text(fields.get("publication_date"))
            published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
            date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
            date_in_scope = is_in_yesterday_today_window(
                published_at,
                now,
                source.timezone,
                window_days=source.window_days,
            )

            if published_at is not None and date_in_scope is False:
                # Sorted descending by publication_date, so we can stop once we leave the window.
                break
            if published_at is None and not self.settings.include_items_without_parsed_date:
                continue

            items.append(
                NewsItem(
                    title=title,
                    summary=self._clean_text(fields.get("description")),
                    published_at=published_at,
                    url=urljoin(str(source.base_url), article_path),
                    source=source.name,
                    collected_at=now,
                    raw_date_text=raw_date_text or None,
                    date_parse_status=date_parse_status,
                    date_in_scope=date_in_scope,
                )
            )
        return items

    def _fetch_text(self, url: str) -> str:
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def _extract_sitemap_links(self, source: SourceConfig) -> list[str]:
        sitemap_url = self._resolve_sitemap_url(source)
        if not sitemap_url:
            return []

        try:
            sitemap_xml = self._fetch_text(sitemap_url)
        except Exception:
            LOGGER.warning("Failed to fetch Coca-Cola sitemap. url=%s", sitemap_url)
            return []

        try:
            root = ET.fromstring(sitemap_xml)
        except ET.ParseError:
            LOGGER.warning("Failed to parse Coca-Cola sitemap XML. url=%s", sitemap_url)
            return []

        namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        links: list[str] = []
        for node in root.findall("sm:url", namespace):
            loc = self._clean_text(node.findtext("sm:loc", default="", namespaces=namespace))
            if self._is_media_center_article_url(loc):
                links.append(loc)

        return list(reversed(links))

    def _resolve_sitemap_url(self, source: SourceConfig) -> str:
        parsed = urlsplit(str(source.base_url))
        robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
        try:
            robots_text = self._fetch_text(robots_url)
        except Exception:
            LOGGER.warning("Failed to fetch Coca-Cola robots.txt. url=%s", robots_url)
            return ""

        for match in re.finditer(r"(?im)^Sitemap:\s*(\S+)", robots_text):
            sitemap_url = self._clean_text(match.group(1))
            if sitemap_url:
                return sitemap_url
        return ""

    def _is_media_center_article_url(self, url: str) -> bool:
        if not url:
            return False
        parsed = urlsplit(url)
        path = parsed.path.rstrip("/")
        return path.startswith("/media-center/") and path not in {"/media-center", "/media-center-"}

    def _extract_homepage_links(self, soup: BeautifulSoup) -> list[str]:
        links: list[str] = []
        for section_id in ("text-9b987e301a", "text-fdbf700205"):
            section = soup.select_one(f"#{section_id}")
            if not section:
                continue
            for link in section.select("ul li a[href]"):
                href = self._clean_text(link.get("href"))
                if not href or href in {"/media-center", "/media-center-"}:
                    continue
                links.append(href)
        return links

    def _extract_cloudsearch_site_key(self, html: str) -> str:
        match = re.search(r'window\.tccc\.cloudsearch\s*=\s*"([^"]+)"', html)
        if match:
            return self._clean_text(match.group(1))
        return ""

    def _extract_content_types(self, html: str) -> list[str]:
        match = re.search(r'<div id="searchResult"[^>]*data-params=(["\'])(.*?)\1', html, re.DOTALL)
        if not match:
            return []

        raw_params = html_unescape(match.group(2))
        try:
            params = json.loads(raw_params)
        except json.JSONDecodeError:
            return []

        filters = params.get("contentTypeFilters")
        if not isinstance(filters, list):
            return []

        content_types: list[str] = []
        seen: set[str] = set()
        for item in filters:
            if not isinstance(item, dict):
                continue
            content_type = self._clean_text(item.get("contentType"))
            if not content_type or content_type in seen:
                continue
            seen.add(content_type)
            content_types.append(content_type)
        return content_types

    def _build_content_types_query(self, content_types: list[str]) -> str:
        clauses = " ".join(f"content_type: '{content_type}'" for content_type in content_types)
        return f"(and (or {clauses}))"

    def _build_item(self, article_url: str, source: SourceConfig, now: datetime) -> NewsItem | None:
        html = self._fetch_text(article_url)
        soup = BeautifulSoup(html, "lxml")

        title = self._extract_detail_title(soup)
        if not title:
            return None

        summary = self._extract_detail_summary(soup)
        raw_date_text = self._extract_detail_date(soup, html)
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _extract_detail_title(self, soup: BeautifulSoup) -> str:
        for selector, attr in [
            ('meta[property="og:title"]', "content"),
            ('meta[name="twitter:title"]', "content"),
        ]:
            node = soup.select_one(selector)
            if node:
                title = self._clean_text(node.get(attr))
                if title:
                    return title

        if soup.title:
            return self._clean_text(soup.title.get_text(" ", strip=True))
        return ""

    def _extract_detail_summary(self, soup: BeautifulSoup) -> str:
        for selector, attr in [
            ('meta[name="description"]', "content"),
            ('meta[property="og:description"]', "content"),
        ]:
            node = soup.select_one(selector)
            if node:
                text = self._clean_text(node.get(attr))
                if text:
                    return text
        return ""

    def _extract_detail_date(self, soup: BeautifulSoup, html: str) -> str | None:
        for selector, attr in [
            ('meta[property="article:published_time"]', "content"),
            ('meta[name="article:published_time"]', "content"),
            ('meta[name="publish_date"]', "content"),
            ('meta[name="publicationDate"]', "content"),
            (".cmp-publication-date", "datetime"),
            ("time", "datetime"),
        ]:
            node = soup.select_one(selector)
            if node:
                candidate = self._clean_text(node.get(attr) or node.get_text(" ", strip=True))
                if self._looks_like_real_date(candidate):
                    return candidate

        for script in soup.select('script[type="application/ld+json"]'):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for candidate in self._walk_json(payload):
                if not isinstance(candidate, dict):
                    continue
                for key in ("datePublished", "dateCreated", "dateModified"):
                    value = candidate.get(key)
                    if isinstance(value, str):
                        cleaned = self._clean_text(value)
                        if self._looks_like_real_date(cleaned):
                            return cleaned

        for pattern in [
            r'"datePublished"\s*:\s*"([^"]+)"',
            r'"publishDate"\s*:\s*"([^"]+)"',
            r"\b[A-Z][A-Za-z .'-]+,\s+[A-Z][a-z]+\s+\d{1,2},\s+\d{4}\b",
        ]:
            match = re.search(pattern, html)
            if match:
                candidate = self._clean_text(match.group(1) if match.lastindex else match.group(0))
                if self._looks_like_real_date(candidate):
                    return candidate
        return None

    def _looks_like_real_date(self, value: str) -> bool:
        if not value:
            return False
        return bool(
            re.search(
                r"\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}",
                value,
            )
        )

    def _walk_json(self, payload: object):
        if isinstance(payload, dict):
            yield payload
            for value in payload.values():
                yield from self._walk_json(value)
        elif isinstance(payload, list):
            for item in payload:
                yield from self._walk_json(item)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class DanonePressReleasesParser(BaseNewsParser):
    DEFAULT_INDEX_NAME = "prod_DANONERENEW_news_en"
    DEFAULT_FILTER_EXPRESSION = (
        'category.titles:"Press release" AND '
        '(subject.titles:"Corporate news" OR '
        'subject.titles:"Other news related to Danone" OR '
        'subject.titles:"Local news" OR '
        'subject.titles:"Brand news" OR '
        'subject.titles:"Corporate campaign" OR '
        'subject.titles:"Sustainability")'
    )
    DEFAULT_HITS_PER_PAGE = 20
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        list_url = str(source.list_urls[0])
        html = self._fetch_text(list_url)
        search_config = self._extract_search_config(html)
        if search_config is None:
            LOGGER.warning("Danone search config not found. source=%s url=%s", source.name, list_url)
            return []

        app_id = self._clean_text(search_config.get("appId")) or "EH0TYTZBJR"
        query_key = self._clean_text(search_config.get("queryKey"))
        if not query_key:
            LOGGER.warning("Danone query key missing. source=%s url=%s", source.name, list_url)
            return []

        index_config = self._extract_index_config(search_config)
        index_name = self._clean_text(index_config.get("indexName")) or self.DEFAULT_INDEX_NAME
        filter_expression = self._clean_text(index_config.get("filterExpression"))
        if not filter_expression and index_name == self.DEFAULT_INDEX_NAME:
            filter_expression = self.DEFAULT_FILTER_EXPRESSION
        hits_per_page = self._extract_hits_per_page(index_config)

        items: list[NewsItem] = []
        seen_urls: set[str] = set()
        page = 0
        total_pages = 1

        while len(items) < max_items and page < total_pages:
            result_page = self._search_page(
                app_id=app_id,
                query_key=query_key,
                index_name=index_name,
                filter_expression=filter_expression,
                hits_per_page=hits_per_page,
                page=page,
            )
            total_pages = int(result_page.get("nbPages") or 0) or 1
            hits = result_page.get("hits", [])
            if not isinstance(hits, list) or not hits:
                break

            for hit in hits:
                parsed = self._build_item(hit, source, now)
                if parsed is None:
                    continue
                url_text = str(parsed.url)
                if url_text in seen_urls:
                    continue
                seen_urls.add(url_text)
                items.append(parsed)
                if len(items) >= max_items:
                    break
            page += 1

        return items[:max_items]

    def _extract_search_config(self, html: str) -> dict | None:
        soup = BeautifulSoup(html, "lxml")
        for node in soup.select(".instant-search-comp[data-searchjson]"):
            raw_config = node.get("data-searchjson") or node.get("data-searchJson")
            if not raw_config:
                continue
            try:
                payload = json.loads(html_unescape(str(raw_config)))
            except json.JSONDecodeError:
                continue
            instant_search = payload.get("instantSearch")
            if not isinstance(instant_search, dict):
                continue
            indices = instant_search.get("indices")
            if isinstance(indices, list) and indices:
                return instant_search
        return None

    def _extract_index_config(self, search_config: dict) -> dict:
        indices = search_config.get("indices")
        if not isinstance(indices, list):
            return {}
        for index_config in indices:
            if not isinstance(index_config, dict):
                continue
            index_name = self._clean_text(index_config.get("indexName"))
            filter_expression = self._clean_text(index_config.get("filterExpression"))
            if index_name == self.DEFAULT_INDEX_NAME or "Press release" in filter_expression:
                return index_config
        return indices[0] if indices else {}

    def _extract_hits_per_page(self, index_config: dict) -> int:
        hits_per_page = index_config.get("hitsPerPage")
        if isinstance(hits_per_page, list) and hits_per_page:
            value = hits_per_page[0].get("value")
            try:
                return int(value)
            except (TypeError, ValueError):
                return self.DEFAULT_HITS_PER_PAGE
        try:
            return int(hits_per_page)
        except (TypeError, ValueError):
            return self.DEFAULT_HITS_PER_PAGE

    def _search_page(
        self,
        *,
        app_id: str,
        query_key: str,
        index_name: str,
        filter_expression: str,
        hits_per_page: int,
        page: int,
    ) -> dict:
        endpoint = f"https://{app_id}-dsn.algolia.net/1/indexes/*/queries"
        params = urlencode(
            {
                "query": "",
                "hitsPerPage": hits_per_page,
                "page": page,
                "filters": filter_expression,
            }
        )
        payload = {
            "requests": [
                {
                    "indexName": index_name,
                    "params": params,
                }
            ]
        }
        response = self.client.post(
            endpoint,
            headers={
                "x-algolia-agent": "Algolia for JavaScript (4.19.1); Browser",
                "x-algolia-api-key": query_key,
                "x-algolia-application-id": app_id,
                "content-type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])
        if not isinstance(results, list) or not results:
            return {}
        first_result = results[0]
        return first_result if isinstance(first_result, dict) else {}

    def _fetch_text(self, url: str) -> str:
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def _build_item(self, hit: dict, source: SourceConfig, now: datetime) -> NewsItem | None:
        title = self._clean_text(hit.get("title"))
        article_url = self._clean_text(hit.get("detailspage"))
        if not article_url:
            external_links = hit.get("externallink")
            if isinstance(external_links, list) and external_links:
                article_url = self._clean_text(external_links[0])
        timestamp_ms = hit.get("date")
        if not title or not article_url or timestamp_ms is None:
            return None

        try:
            published_at = datetime.fromtimestamp(
                int(timestamp_ms) / 1000,
                tz=timezone.utc,
            ).astimezone(ZoneInfo(source.timezone))
        except (TypeError, ValueError, OSError):
            return None

        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        subject = hit.get("subject") or {}
        summary = ""
        if isinstance(subject, dict):
            titles = subject.get("titles")
            if isinstance(titles, list) and titles:
                filtered_titles = [self._clean_text(title) for title in titles if self._clean_text(title)]
                summary = self._clean_text(" / ".join(filtered_titles[1:] or filtered_titles[:1]))

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=urljoin(str(source.base_url), article_url),
            source=source.name,
            collected_at=now,
            raw_date_text=str(timestamp_ms),
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class MondelezNewsParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        list_url = str(source.list_urls[0])
        try:
            list_html = self._fetch_text(list_url)
        except Exception:
            LOGGER.warning("Failed to fetch Mondelez news page HTML. url=%s", list_url)
            list_html = ""

        if list_html:
            dom_items = self._extract_dom_items(list_html, source, now, max_items)
            if dom_items:
                return dom_items[:max_items]

        page_data_url = self._build_page_data_url(list_url)
        try:
            payload = json.loads(self._fetch_text(page_data_url))
        except Exception:
            LOGGER.warning("Failed to fetch Mondelez page-data. url=%s", page_data_url)
            return []

        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for entry in self._extract_cards(payload):
            parsed = self._build_item(entry, source, now)
            if parsed is None:
                continue
            if str(parsed.url) in seen_urls:
                continue
            seen_urls.add(str(parsed.url))
            items.append(parsed)
            if len(items) >= max_items:
                break

        return items[:max_items]

    def _extract_dom_items(
        self,
        html: str,
        source: SourceConfig,
        now: datetime,
        max_items: int,
    ) -> list[NewsItem]:
        soup = BeautifulSoup(html, "lxml")
        container = soup.select_one(".newsMainWrapContainer")
        if not container:
            return []

        items: list[NewsItem] = []
        seen_urls: set[str] = set()
        for story in container.select(".NewsStoryWrapper > div"):
            parsed = self._parse_story_card(story, source, now)
            if parsed is None:
                continue
            if str(parsed.url) in seen_urls:
                continue
            seen_urls.add(str(parsed.url))
            items.append(parsed)
            if len(items) >= max_items:
                break
        return items

    def _build_page_data_url(self, list_url: str) -> str:
        parsed = urlsplit(list_url)
        path = parsed.path.strip("/")
        page_path = f"/page-data/{path}/page-data.json" if path else "/page-data/index/page-data.json"
        return urlunsplit((parsed.scheme, parsed.netloc, page_path, "", ""))

    def _fetch_text(self, url: str) -> str:
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def _extract_cards(self, payload: dict) -> list[dict]:
        page_context = payload.get("result", {}).get("pageContext", {})
        for component in page_context.get("componentProps", []):
            wrapper = component.get("ListPaginationFilterWrapper")
            if not wrapper:
                continue
            cards = wrapper.get("newsCardsListCollection", {}).get("items", [])
            if isinstance(cards, list):
                return cards
        return []

    def _parse_story_card(self, story: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        article_link = None
        for link in story.select('a[href^="/news/"]'):
            href = self._clean_text(link.get("href"))
            if not href or "?filter=" in href:
                continue
            article_link = link
            break
        if article_link is None:
            return None

        title = self._clean_text(article_link.get_text(" ", strip=True))
        article_path = self._clean_text(article_link.get("href"))
        if not title or not article_path:
            return None

        raw_date_text = ""
        for text in story.stripped_strings:
            candidate = self._clean_text(text)
            if re.search(
                r"^[A-Za-z]+,\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}$",
                candidate,
            ):
                raw_date_text = self._normalize_date_text(candidate)
                break

        summary = ""
        title_block = article_link.find_parent("div")
        if title_block is not None:
            sibling = title_block.find_next_sibling("div")
            if sibling is not None:
                summary = self._clean_text(sibling.get_text(" ", strip=True))

        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None:
            return None

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=urljoin(str(source.base_url), article_path),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _build_item(self, entry: dict, source: SourceConfig, now: datetime) -> NewsItem | None:
        title_link = entry.get("titleLink") or {}
        article_path = self._clean_text(
            title_link.get("url") or title_link.get("label") or title_link.get("title") or title_link.get("name")
        )
        title = self._clean_text(
            title_link.get("label") or title_link.get("title") or title_link.get("name")
        )
        if not article_path or not title:
            return None

        raw_date_text = self._normalize_date_text(self._clean_text(entry.get("date")))
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None:
            return None

        return NewsItem(
            title=title,
            summary=self._clean_text(entry.get("description")),
            published_at=published_at,
            url=urljoin(str(source.base_url), article_path),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _normalize_date_text(self, raw_date_text: str) -> str:
        if not raw_date_text:
            return ""
        return re.sub(r"^[A-Za-z]+,\s*", "", raw_date_text)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class KraftHeinzPressReleaseParser(BaseNewsParser):
    API_KEY = "BF185719B0464B3CB809D23926182246"
    CATEGORY_ID = "1cb807d2-208f-4bc3-9133-6a9ad45ac3b0"
    LIST_ENDPOINT = "https://news.kraftheinzcompany.com/feed/PressRelease.svc/GetPressReleaseList"
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        payload = self._fetch_feed(limit=max_items)
        entries = payload.get("GetPressReleaseListResult", [])
        if not isinstance(entries, list):
            LOGGER.warning("Unexpected Kraft Heinz response shape. source=%s", source.name)
            return []

        items: list[NewsItem] = []
        seen_urls: set[str] = set()
        for entry in entries:
            parsed = self._build_item(entry, source, now)
            if parsed is None:
                continue
            url_text = str(parsed.url)
            if url_text in seen_urls:
                continue
            seen_urls.add(url_text)
            items.append(parsed)
            if len(items) >= max_items:
                break
        return items

    def _fetch_feed(self, limit: int) -> dict:
        params = {
            "apiKey": self.API_KEY,
            "LanguageId": 1,
            "pageSize": limit,
            "pageNumber": 0,
            "includeTags": "true",
            "year": -1,
            "excludeSelection": 1,
            "bodyType": 0,
            "pressReleaseDateFilter": 3,
            "categoryId": self.CATEGORY_ID,
        }
        response = self.client.get(self.LIST_ENDPOINT, params=params)
        response.raise_for_status()
        return response.json()

    def _build_item(
        self,
        entry: dict,
        source: SourceConfig,
        now: datetime,
    ) -> NewsItem | None:
        title = self._clean_text(entry.get("Headline"))
        url = self._build_url(source.base_url, entry.get("LinkToDetailPage"))
        raw_date = self._clean_text(entry.get("PressReleaseDate"))
        published_at = parse_datetime_text(
            raw_date,
            source.timezone,
            date_format_hint="%m/%d/%Y %H:%M:%S",
        )
        if not title or not url or published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        summary = self._clean_text(
            entry.get("ShortDescription")
            or entry.get("ShortBody")
            or entry.get("Subheadline")
            or ""
        )

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _build_url(self, base_url: str, path: object) -> str:
        href = self._clean_text(path)
        if not href:
            return ""
        return urljoin(str(base_url), href)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class GeneralMillsPressReleaseParser(BaseNewsParser):
    SEARCH_ENDPOINT = "https://www.generalmills.com/sxa/search/results/"
    SEARCH_PARAMS = {
        "v": "{A7D8D918-DEE3-49A3-9DF0-9A9D9913AAEA}",
        "s": "{9374E420-B3D8-418D-AEDB-C018CF2C716D}",
        "l": "en",
        "defaultSortOrder": "Publish Date,Descending",
        "sig": "content-lake-listing",
        "itemid": "{D26E828A-69E7-42CA-85A6-D69E25E347A8}",
        "autoFireSearch": "true",
    }
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        list_url = str(source.list_urls[0])
        html = self._fetch_text(list_url)

        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        featured = self._parse_featured(html, source, now)
        if featured is not None:
            items.append(featured)
            seen_urls.add(str(featured.url))

        for parsed in self._parse_rendered_cards(html, source, now, max_items=max_items):
            if str(parsed.url) in seen_urls:
                continue
            seen_urls.add(str(parsed.url))
            items.append(parsed)
            if len(items) >= max_items:
                return items[:max_items]

        for result in self._fetch_result_entries(max_items):
            article_url = result["url"]
            if article_url in seen_urls:
                continue
            parsed = self._build_news_item(
                title=result["title"],
                article_url=article_url,
                raw_date=result["raw_date"],
                summary=result["summary"],
                source=source,
                now=now,
            )
            if parsed is None and not result["has_metadata"]:
                try:
                    parsed = self._parse_detail(article_url, source, now)
                except Exception:
                    LOGGER.warning("Failed to parse General Mills detail. url=%s", article_url)
                    continue
            if parsed is None:
                continue
            seen_urls.add(str(parsed.url))
            items.append(parsed)
            if len(items) >= max_items:
                break

        return items[:max_items]

    def _parse_rendered_cards(
        self,
        html: str,
        source: SourceConfig,
        now: datetime,
        *,
        max_items: int,
    ) -> list[NewsItem]:
        soup = BeautifulSoup(html, "lxml")
        items: list[NewsItem] = []

        for card in soup.select(".search-results.content-cards .press-release-card")[:max_items]:
            title = self._clean_text(card.select_one(".card-title, .field-cardtitle"))
            summary = self._clean_text(card.select_one(".card-description, .field-cardsummary"))
            article_url = self._build_url(card.select_one("a.card-coverLink"))
            month = self._clean_text(card.select_one(".card-overlayDate-month"))
            day = self._clean_text(card.select_one(".card-overlayDate-day"))
            year = self._clean_text(card.select_one(".card-overlayDate-year"))
            raw_date = f"{month} {day}, {year}".strip(" ,")
            parsed = self._build_news_item(
                title=title,
                article_url=article_url,
                raw_date=raw_date,
                summary=summary,
                source=source,
                now=now,
            )
            if parsed is not None:
                items.append(parsed)

        return items

    def _fetch_result_entries(self, limit: int) -> list[dict[str, object]]:
        params = dict(self.SEARCH_PARAMS)
        params["p"] = str(limit)
        response = self.client.get(self.SEARCH_ENDPOINT, params=params)
        response.raise_for_status()
        payload = response.json()
        results = payload.get("Results", [])
        if not isinstance(results, list):
            return []

        entries: list[dict[str, object]] = []
        for entry in results:
            article_url = self._build_url(entry.get("Url"))
            if not article_url:
                continue

            title = ""
            raw_date = ""
            summary = ""
            html = self._clean_text(entry.get("Html"))
            if html:
                soup = BeautifulSoup(html, "lxml")
                card = soup.select_one(".press-release-card") or soup
                title = self._clean_text(card.select_one(".card-title, .field-cardtitle"))
                summary = self._clean_text(card.select_one(".card-description, .field-cardsummary"))
                month = self._clean_text(card.select_one(".card-overlayDate-month"))
                day = self._clean_text(card.select_one(".card-overlayDate-day"))
                year = self._clean_text(card.select_one(".card-overlayDate-year"))
                raw_date = f"{month} {day}, {year}".strip(" ,")

            entries.append(
                {
                    "url": article_url,
                    "title": title,
                    "summary": summary,
                    "raw_date": raw_date,
                    "has_metadata": bool(title and raw_date),
                }
            )
        return entries

    def _parse_featured(
        self,
        html: str,
        source: SourceConfig,
        now: datetime,
    ) -> NewsItem | None:
        soup = BeautifulSoup(html, "lxml")
        block = soup.select_one(".featured-story-hero .hero-content-inner")
        if not block:
            return None

        title = self._clean_text(block.select_one(".field-cardtitle, .field-title"))
        raw_date = self._clean_text(block.select_one(".field-publishdate"))
        summary = self._clean_text(block.select_one(".field-cardsummary"))
        article_url = self._build_url(block.select_one(".cta-link"))

        return self._build_news_item(
            title=title,
            article_url=article_url,
            raw_date=raw_date,
            summary=summary,
            source=source,
            now=now,
        )

    def _parse_detail(
        self,
        article_url: str,
        source: SourceConfig,
        now: datetime,
    ) -> NewsItem | None:
        html = self._fetch_text(article_url)
        soup = BeautifulSoup(html, "lxml")

        title = self._clean_text(
            soup.select_one(".article-headline .field-pageheading, .article-headline .field-title, meta[property='og:title']")
        )
        if not title:
            og_title = soup.select_one("meta[property='og:title']")
            title = self._clean_text(og_title.get("content") if og_title else "")

        raw_date = self._clean_text(soup.select_one(".article-headline .field-publishdate"))
        summary = self._clean_text(soup.select_one(".article-headline .field-cardsummary"))
        if not summary:
            meta_desc = soup.select_one("meta[name='description']")
            summary = self._clean_text(meta_desc.get("content") if meta_desc else "")

        return self._build_news_item(
            title=title,
            article_url=article_url,
            raw_date=raw_date,
            summary=summary,
            source=source,
            now=now,
        )

    def _build_news_item(
        self,
        *,
        title: str,
        article_url: str,
        raw_date: str,
        summary: str,
        source: SourceConfig,
        now: datetime,
    ) -> NewsItem | None:
        if not title or not article_url:
            return None

        published_at = parse_datetime_text(
            raw_date,
            source.timezone,
            date_format_hint="%B %d, %Y",
        )
        date_parse_status = "parsed" if published_at is not None else "missing"
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _fetch_text(self, url: str) -> str:
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def _build_url(self, value: object) -> str:
        if isinstance(value, Tag):
            href = self._clean_text(value.get("href"))
        else:
            href = self._clean_text(value)
        if not href:
            return ""
        return urljoin("https://www.generalmills.com", href)

    def _clean_text(self, value: object) -> str:
        if isinstance(value, Tag):
            text = value.get_text(" ", strip=True)
        else:
            text = str(value or "")
        return re.sub(r"\s+", " ", text).strip()


class FerreroNewsParser(BaseNewsParser):
    SEARCH_ENDPOINT = "https://www.ferrero.com/api/int/search/_search"
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        payload = self._fetch_hits(limit=max_items)
        hits_container = payload.get("hits", {})
        if not isinstance(hits_container, dict):
            LOGGER.warning("Unexpected Ferrero response shape. source=%s", source.name)
            return []
        hits = hits_container.get("hits", [])
        if not isinstance(hits, list):
            return []

        items: list[NewsItem] = []
        seen_urls: set[str] = set()
        for hit in hits:
            parsed = self._build_item(hit, source, now)
            if parsed is None:
                continue
            url_text = str(parsed.url)
            if url_text in seen_urls:
                continue
            seen_urls.add(url_text)
            items.append(parsed)
            if len(items) >= max_items:
                break
        return items

    def _fetch_hits(self, *, limit: int) -> dict:
        payload = {
            "size": limit,
            "sort": [{"created": "desc"}],
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"article_type": "news"}},
                    ]
                }
            },
        }
        response = self.client.post(
            self.SEARCH_ENDPOINT,
            headers={"content-type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    def _build_item(self, hit: dict, source: SourceConfig, now: datetime) -> NewsItem | None:
        source_data = hit.get("_source") or {}
        if not isinstance(source_data, dict):
            return None

        title = self._extract_title(source_data)
        url = self._extract_url(source_data, source.base_url)
        summary = self._extract_summary(source_data)
        published_at = self._extract_published_at(source_data, source.timezone)
        if not title or not url or published_at is None:
            return None

        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        raw_date_text = self._first_string(source_data.get("date")) or self._first_string(source_data.get("created"))
        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _extract_url(self, source_data: dict, base_url: str) -> str:
        href = self._first_string(source_data.get("url"))
        return urljoin(str(base_url), href) if href else ""

    def _extract_title(self, source_data: dict) -> str:
        search_result_html = self._first_string(source_data.get("search_result"))
        if search_result_html:
            soup = BeautifulSoup(search_result_html, "lxml")
            title_node = soup.select_one(".search-result--title")
            if title_node is not None:
                title = self._clean_text(title_node.get_text(" ", strip=True))
                if title:
                    return title

        rendered_html = self._first_string(source_data.get("rendered_item"))
        if rendered_html:
            soup = BeautifulSoup(rendered_html, "lxml")
            title_node = soup.select_one(".node-article-teaser--title")
            if title_node is not None:
                title = self._clean_text(title_node.get_text(" ", strip=True))
                if title:
                    return title

        return self._first_string(source_data.get("field_title")) or self._first_string(source_data.get("title"))

    def _extract_summary(self, source_data: dict) -> str:
        search_result_html = self._first_string(source_data.get("search_result"))
        if search_result_html:
            soup = BeautifulSoup(search_result_html, "lxml")
            summary_node = soup.select_one(".search-result--text")
            if summary_node is not None:
                summary = self._clean_text(summary_node.get_text(" ", strip=True))
                if summary:
                    return summary

        summary = self._first_string(source_data.get("summary"))
        if summary:
            return summary

        body = self._first_string(source_data.get("body"))
        if body:
            soup = BeautifulSoup(body, "lxml")
            return self._clean_text(soup.get_text(" ", strip=True))
        return ""

    def _extract_published_at(self, source_data: dict, timezone_name: str) -> datetime | None:
        raw_date = self._first_string(source_data.get("date"))
        if raw_date:
            try:
                return datetime.fromtimestamp(int(raw_date), tz=timezone.utc).astimezone(ZoneInfo(timezone_name))
            except (TypeError, ValueError, OSError):
                pass

        rendered_html = self._first_string(source_data.get("rendered_item"))
        if rendered_html:
            soup = BeautifulSoup(rendered_html, "lxml")
            date_node = soup.select_one(".node-article-teaser--date")
            if date_node is not None:
                parsed = parse_datetime_text(
                    self._clean_text(date_node.get_text(" ", strip=True)),
                    timezone_name,
                    date_format_hint="%d %b %Y",
                )
                if parsed is not None:
                    return parsed
        return None

    def _first_string(self, value: object) -> str:
        if isinstance(value, list):
            for item in value:
                text = self._clean_text(item)
                if text:
                    return text
            return ""
        return self._clean_text(value)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class AsahiNewsroomParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []

        for list_url in source.list_urls:
            response = self.client.get(str(list_url))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            cards = soup.select("section.newsroom-list li.mod-newsList03")
            if not cards:
                LOGGER.warning("Asahi newsroom cards not found. url=%s", list_url)
                continue

            for card in cards:
                item = self._parse_card(card, source, now)
                if item is not None:
                    items.append(item)
                if len(items) >= max_items:
                    return items

        return items[:max_items]

    def _parse_card(self, card: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        link_node = card.select_one("a.mod-newsList03-a[href]")
        title_node = card.select_one(".rt_cf_n_title")
        date_node = card.select_one("time.rt_cf_n_date")
        if link_node is None or title_node is None or date_node is None:
            return None

        href = self._clean_text(link_node.get("href"))
        title = self._clean_text(title_node.get_text(" ", strip=True))
        raw_date_text = self._clean_text(date_node.get("datetime")) or self._clean_text(date_node.get_text(" ", strip=True))
        published_at = self._parse_dom_date(raw_date_text, source.timezone)
        if not href or not title or published_at is None:
            return None

        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        business = [
            self._clean_text(node.get_text(" ", strip=True))
            for node in card.select(".rt_cf_n_tags_business")
            if self._clean_text(node.get_text(" ", strip=True))
        ]
        categories = [
            self._clean_text(node.get_text(" ", strip=True))
            for node in card.select(".rt_cf_n_tags_category")
            if self._clean_text(node.get_text(" ", strip=True))
        ]
        summary_parts = []
        if business:
            summary_parts.append(" / ".join(dict.fromkeys(business)))
        if categories:
            summary_parts.append(" / ".join(dict.fromkeys(categories)))
        summary = " | ".join(summary_parts)

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            try:
                parsed = datetime.strptime(value, "%Y.%m.%d")
            except ValueError:
                return parse_datetime_text(value, timezone_name)
        return parsed.replace(tzinfo=ZoneInfo(timezone_name))


class AsahiBeerYearNewsParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []

        for list_url in source.list_urls:
            response = self.client.get(str(list_url))
            response.raise_for_status()
            html = response.content.decode("shift_jis", errors="ignore")
            soup = BeautifulSoup(html, "lxml")
            cards = soup.select("section.news_monthly .news_monthly_item")
            if not cards:
                LOGGER.warning("Asahi Beer yearly news cards not found. url=%s", list_url)
                continue

            for card in cards:
                item = self._parse_card(card, source, now)
                if item is not None:
                    items.append(item)
                if len(items) >= max_items:
                    return items

        return items[:max_items]

    def _parse_card(self, card: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        link_node = card.select_one("a[href]")
        date_node = card.select_one("time.news_monthly_item_date")
        category_node = card.select_one(".news_monthly_item_category span:last-child")
        title_node = card.select_one(".news_monthly_item_text p")
        if link_node is None or date_node is None or title_node is None:
            return None

        href = self._clean_text(link_node.get("href"))
        raw_date_text = self._clean_text(date_node.get_text(" ", strip=True))
        published_at = self._parse_dom_date(raw_date_text, source.timezone)
        title = self._clean_text(title_node.get_text(" ", strip=True))
        category = self._clean_text(category_node.get_text(" ", strip=True) if category_node is not None else "")

        if not href or not title or published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        return NewsItem(
            title=title,
            summary=category,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        try:
            parsed = datetime.strptime(value, "%Y年%m月%d日")
        except ValueError:
            return parse_datetime_text(value, timezone_name)
        return parsed.replace(tzinfo=ZoneInfo(timezone_name))


class AsahiRDReportParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            next_url: str | None = str(list_url)
            while next_url and len(items) < max_items:
                response = self.client.get(next_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "lxml")
                cards = soup.select("ul.newsroom-result-list li.newsroom-result-list-item.rt_bn_news_list")
                if not cards:
                    LOGGER.warning("Asahi R&D report cards not found. url=%s", next_url)
                    break

                oldest_in_page: datetime | None = None
                for card in cards:
                    item = self._parse_card(card, source, now)
                    if item is None:
                        continue
                    if item.published_at is not None:
                        oldest_in_page = item.published_at if oldest_in_page is None else min(oldest_in_page, item.published_at)
                    if str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break

                next_url = self._next_page_url(soup, source)

        return items[:max_items]

    def _parse_card(self, card: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        link_node = card.select_one("a.rt_cf_n_href_contents_report[href], a.mod-boxLink03[href]")
        title_node = card.select_one(".rt_cf_n_title")
        date_node = card.select_one("time.rt_cf_n_date")
        if link_node is None or title_node is None or date_node is None:
            return None

        href = self._clean_text(link_node.get("href"))
        title = self._clean_text(title_node.get_text(" ", strip=True))
        raw_date_text = self._clean_text(date_node.get("datetime")) or self._clean_text(date_node.get_text(" ", strip=True))
        published_at = self._parse_dom_date(raw_date_text, source.timezone)
        if not href or not title or published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        tags = [
            self._clean_text(node.get_text(" ", strip=True))
            for node in card.select(".mod-boxLink03-tag li, .mod-boxLink03-tag span")
            if self._clean_text(node.get_text(" ", strip=True))
        ]
        summary = " / ".join(dict.fromkeys(tags))

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _next_page_url(self, soup: BeautifulSoup, source: SourceConfig) -> str | None:
        next_node = soup.select_one("a.mod-paginate-next[href]")
        if next_node is None:
            return None

        href = self._clean_text(next_node.get("href"))
        if not href:
            return None
        return urljoin(str(source.base_url), href)

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            try:
                parsed = datetime.strptime(value, "%Y.%m.%d")
            except ValueError:
                return parse_datetime_text(value, timezone_name)
        return parsed.replace(tzinfo=ZoneInfo(timezone_name))

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class NissinNewsParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            response = self.client.get(str(list_url))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")

            initial_cards = soup.select(".ListCardNews .ListCardNews__item")
            if not initial_cards:
                LOGGER.warning("Nissin news cards not found. url=%s", list_url)
                continue

            oldest_initial: datetime | None = None
            for card in initial_cards:
                date_node = card.select_one(".CardNews__date")
                raw_date_text = self._clean_text(date_node.get_text(" ", strip=True) if date_node is not None else "")
                parsed_date = self._parse_dom_date(raw_date_text, source.timezone) if raw_date_text else None
                if parsed_date is not None:
                    oldest_initial = parsed_date if oldest_initial is None else min(oldest_initial, parsed_date)

                item = self._parse_dom_card(card, source, now)
                if item is None:
                    continue
                if str(item.url) in seen_urls:
                    continue
                seen_urls.add(str(item.url))
                items.append(item)
                if len(items) >= max_items:
                    return items[:max_items]

            next_page = self._extract_next_page_path(soup)
            if oldest_initial is not None and not is_in_yesterday_today_window(
                oldest_initial,
                now,
                source.timezone,
                window_days=source.window_days,
            ):
                continue

            while next_page and len(items) < max_items:
                api_url = urljoin(str(source.base_url), next_page)
                api_response = self.client.get(api_url)
                api_response.raise_for_status()
                payload = api_response.json()
                batch = payload.get("news") if isinstance(payload, dict) else None
                if not isinstance(batch, list) or not batch:
                    break

                oldest_batch: datetime | None = None
                for entry in batch:
                    raw_date_text = self._clean_text(entry.get("date")) if isinstance(entry, dict) else ""
                    parsed_date = self._parse_api_date(raw_date_text, source.timezone) if raw_date_text else None
                    if parsed_date is not None:
                        oldest_batch = parsed_date if oldest_batch is None else min(oldest_batch, parsed_date)

                    item = self._parse_api_entry(entry, source, now)
                    if item is None:
                        continue
                    if str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_batch is not None and not is_in_yesterday_today_window(
                    oldest_batch,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break
                next_page = self._clean_text(payload.get("next_page")) if isinstance(payload, dict) else ""

        return items[:max_items]

    def _parse_dom_card(self, card: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        link_node = card.select_one("a.CardNews__link[href]")
        title_node = card.select_one(".CardNews__title")
        date_node = card.select_one(".CardNews__date")
        group_node = card.select_one(".CardNews__group")
        if link_node is None or title_node is None or date_node is None:
            return None

        href = self._clean_text(link_node.get("href"))
        title = self._clean_text(title_node.get_text(" ", strip=True))
        raw_date_text = self._clean_text(date_node.get_text(" ", strip=True))
        published_at = self._parse_dom_date(raw_date_text, source.timezone)
        if not href or not title or published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        group = self._clean_text(group_node.get_text(" ", strip=True) if group_node is not None else "")
        return NewsItem(
            title=title,
            summary=group,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _parse_api_entry(self, entry: object, source: SourceConfig, now: datetime) -> NewsItem | None:
        if not isinstance(entry, dict):
            return None

        href = self._clean_text(entry.get("permalink"))
        title = self._clean_text(entry.get("title"))
        raw_date_text = self._clean_text(entry.get("date"))
        published_at = self._parse_api_date(raw_date_text, source.timezone)
        if not href or not title or published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        category = self._nested_text(entry, "category", "label")
        company = self._nested_text(entry, "company", "label")
        lead = self._clean_text(entry.get("lead"))
        summary_parts = [part for part in [company, category] if part]
        summary = " | ".join(summary_parts) if summary_parts else lead

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            content_preview=lead or None,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _extract_next_page_path(self, soup: BeautifulSoup) -> str | None:
        container = soup.select_one(".ListCardNews[data-news]")
        if container is None:
            return None
        value = self._clean_text(container.get("data-news"))
        return value or None

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        try:
            parsed = datetime.strptime(value, "%Y.%m.%d")
        except ValueError:
            return parse_datetime_text(value, timezone_name)
        return parsed.replace(tzinfo=ZoneInfo(timezone_name))

    def _parse_api_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        if not re.fullmatch(r"\d{12}", value):
            return parse_datetime_text(value, timezone_name)
        try:
            parsed = datetime.strptime(value, "%Y%m%d%H%M")
        except ValueError:
            return None
        return parsed.replace(tzinfo=ZoneInfo(timezone_name))

    def _nested_text(self, entry: dict[str, object], parent_key: str, child_key: str) -> str:
        parent = entry.get(parent_key)
        if not isinstance(parent, dict):
            return ""
        return self._clean_text(parent.get(child_key))

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class KirinNewsroomParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    CATEGORY_LABELS = {
        "01": "IR",
        "02": "CSV",
        "03": "商品・サービス",
        "04": "キャンペーン",
        "05": "研究・技術",
        "07": "人事",
        "08": "その他",
    }
    AREA_LABELS = {
        "01": "酒類",
        "02": "医薬",
        "03": "飲料・ヘルスサイエンス領域",
        "04": "その他",
    }
    COMPANY_LABELS = {
        "KH": "キリンホールディングス",
        "KB": "キリンビール",
        "KBC": "キリンビバレッジ",
        "ME": "メルシャン",
        "KHB": "協和発酵バイオ",
        "KKC": "協和キリン",
        "FNC": "ファンケル",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()
        years_to_fetch = [now.astimezone(ZoneInfo(source.timezone)).year, now.astimezone(ZoneInfo(source.timezone)).year - 1]

        for list_url in source.list_urls:
            xml_base = self._build_xml_base_url(str(list_url))
            stop_due_to_window = False
            for year in years_to_fetch:
                xml_url = urljoin(xml_base, f"news_{year}.xml")
                response = self.client.get(xml_url)
                response.raise_for_status()
                parsed_items = self._parse_xml(response.text, source, now)
                if not parsed_items:
                    continue

                for item in parsed_items:
                    if item.published_at is not None and not is_in_yesterday_today_window(
                        item.published_at,
                        now,
                        source.timezone,
                        window_days=source.window_days,
                    ):
                        stop_due_to_window = True
                        break
                    if str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if len(items) >= max_items:
                        return items[:max_items]

                if stop_due_to_window:
                    break

        return items[:max_items]

    def _parse_xml(self, xml_text: str, source: SourceConfig, now: datetime) -> list[NewsItem]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            LOGGER.warning("Kirin newsroom XML parse failed. source=%s", source.name)
            return []

        items: list[NewsItem] = []
        for item_node in root.findall("./item"):
            parsed = self._parse_item(item_node, source, now)
            if parsed is not None:
                items.append(parsed)
        items.sort(key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items

    def _parse_item(self, item_node: ET.Element, source: SourceConfig, now: datetime) -> NewsItem | None:
        raw_date_text = self._clean_text(self._child_text(item_node, "date"))
        title = self._clean_text(html_unescape(self._child_text(item_node, "title")))
        href = self._clean_text(self._child_text(item_node, "link"))
        published_at = self._parse_date(raw_date_text, source.timezone)
        if not raw_date_text or not title or not href or published_at is None:
            return None

        company_codes = [self._clean_text(node.text) for node in item_node.findall("./companies/company") if self._clean_text(node.text)]
        category_codes = [self._clean_text(node.text) for node in item_node.findall("./categories/category") if self._clean_text(node.text)]
        area_codes = [self._clean_text(node.text) for node in item_node.findall("./areas/area") if self._clean_text(node.text)]
        if not area_codes:
            single_area = self._clean_text(self._child_text(item_node, "area"))
            if single_area:
                area_codes = [single_area]
        filesize = self._clean_text(self._child_text(item_node, "filesize"))

        summary_parts = [
            self._join_labels(company_codes, self.COMPANY_LABELS),
            self._join_labels(category_codes, self.CATEGORY_LABELS),
            self._join_labels(area_codes, self.AREA_LABELS),
        ]
        if filesize:
            summary_parts.append(filesize)
        summary = " | ".join(part for part in summary_parts if part)

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _build_xml_base_url(self, list_url: str) -> str:
        parsed = urlsplit(list_url)
        path = parsed.path.rstrip("/")
        lang_prefix = "/jp" if path.startswith("/jp/") else "/en" if path.startswith("/en/") else ""
        return urlunsplit((parsed.scheme, parsed.netloc, f"{lang_prefix}/newsroom/release/inc/", "", ""))

    def _parse_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, fmt)
                return parsed.replace(tzinfo=ZoneInfo(timezone_name))
            except ValueError:
                continue
        return parse_datetime_text(value, timezone_name)

    def _child_text(self, parent: ET.Element, tag_name: str) -> str:
        child = parent.find(tag_name)
        return child.text if child is not None and child.text is not None else ""

    def _join_labels(self, codes: list[str], mapping: dict[str, str]) -> str:
        labels = [mapping.get(code, code) for code in codes if code]
        return " / ".join(dict.fromkeys(labels))

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class AjinomotoNewsroomParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            next_url: str | None = str(list_url)
            while next_url and len(items) < max_items:
                response = self.client.get(next_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "lxml")
                parsed_items = self._parse_page(soup, source, now)
                if parsed_items is None:
                    LOGGER.warning("Ajinomoto newsroom list not found. url=%s", next_url)
                    break
                if not parsed_items:
                    break

                oldest_in_page: datetime | None = None
                for item in parsed_items:
                    if item.published_at is not None:
                        oldest_in_page = item.published_at if oldest_in_page is None else min(oldest_in_page, item.published_at)
                    if str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break
                next_url = self._next_page_url(soup, source)

        return items[:max_items]

    def _parse_page(self, soup: BeautifulSoup, source: SourceConfig, now: datetime) -> list[NewsItem] | None:
        container = soup.select_one("#newsListContainer dl.news-list")
        if container is None:
            return None

        items: list[NewsItem] = []
        for meta in container.find_all("dt", class_="news-list__meta", recursive=False):
            title_node = meta.find_next_sibling("dd", class_="news-list__title")
            if title_node is None:
                continue

            item = self._parse_entry(meta, title_node, source, now)
            if item is not None:
                items.append(item)
        return items

    def _parse_entry(self, meta: Tag, title_node: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        date_node = meta.select_one(".news-list__date")
        link_node = title_node.select_one("a[href]")
        if date_node is None or link_node is None:
            return None

        raw_date_text = self._clean_text(date_node.get_text(" ", strip=True))
        href = self._clean_text(link_node.get("href"))
        title = self._clean_text(link_node.get_text(" ", strip=True))
        published_at = self._parse_dom_date(raw_date_text, source.timezone)
        if not raw_date_text or not href or not title or published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        category = self._clean_text(meta.select_one(".news-list__ctg") and meta.select_one(".news-list__ctg").get_text(" ", strip=True))
        genres = [
            self._clean_text(node.get_text(" ", strip=True))
            for node in meta.select(".news-list__genre li")
            if self._clean_text(node.get_text(" ", strip=True))
        ]
        summary_parts = [part for part in [category, " / ".join(genres)] if part]
        summary = " | ".join(summary_parts)

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _next_page_url(self, soup: BeautifulSoup, source: SourceConfig) -> str | None:
        next_node = soup.select_one(".news-list__pager li.pager--next a[href]")
        if next_node is None:
            return None
        href = self._clean_text(next_node.get("href"))
        if not href:
            return None
        return urljoin(str(source.base_url), href)

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        match = re.fullmatch(r"(\d{4})年(\d{2})月(\d{2})日", value)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return datetime(year, month, day, tzinfo=ZoneInfo(timezone_name))
        return parse_datetime_text(value, timezone_name)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class MeijiPressReleaseParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            response = self.client.get(str(list_url))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            cards = soup.select("div.l-list-container ul.l-list-line > li > a.l-card[href]")
            if not cards:
                LOGGER.warning("Meiji press release cards not found. url=%s", list_url)
                continue

            for card in cards:
                item = self._parse_card(card, source, now)
                if item is None:
                    continue
                if str(item.url) in seen_urls:
                    continue
                seen_urls.add(str(item.url))
                items.append(item)
                if len(items) >= max_items:
                    return items[:max_items]

        return items[:max_items]

    def _parse_card(self, card: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        href = self._clean_text(card.get("href"))
        date_node = card.select_one("time.m-txt-time")
        title_node = card.select_one("p.m-txtLink-block")
        category_node = card.select_one(".l-card-body .m-icon")
        if not href or date_node is None or title_node is None:
            return None

        raw_date_text = self._clean_text(date_node.get_text(" ", strip=True))
        title = self._clean_text(title_node.get_text(" ", strip=True))
        category = self._clean_text(category_node.get_text(" ", strip=True) if category_node is not None else "")
        published_at = self._parse_dom_date(raw_date_text, source.timezone)
        if not raw_date_text or not title or published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        return NewsItem(
            title=title,
            summary=category,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        try:
            parsed = datetime.strptime(value, "%Y/%m/%d")
        except ValueError:
            return parse_datetime_text(value, timezone_name)
        return parsed.replace(tzinfo=ZoneInfo(timezone_name))

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class MeijiRDTopicsParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            response = self.client.get(str(list_url))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            rows = soup.select("div.js-heading-accordion-body div.l-list-container ul.l-list-line > li")
            if not rows:
                LOGGER.warning("Meiji R&D topics not found. url=%s", list_url)
                continue

            for row in rows:
                item = self._parse_row(row, source, now)
                if item is None:
                    continue
                if str(item.url) in seen_urls:
                    continue
                seen_urls.add(str(item.url))
                items.append(item)
                if len(items) >= max_items:
                    return items[:max_items]

        return items[:max_items]

    def _parse_row(self, row: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        date_node = row.select_one("time.m-txt-time")
        link_node = row.select_one("a.m-txtLink[href]")
        if date_node is None or link_node is None:
            return None

        raw_date_text = self._clean_text(date_node.get_text(" ", strip=True))
        title = self._clean_text(link_node.get_text(" ", strip=True))
        href = self._clean_text(link_node.get("href"))
        published_at = self._parse_dom_date(raw_date_text, source.timezone)
        if not raw_date_text or not title or not href or published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        return NewsItem(
            title=title,
            summary="研究所からのお知らせ",
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        try:
            parsed = datetime.strptime(value, "%Y/%m/%d")
        except ValueError:
            return parse_datetime_text(value, timezone_name)
        return parsed.replace(tzinfo=ZoneInfo(timezone_name))

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class MegSnowNewsParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            response = self.client.get(str(list_url))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            cards = soup.select("#js-news-list li.p-news-item")
            if not cards:
                LOGGER.warning("Meg-Snow news cards not found. url=%s", list_url)
                continue

            for card in cards:
                item = self._parse_card(card, source, now)
                if item is None:
                    continue
                if str(item.url) in seen_urls:
                    continue
                seen_urls.add(str(item.url))
                items.append(item)
                if len(items) >= max_items:
                    return items[:max_items]

        return items[:max_items]

    def _parse_card(self, card: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        link_node = card.select_one("a.p-news-item__inner[href]")
        date_node = card.select_one(".p-news-item__date")
        title_node = card.select_one(".p-news-item__title")
        category_node = card.select_one(".c-tag span")
        if link_node is None or date_node is None or title_node is None:
            return None

        href = self._clean_text(link_node.get("href"))
        raw_date_text = self._clean_text(date_node.get_text(" ", strip=True))
        title = self._clean_text(title_node.get_text(" ", strip=True))
        category = self._clean_text(category_node.get_text(" ", strip=True) if category_node is not None else "")
        published_at = self._parse_dom_date(raw_date_text, source.timezone)
        if not href or not raw_date_text or not title or published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        return NewsItem(
            title=title,
            summary=category,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        match = re.fullmatch(r"(\d{4})年(\d{2})月(\d{2})日", value)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return datetime(year, month, day, tzinfo=ZoneInfo(timezone_name))
        return parse_datetime_text(value, timezone_name)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class YakultInformationParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            next_url: str | None = str(list_url)
            while next_url and len(items) < max_items:
                response = self.client.get(next_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "lxml")
                parsed_items = self._parse_page(soup, source, now)
                if parsed_items is None:
                    LOGGER.warning("Yakult information list not found. url=%s", next_url)
                    break
                if not parsed_items:
                    break

                oldest_in_page: datetime | None = None
                for item in parsed_items:
                    if item.published_at is not None:
                        oldest_in_page = item.published_at if oldest_in_page is None else min(oldest_in_page, item.published_at)
                    if str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break

                next_url = self._next_page_url(soup, source)

        return items[:max_items]

    def _parse_page(self, soup: BeautifulSoup, source: SourceConfig, now: datetime) -> list[NewsItem] | None:
        nodes = soup.select(".information-contents-wrap a.information-link")
        if not nodes:
            return None

        items: list[NewsItem] = []
        for node in nodes:
            item = self._parse_entry(node, source, now)
            if item is not None:
                items.append(item)
        return items

    def _parse_entry(self, node: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        href = self._clean_text(node.get("href"))
        date_text = self._clean_text(node.select_one(".information-date").get_text(" ", strip=True) if node.select_one(".information-date") else "")
        title = self._clean_text(node.select_one(".information-text").get_text(" ", strip=True) if node.select_one(".information-text") else "")
        if not href or not date_text or not title:
            return None

        published_at = self._parse_dom_date(date_text, source.timezone)
        if published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        return NewsItem(
            title=title,
            summary="お知らせ",
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=date_text,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _next_page_url(self, soup: BeautifulSoup, source: SourceConfig) -> str | None:
        next_node = soup.select_one(".pagination .pager-next a[href]")
        if next_node is None:
            return None
        href = self._clean_text(next_node.get("href"))
        if not href:
            return None
        return urljoin(str(source.base_url), href)

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        match = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", value)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return datetime(year, month, day, tzinfo=ZoneInfo(timezone_name))
        return parse_datetime_text(value, timezone_name)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class ItoEnReleaseParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            next_url: str | None = str(list_url)
            while next_url and len(items) < max_items:
                response = self.client.get(next_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "lxml")
                parsed_items = self._parse_page(soup, source, now)
                if parsed_items is None:
                    LOGGER.warning("Ito En release list not found. url=%s", next_url)
                    break
                if not parsed_items:
                    break

                oldest_in_page: datetime | None = None
                for item in parsed_items:
                    if item.published_at is not None:
                        oldest_in_page = item.published_at if oldest_in_page is None else min(oldest_in_page, item.published_at)
                    if str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break

                next_url = self._next_page_url(soup)

        return items[:max_items]

    def _parse_page(self, soup: BeautifulSoup, source: SourceConfig, now: datetime) -> list[NewsItem] | None:
        nodes = soup.select("a.p-newsListItemHaveThumb[href]")
        if not nodes:
            return None

        items: list[NewsItem] = []
        for node in nodes:
            item = self._parse_entry(node, source, now)
            if item is not None:
                items.append(item)
        return items

    def _parse_entry(self, node: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        href = self._clean_text(node.get("href"))
        title = self._clean_text(node.select_one("h3 .u-fontExtended").get_text(" ", strip=True) if node.select_one("h3 .u-fontExtended") else "")
        subtitle = self._clean_text(node.select_one(".p-newsListItemHaveThumb__subtitle").get_text(" ", strip=True) if node.select_one(".p-newsListItemHaveThumb__subtitle") else "")
        date_text = self._clean_text(node.select_one("time.p-newsListItemHaveThumb__pubDate").get("datetime") if node.select_one("time.p-newsListItemHaveThumb__pubDate") else "")
        category_nodes = node.select(".p-newsListItemHaveThumb__categoryIconList li")
        categories = " / ".join(self._clean_text(n.get_text(" ", strip=True)) for n in category_nodes if self._clean_text(n.get_text(" ", strip=True)))
        if not href or not title or not date_text:
            return None

        published_at = self._parse_dom_date(date_text, source.timezone)
        if published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        summary = categories
        if subtitle:
            summary = f"{categories} | {subtitle}" if categories else subtitle

        return NewsItem(
            title=title,
            summary=summary or None,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=date_text,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _next_page_url(self, soup: BeautifulSoup) -> str | None:
        next_node = soup.select_one(".wp-pagenavi a.nextpostslink[href]")
        if next_node is None:
            return None
        href = self._clean_text(next_node.get("href"))
        return href or None

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        match = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", value)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return datetime(year, month, day, tzinfo=ZoneInfo(timezone_name))
        return parse_datetime_text(value, timezone_name)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class LotteChilsungNewsParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            page_index = 1
            while len(items) < max_items:
                page_url = self._build_page_url(str(list_url), page_index)
                soup = self._fetch_soup(page_url)
                nodes = soup.select(".listWrap > a.list[href]")
                if not nodes:
                    if page_index == 1:
                        LOGGER.warning("Lotte Chilsung news list not found. url=%s", page_url)
                    break

                page_items: list[NewsItem] = []
                oldest_in_page: datetime | None = None
                for node in nodes:
                    item = self._parse_entry(node, page_url, source, now)
                    if item is None:
                        continue
                    page_items.append(item)
                    if item.published_at is not None:
                        oldest_in_page = item.published_at if oldest_in_page is None else min(oldest_in_page, item.published_at)

                if not page_items:
                    break

                for item in page_items:
                    if str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break

                next_page = self._extract_next_page(soup)
                if next_page is None or next_page <= page_index:
                    break
                page_index = next_page

        return items[:max_items]

    def _build_page_url(self, base_url: str, page_index: int) -> str:
        if page_index <= 1:
            return base_url
        parsed = urlsplit(base_url)
        params = []
        existing = dict()
        if parsed.query:
            for part in parsed.query.split("&"):
                if "=" in part:
                    key, value = part.split("=", 1)
                    existing[key] = value
        existing["pageIndex"] = str(page_index)
        query = urlencode(existing)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))

    def _fetch_soup(self, url: str) -> BeautifulSoup:
        try:
            response = self.client.get(url)
            response.raise_for_status()
            return BeautifulSoup(response.text, "lxml")
        except Exception as exc:
            LOGGER.warning("Lotte Chilsung httpx fetch failed, retrying with curl. url=%s error=%s", url, exc)

        result = subprocess.run(
            [
                "curl",
                "-L",
                "--max-time",
                str(int(max(5, self.settings.request_timeout_seconds))),
                "-A",
                self.BROWSER_USER_AGENT,
                url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            stderr = result.stderr.strip()
            raise RuntimeError(f"curl fallback failed for {url}: {stderr or f'exit {result.returncode}'}")
        return BeautifulSoup(result.stdout, "lxml")

    def _parse_entry(self, node: Tag, current_url: str, source: SourceConfig, now: datetime) -> NewsItem | None:
        href = self._clean_text(node.get("href"))
        title = self._clean_text(node.select_one(".txtArea .tit").get_text(" ", strip=True) if node.select_one(".txtArea .tit") else "")
        date_text = self._clean_text(node.select_one(".listDate").get_text(" ", strip=True) if node.select_one(".listDate") else "")
        summary = self._clean_text(node.select_one(".img img").get("alt") if node.select_one(".img img") else "")
        if not href or not title or not date_text:
            return None

        published_at = parse_datetime_text(date_text, timezone_name=source.timezone)
        if published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        return NewsItem(
            title=html_unescape(title),
            summary=summary or "뉴스/언론보도",
            published_at=published_at,
            url=urljoin(current_url, href),
            source=source.name,
            collected_at=now,
            raw_date_text=date_text,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _extract_next_page(self, soup: BeautifulSoup) -> int | None:
        next_button = soup.select_one(".btnArea a.roundBtn.wht[data-page]")
        if not next_button:
            return None
        value = self._clean_text(next_button.get("data-page"))
        if value.isdigit():
            return int(value) + 1
        return None

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class FamilyMartNewsReleaseParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []

        for list_url in source.list_urls:
            response = self.client.get(str(list_url))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            nodes = soup.select("section.ly-wrp-list-month article.row")
            if not nodes:
                LOGGER.warning("FamilyMart news release list not found. url=%s", list_url)
                continue

            for node in nodes:
                item = self._parse_entry(node, source, now)
                if item is not None:
                    items.append(item)
                if len(items) >= max_items:
                    return items[:max_items]

        return items[:max_items]

    def _parse_entry(self, node: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        link_node = node.select_one("p.ly-txt-tit a[href]")
        date_node = node.select_one(".ly-ttl-area_row .ly-txt-date")
        category_node = node.select_one(".ly-ttl-area_row [class^='ly-icn-']")
        image_node = node.select_one("a[href] img.thumb_row")

        href = self._clean_text(link_node.get("href") if link_node else "")
        title = self._clean_text(link_node.get_text(" ", strip=True) if link_node else "")
        date_text = self._clean_text(date_node.get_text(" ", strip=True) if date_node else "")
        category = self._clean_text(category_node.get_text(" ", strip=True) if category_node else "")
        image_alt = self._clean_text(image_node.get("alt") if image_node else "")

        if not href or not title or not date_text:
            return None

        published_at = parse_datetime_text(date_text, timezone_name=source.timezone)
        if published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        return NewsItem(
            title=title,
            summary=category or image_alt or "ニュースリリース",
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=date_text,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class SevenElevenJapanNewsReleaseParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            response = self.client.get(str(list_url))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            sections = soup.select("div.section")
            if not sections:
                LOGGER.warning("Seven-Eleven Japan news release sections not found. url=%s", list_url)
                continue

            for section in sections:
                for row in section.select("ul.list-news > li"):
                    item = self._parse_row(row, source, now)
                    if item is None:
                        continue
                    if str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if len(items) >= max_items:
                        return items[:max_items]

        return items[:max_items]

    def _parse_row(self, row: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        date_node = row.select_one("p.date")
        link_node = row.select_one("a.link-txt[href]")
        if date_node is None or link_node is None:
            return None

        date_text = self._clean_text(date_node.get_text(" ", strip=True))
        raw_date_text = date_text.split(" ")[0]
        href = self._clean_text(link_node.get("href"))
        title = self._clean_text(link_node.get_text(" ", strip=True))
        categories = " / ".join(
            category
            for node in row.select("p.date span[class^='label--']")
            if (category := self._clean_text(node.get_text(" ", strip=True)))
        )
        if not href or not title or not raw_date_text:
            return None

        published_at = self._parse_dom_date(raw_date_text, source.timezone)
        if published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        return NewsItem(
            title=title,
            summary=categories or "ニュースリリース",
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        match = re.fullmatch(r"(\d{4})/(\d{2})/(\d{2})", value)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return datetime(year, month, day, tzinfo=ZoneInfo(timezone_name))
        return parse_datetime_text(value, timezone_name)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class LawsonNewsReleaseParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            response = self.client.get(str(list_url))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            nodes = [
                row
                for row in soup.select("table.newsTable tr")
                if row.select_one("td.icon_cate") is not None and row.select_one("a[href]") is not None
            ]
            if not nodes:
                LOGGER.warning("Lawson news release list not found. url=%s", list_url)
                continue

            for node in nodes:
                item = self._parse_entry(node, source, now)
                if item is not None:
                    if str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                if len(items) >= max_items:
                    return items[:max_items]

        return items[:max_items]

    def _parse_entry(self, node: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        date_node = node.select_one("th span")
        link_node = node.select_one("td:last-of-type a[href]")
        category_icons = node.select("td.icon_cate img[alt]")

        href = self._clean_text(link_node.get("href") if link_node else "")
        title = self._clean_text(link_node.get_text(" ", strip=True) if link_node else "")
        date_text = self._clean_text(date_node.get_text(" ", strip=True) if date_node else "")
        category = " / ".join(
            alt
            for icon in category_icons
            if (alt := self._clean_text(icon.get("alt")))
        )

        if not href or not title or not date_text:
            return None

        published_at = self._parse_dom_date(date_text, source.timezone)
        if published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        return NewsItem(
            title=title,
            summary=category or "ニュースリリース",
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=date_text,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", value)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return datetime(year, month, day, tzinfo=ZoneInfo(timezone_name))
        return parse_datetime_text(value, timezone_name)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class CJKoreaNewsroomParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            next_url: str | None = str(list_url)
            while next_url and len(items) < max_items:
                soup = self._fetch_soup(next_url)
                if soup is None:
                    LOGGER.warning("CJ Korea newsroom fetch failed. url=%s", next_url)
                    break
                parsed_items = self._parse_page(soup, source, now)
                if parsed_items is None:
                    LOGGER.warning("CJ Korea newsroom list not found. url=%s", next_url)
                    break
                if not parsed_items:
                    break

                oldest_in_page: datetime | None = None
                for item in parsed_items:
                    if item.published_at is not None:
                        oldest_in_page = item.published_at if oldest_in_page is None else min(oldest_in_page, item.published_at)
                    if str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break

                next_url = self._next_page_url(soup, next_url)

        return items[:max_items]

    def _fetch_soup(self, url: str) -> BeautifulSoup | None:
        response = self.client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        if self._has_supported_nodes(soup):
            return soup

        try:
            html = fetch_html_with_playwright(
                url,
                timeout_seconds=self.settings.request_timeout_seconds,
                user_agent=self.BROWSER_USER_AGENT,
                locale="ko-KR",
                wait_selector=".grid.bbs-news-list .grid.list .item, .storyList .newsroom-grid.newsroom-stories .item, .newsroom-grid.newsroom-press .item, .newsroom-overview-release .newsroom-grid.newsroom-release .item > a[href]",
            )
        except Exception as exc:  # pragma: no cover - curl fallback is environment-specific
            LOGGER.warning("CJ Korea browser fallback failed. url=%s error=%s", url, exc)
            curl_soup = self._fetch_curl_soup(url)
            return curl_soup or soup

        browser_soup = BeautifulSoup(html, "lxml")
        if self._has_supported_nodes(browser_soup):
            return browser_soup

        curl_soup = self._fetch_curl_soup(url)
        return curl_soup or browser_soup or soup

    def _fetch_curl_soup(self, url: str) -> BeautifulSoup | None:
        result = subprocess.run(
            [
                "curl",
                "-L",
                "--max-time",
                str(int(max(5, self.settings.request_timeout_seconds))),
                "-A",
                self.BROWSER_USER_AGENT,
                url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            stderr = result.stderr.strip()
            LOGGER.warning(
                "CJ Korea curl fallback failed. url=%s error=%s",
                url,
                stderr or f"exit {result.returncode}",
            )
            return None

        soup = BeautifulSoup(result.stdout, "lxml")
        if self._has_supported_nodes(soup):
            return soup
        return None

    def _parse_page(self, soup: BeautifulSoup, source: SourceConfig, now: datetime) -> list[NewsItem] | None:
        nodes, page_type = self._select_nodes(soup)
        if not nodes or page_type is None:
            return None

        items: list[NewsItem] = []
        for node in nodes:
            item = self._parse_entry(node, page_type, source, now)
            if item is not None:
                items.append(item)
        return items

    def _parse_entry(self, node: Tag, page_type: str, source: SourceConfig, now: datetime) -> NewsItem | None:
        if page_type == "overview":
            link_node = node
            href = self._clean_text(link_node.get("href"))
            title = self._clean_text(link_node.select_one("h2.name").get_text(" ", strip=True) if link_node.select_one("h2.name") else "")
            date_text = self._clean_text(link_node.select_one("p.date").get_text(" ", strip=True) if link_node.select_one("p.date") else "")
            summary = self._clean_text(link_node.select_one(".background img").get("alt") if link_node.select_one(".background img") else "") or "보도자료"
        elif page_type == "pressreleases":
            link_node = node.select_one("a.anchor[href]")
            href = self._clean_text(link_node.get("href") if link_node else "")
            title = self._clean_text(link_node.select_one("h2.name").get_text(" ", strip=True) if link_node and link_node.select_one("h2.name") else "")
            date_text = self._clean_text(link_node.select_one("p.date").get_text(" ", strip=True) if link_node and link_node.select_one("p.date") else "")
            summary = self._clean_text(node.select_one(".background img").get("alt") if node.select_one(".background img") else "") or "보도자료"
        elif page_type == "stories":
            link_node = node.select_one("a[href]")
            href = self._clean_text(link_node.get("href") if link_node else "")
            title = self._clean_text(link_node.select_one("h2.name").get_text(" ", strip=True) if link_node and link_node.select_one("h2.name") else "")
            date_text = self._clean_text(link_node.select_one("p.date").get_text(" ", strip=True) if link_node and link_node.select_one("p.date") else "")
            categories = [self._clean_text(tag.get_text(" ", strip=True)) for tag in node.select(".category span")]
            summary = ", ".join([value for value in categories if value]) or self._clean_text(link_node.get("alt") if link_node else "") or "기획칼럼"
        elif page_type == "inthemedia":
            link_node = node.select_one("a[href]")
            href = self._clean_text(link_node.get("href") if link_node else "")
            title = self._clean_text(link_node.select_one("h2.name").get_text(" ", strip=True) if link_node and link_node.select_one("h2.name") else "")
            date_text = self._clean_text(link_node.select_one("p.date").get_text(" ", strip=True) if link_node and link_node.select_one("p.date") else "")
            summary = self._clean_text(link_node.select_one("p.media").get_text(" ", strip=True) if link_node and link_node.select_one("p.media") else "") or "언론보도"
        else:
            return None

        if not href or not date_text or not title:
            return None

        published_at = self._parse_dom_date(date_text, source.timezone)
        if published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=date_text,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _next_page_url(self, soup: BeautifulSoup, current_url: str) -> str | None:
        next_node = soup.select_one('.section-pagination a.nav.next[href]:not(.disabled)')
        if not next_node:
            return None
        href = self._clean_text(next_node.get("href"))
        if not href or href == "#" or href.startswith("javascript:"):
            return None
        return urljoin(current_url, href)

    def _has_supported_nodes(self, soup: BeautifulSoup) -> bool:
        nodes, page_type = self._select_nodes(soup)
        return bool(nodes) and page_type is not None

    def _select_nodes(self, soup: BeautifulSoup) -> tuple[list[Tag], str | None]:
        selectors = [
            (".grid.bbs-news-list .grid.list > .item", "pressreleases"),
            (".storyList .newsroom-grid.newsroom-stories > .item", "stories"),
            (".newsroom-grid.newsroom-press > #news-all > .item, .newsroom-grid.newsroom-press > .item", "inthemedia"),
            (".newsroom-overview-release .newsroom-grid.newsroom-release .item > a[href]", "overview"),
        ]
        for selector, page_type in selectors:
            nodes = soup.select(selector)
            if nodes:
                return nodes, page_type
        return [], None

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        match = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", value)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return datetime(year, month, day, tzinfo=ZoneInfo(timezone_name))
        return parse_datetime_text(value, timezone_name)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class MorinagaMilkReleaseParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            next_url: str | None = str(list_url)
            while next_url and len(items) < max_items:
                response = self.client.get(next_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "lxml")
                parsed_items = self._parse_page(soup, source, now)
                if parsed_items is None:
                    LOGGER.warning("Morinaga Milk release list not found. url=%s", next_url)
                    break
                if not parsed_items:
                    break

                oldest_in_page: datetime | None = None
                for item in parsed_items:
                    if item.published_at is not None:
                        oldest_in_page = item.published_at if oldest_in_page is None else min(oldest_in_page, item.published_at)
                    if str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break

                next_url = self._next_page_url(soup, source)

        return items[:max_items]

    def _parse_page(self, soup: BeautifulSoup, source: SourceConfig, now: datetime) -> list[NewsItem] | None:
        container = soup.select_one("dl.news-list")
        if container is None:
            return None

        items: list[NewsItem] = []
        for meta in container.find_all("dt", recursive=False):
            body = meta.find_next_sibling("dd")
            if body is None:
                continue
            item = self._parse_entry(meta, body, source, now)
            if item is not None:
                items.append(item)
        return items

    def _parse_entry(self, meta: Tag, body: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        link_node = body.select_one("a[href]")
        if link_node is None:
            return None

        meta_text = self._clean_text(meta.get_text(" ", strip=True))
        match = re.match(r"^(\d{4}年\d{2}月\d{2}日)\s*(.*)$", meta_text)
        if not match:
            return None
        raw_date_text = self._clean_text(match.group(1))
        category = self._clean_text(match.group(2))
        title = self._clean_text(link_node.get_text(" ", strip=True))
        href = self._clean_text(link_node.get("href"))
        published_at = self._parse_dom_date(raw_date_text, source.timezone)
        if not raw_date_text or not title or not href or published_at is None:
            return None
        if not is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        ):
            return None

        return NewsItem(
            title=title,
            summary=category,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status="parsed",
            date_in_scope=True,
        )

    def _next_page_url(self, soup: BeautifulSoup, source: SourceConfig) -> str | None:
        next_node = soup.select_one(".pagerWrapper .pager li:last-child a.pager-link[href]")
        if next_node is None:
            return None
        href = self._clean_text(next_node.get("href"))
        if not href:
            return None
        return urljoin(str(source.base_url), href)

    def _parse_dom_date(self, raw_date_text: str, timezone_name: str) -> datetime | None:
        value = self._clean_text(raw_date_text)
        match = re.fullmatch(r"(\d{4})年(\d{2})月(\d{2})日", value)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return datetime(year, month, day, tzinfo=ZoneInfo(timezone_name))
        return parse_datetime_text(value, timezone_name)

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class SuntoryNewsListParser(BaseNewsParser):
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.BROWSER_USER_AGENT},
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        queue = [str(url) for url in source.list_urls]
        visited: set[str] = set()
        seen_urls: set[str] = set()
        items: list[NewsItem] = []

        while queue and len(items) < max_items:
            current_url = queue.pop(0)
            if current_url in visited:
                continue
            visited.add(current_url)

            try:
                if self._should_use_browser_fallback(source):
                    html = self._fetch_text_with_browser(current_url, source)
                else:
                    html = self._fetch_text(current_url)
            except Exception:
                LOGGER.warning("Failed to fetch Suntory list page. url=%s", current_url)
                continue

            soup = BeautifulSoup(html, "lxml")
            found_in_scope = False
            oldest_in_page: datetime | None = None

            grouped_articles = self._extract_grouped_articles(soup, source, now)
            card_articles = self._extract_card_articles(soup, source, now)

            for parsed_item in grouped_articles + card_articles:
                if parsed_item.published_at is not None:
                    if oldest_in_page is None or parsed_item.published_at < oldest_in_page:
                        oldest_in_page = parsed_item.published_at
                item_url = str(parsed_item.url)
                if item_url in seen_urls:
                    continue
                seen_urls.add(item_url)
                items.append(parsed_item)
                found_in_scope = True
                if len(items) >= max_items:
                    break

            if len(items) >= max_items:
                break

            if (
                oldest_in_page is not None
                and is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                )
                is False
                and not found_in_scope
            ):
                continue

            for next_url in self._extract_pagination_urls(soup, source.base_url):
                if next_url not in visited and next_url not in queue:
                    queue.append(next_url)

        return items[:max_items]

    def _fetch_text(self, url: str) -> str:
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def _fetch_text_with_browser(self, url: str, source: SourceConfig) -> str:
        wait_selector = self._browser_wait_selector(source)
        return fetch_html_with_playwright(
            url,
            timeout_seconds=self.settings.request_timeout_seconds * 2,
            user_agent=self.BROWSER_USER_AGENT,
            locale="ja-JP" if ".co.jp" in str(source.base_url) else "en-US",
            wait_selector=wait_selector,
        )

    def _extract_grouped_articles(
        self,
        soup: BeautifulSoup,
        source: SourceConfig,
        now: datetime,
    ) -> list[NewsItem]:
        items: list[NewsItem] = []
        for group in soup.select(".listGroup"):
            raw_date_text = self._extract_group_date(group)
            if not raw_date_text:
                continue
            published_at = parse_datetime_text(
                raw_date_text,
                source.timezone,
                date_format_hint="%B %d, %Y",
            )
            for article in group.select("article"):
                item = self._parse_group_article(article, raw_date_text, published_at, source, now)
                if item is not None:
                    items.append(item)
        return items

    def _extract_card_articles(
        self,
        soup: BeautifulSoup,
        source: SourceConfig,
        now: datetime,
    ) -> list[NewsItem]:
        items: list[NewsItem] = []
        for article in soup.select("#search_body article.article01"):
            item = self._parse_card_article(article, source, now)
            if item is not None:
                items.append(item)
        return items

    def _extract_group_date(self, group: Tag) -> str:
        heading = group.find("h3")
        if heading is None:
            return ""
        return self._clean_text(heading.get_text(" ", strip=True))

    def _parse_group_article(
        self,
        article: Tag,
        raw_date_text: str,
        published_at: datetime | None,
        source: SourceConfig,
        now: datetime,
    ) -> NewsItem | None:
        title_node = article.select_one(".artiBody a.title")
        if title_node is None:
            return None
        title = self._clean_text(title_node.get_text(" ", strip=True))
        href = self._clean_text(title_node.get("href"))
        if not title or not href:
            return None

        if published_at is None:
            if not self.settings.include_items_without_parsed_date:
                return None
            date_in_scope = None
            date_parse_status = "missing"
        else:
            date_in_scope = is_in_yesterday_today_window(
                published_at,
                now,
                source.timezone,
                window_days=source.window_days,
            )
            date_parse_status = "parsed"
            if date_in_scope is False:
                return None

        categories = [
            self._clean_text(node.get_text(" ", strip=True))
            for node in article.select(".artiBody ul.tag li a")
            if self._clean_text(node.get_text(" ", strip=True))
        ]
        read_text = self._clean_text(
            article.select_one(".artiBody p.read").get_text(" ", strip=True)
            if article.select_one(".artiBody p.read") is not None
            else ""
        )
        summary_parts = []
        if categories:
            summary_parts.append(" / ".join(categories))
        if read_text:
            summary_parts.append(read_text)
        summary = " | ".join(summary_parts)

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _parse_card_article(
        self,
        article: Tag,
        source: SourceConfig,
        now: datetime,
    ) -> NewsItem | None:
        title_node = article.select_one(".article01_heading a")
        if title_node is None:
            return None
        title = self._clean_text(title_node.get_text(" ", strip=True))
        href = self._clean_text(title_node.get("href"))
        if not title or not href:
            return None

        time_node = article.select_one(".article01_meta .date time")
        raw_date_text = self._clean_text(time_node.get_text(" ", strip=True) if time_node is not None else "")
        published_at = parse_datetime_text(raw_date_text, source.timezone)
        if published_at is None:
            if not self.settings.include_items_without_parsed_date:
                return None
            date_in_scope = None
            date_parse_status = "missing"
        else:
            date_in_scope = is_in_yesterday_today_window(
                published_at,
                now,
                source.timezone,
                window_days=source.window_days,
            )
            date_parse_status = "parsed"
            if date_in_scope is False:
                return None

        categories = [
            self._clean_text(node.get_text(" ", strip=True))
            for node in article.select(".article01_meta .category a")
            if self._clean_text(node.get_text(" ", strip=True))
        ]
        summary_text = self._clean_text(
            article.select_one(".article01_text").get_text(" ", strip=True)
            if article.select_one(".article01_text") is not None
            else ""
        )
        summary_parts = []
        if categories:
            summary_parts.append(" / ".join(categories))
        if summary_text:
            summary_parts.append(summary_text)
        summary = " | ".join(summary_parts)

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=urljoin(str(source.base_url), href),
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _extract_pagination_urls(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        urls: list[str] = []
        for node in soup.select(".pageNav a[href]"):
            href = self._clean_text(node.get("href"))
            if not href:
                continue
            urls.append(urljoin(str(base_url), href))
        return urls

    def _should_use_browser_fallback(self, source: SourceConfig) -> bool:
        mode = source.query_params.get("fetch_mode", "").strip().lower()
        return mode == "browser"

    def _browser_wait_selector(self, source: SourceConfig) -> str | None:
        wait_selector = source.query_params.get("browser_wait_selector", "").strip()
        return wait_selector or None

    def _looks_like_access_denied(self, html: str) -> bool:
        normalized = html.casefold()
        return "access denied" in normalized and "errors.edgesuite.net" in normalized

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class PRNasiaIndustryParser(BaseNewsParser):
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
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            current_url = str(list_url)
            for _ in range(12):
                response = self.client.get(current_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "lxml")
                cards = soup.select(".section-content.card-short-wrap.width-large .card")
                if not cards:
                    LOGGER.warning("PRNasia cards not found. url=%s", current_url)
                    break

                oldest_in_page: datetime | None = None
                for card in cards:
                    item = self._parse_card(card, source, now)
                    if item is None or str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if item.published_at is not None:
                        oldest_in_page = (
                            item.published_at
                            if oldest_in_page is None
                            else min(oldest_in_page, item.published_at)
                        )
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break

                next_link = soup.select_one(".prcenterpage a.nextPage[href]")
                if not next_link:
                    break
                next_href = (next_link.get("href") or "").strip()
                if not next_href:
                    break
                current_url = urljoin(current_url, next_href)

        return items[:max_items]

    def _parse_card(self, card: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        title_node = card.select_one("h3 a[href]")
        if not title_node:
            return None

        article_url = urljoin(str(source.base_url), title_node.get("href", "").strip())
        title = self._clean_text(title_node.get_text(" ", strip=True))
        summary_node = card.select_one(".card-text-summary")
        summary = self._clean_text(summary_node.get_text(" ", strip=True) if summary_node else "")
        date_node = card.select_one(".card-text-info .datetime")
        raw_date_text = self._clean_text(date_node.get_text(" ", strip=True) if date_node else "")
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if not title or not article_url:
            return None
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _clean_text(self, value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip()


class SSNPNewsParser(BaseNewsParser):
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
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            current_url = str(list_url)
            for page_number in range(1, 11):
                if page_number > 1:
                    current_url = urljoin(str(list_url), f"page/{page_number}/")

                response = self.client.get(current_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "lxml")
                cards = soup.select("article.news-article-item")
                if not cards:
                    LOGGER.warning("SSNP cards not found. url=%s", current_url)
                    break

                oldest_in_page: datetime | None = None
                for card in cards:
                    item = self._parse_card(card, source, now)
                    if item is None or str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if item.published_at is not None:
                        oldest_in_page = (
                            item.published_at
                            if oldest_in_page is None
                            else min(oldest_in_page, item.published_at)
                        )
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break

                if not soup.select_one(f'a[href*="/news/page/{page_number + 1}/"]'):
                    break

        return items[:max_items]

    def _parse_card(self, card: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        link_node = card.select_one("a[href]")
        if not link_node:
            return None

        article_url = urljoin(str(source.base_url), (link_node.get("href") or "").strip())
        title = self._extract_title(card)
        summary = self._extract_summary(card)
        raw_date_text = self._extract_date(card)
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if not title or not article_url:
            return None
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _extract_title(self, card: Tag) -> str:
        for selector in ["h2", "h3", ".entry-title", ".title"]:
            node = card.select_one(selector)
            if node:
                text = self._clean_text(node.get_text(" ", strip=True))
                if text:
                    return text

        text = self._clean_text(card.get_text(" ", strip=True))
        match = re.search(r"\d{4}年\d{1,2}月\d{1,2}日\s+(.+?)(?:\s+（続きを見る）|\s*$)", text)
        return self._clean_text(match.group(1)) if match else ""

    def _extract_summary(self, card: Tag) -> str:
        for selector in [".news-text", ".excerpt", "p"]:
            node = card.select_one(selector)
            if node:
                text = self._clean_text(node.get_text(" ", strip=True))
                text = text.replace("（続きを見る）", "").strip()
                if text:
                    return text
        return ""

    def _extract_date(self, card: Tag) -> str:
        text = self._clean_text(card.get_text(" ", strip=True))
        match = re.search(r"(20\d{2}年\d{1,2}月\d{1,2}日)", text)
        return match.group(1) if match else ""

    def _clean_text(self, value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip()


class PedailyNewsflashParser(BaseNewsParser):
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
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            current_url = str(list_url)
            for page_number in range(1, 11):
                if page_number > 1:
                    current_url = urljoin(str(list_url), f"{page_number}/")

                response = self.client.get(current_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "lxml")
                container = soup.select_one("#firstnews-list")
                if not container:
                    LOGGER.warning("Pedaily list container not found. url=%s", current_url)
                    break

                entries = container.select("li[data-url]")
                if not entries:
                    break

                oldest_in_page: datetime | None = None
                for entry in entries:
                    item = self._parse_entry(entry, source, now)
                    if item is None or str(item.url) in seen_urls:
                        continue
                    seen_urls.add(str(item.url))
                    items.append(item)
                    if item.published_at is not None:
                        oldest_in_page = (
                            item.published_at
                            if oldest_in_page is None
                            else min(oldest_in_page, item.published_at)
                        )
                    if len(items) >= max_items:
                        return items[:max_items]

                if oldest_in_page is not None and not is_in_yesterday_today_window(
                    oldest_in_page,
                    now,
                    source.timezone,
                    window_days=source.window_days,
                ):
                    break

                if not soup.select_one(f'a[href="https://www.pedaily.cn/first/{page_number + 1}/"], a[href="/first/{page_number + 1}/"]'):
                    break

        return items[:max_items]

    def _parse_entry(self, entry: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        raw_title = self._clean_text(entry.get("data-title"))
        detail_url = self._clean_text(entry.get("data-url"))
        title_link = entry.select_one("h3 a[href]")
        article_url = title_link.get("href", "").strip() if title_link else detail_url
        title = raw_title or self._clean_text(title_link.get_text(" ", strip=True) if title_link else "")
        summary_node = entry.select_one(".desc .txt")
        summary = self._clean_text(summary_node.get_text(" ", strip=True) if summary_node else "")
        date_node = entry.select_one(".info .time.date")
        raw_date_text = self._clean_text(date_node.get_text(" ", strip=True) if date_node else "")
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if not title or not article_url:
            return None
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _clean_text(self, value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip()


class PRTimesGourmetParser(BaseNewsParser):
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
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            response = self.client.get(str(list_url))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            entries = soup.select("article.list-article")
            if not entries:
                LOGGER.warning("PR TIMES gourmet entries not found. url=%s", list_url)
                continue

            for entry in entries:
                item = self._parse_entry(entry, source, now)
                if item is None or str(item.url) in seen_urls:
                    continue
                seen_urls.add(str(item.url))
                items.append(item)
                if len(items) >= max_items:
                    return items[:max_items]

        return items[:max_items]

    def _parse_entry(self, entry: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        link_node = entry.select_one("a.list-article__link[href]")
        if not link_node:
            return None

        article_url = urljoin(str(source.base_url), (link_node.get("href") or "").strip())
        title_node = entry.select_one(".list-article__title")
        title = self._clean_text(title_node.get_text(" ", strip=True) if title_node else "")
        company_node = entry.select_one(".list-article__company-name-link, .list-article__company-name--dummy")
        company = self._clean_text(company_node.get_text(" ", strip=True) if company_node else "")
        summary = company
        time_node = entry.select_one("time.list-article__time")
        raw_date_text = (time_node.get("datetime") or "").strip() if time_node else ""
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if not title or not article_url:
            return None
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _clean_text(self, value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip()


class ThirtySixKrWebNewsParser(BaseNewsParser):
    API_URL = "https://gateway.36kr.com/api/mis/nav/ifm/subNav/flow"
    SUMMARY_PREFIX = "【程序生成摘要】"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json; charset=utf-8",
                "Origin": "https://36kr.com",
                "Referer": "https://36kr.com/information/web_news/",
            },
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_item_ids: set[int] = set()
        page_callback: str | None = None
        page_event = 0

        while len(items) < max_items:
            payload = self._build_payload(page_event=page_event, page_callback=page_callback)
            response = self.client.post(self.API_URL, json=payload)
            response.raise_for_status()
            data = response.json()
            result = data.get("data", {})
            entries = result.get("itemList", [])
            if not entries:
                break

            oldest_in_batch: datetime | None = None
            for entry in entries:
                item_id = int(entry.get("itemId", 0) or 0)
                if item_id and item_id in seen_item_ids:
                    continue
                if item_id:
                    seen_item_ids.add(item_id)

                published_at = self._extract_entry_publish_time(entry, source.timezone)
                if published_at is not None:
                    oldest_in_batch = published_at if oldest_in_batch is None else min(oldest_in_batch, published_at)

                parsed = self._parse_item(entry, source, now)
                if parsed is not None:
                    items.append(parsed)

                if len(items) >= max_items:
                    break

            page_callback = str(result.get("pageCallback", "")).strip() or None
            has_next_page = bool(result.get("hasNextPage"))
            if not has_next_page or not page_callback:
                break
            if oldest_in_batch and not is_in_yesterday_today_window(
                oldest_in_batch,
                now,
                source.timezone,
                window_days=source.window_days,
            ):
                break
            page_event = 1

        return items[:max_items]

    def _extract_entry_publish_time(self, entry: dict[str, object], source_timezone: str) -> datetime | None:
        material = entry.get("templateMaterial")
        if not isinstance(material, dict):
            return None
        return self._parse_publish_time(material.get("publishTime"), source_timezone)

    def _build_payload(self, page_event: int, page_callback: str | None) -> dict[str, object]:
        param: dict[str, object] = {
            "siteId": 1,
            "platformId": 2,
            "subnavType": 1,
            "subnavNick": "web_news",
            "pageSize": 20,
            "pageEvent": page_event,
        }
        if page_callback:
            param["pageCallback"] = page_callback
        return {
            "partner_id": "web",
            "timestamp": int(datetime.now().timestamp() * 1000),
            "param": param,
        }

    def _parse_item(self, entry: dict[str, object], source: SourceConfig, now: datetime) -> NewsItem | None:
        material = entry.get("templateMaterial")
        if not isinstance(material, dict):
            return None

        title = self._clean_text(material.get("widgetTitle"))
        item_id = material.get("itemId") or entry.get("itemId")
        article_url = self._build_article_url(item_id)
        summary = self._fetch_generated_summary(article_url) or self._clean_text(material.get("summary"))
        published_at = self._parse_publish_time(material.get("publishTime"), source.timezone)
        raw_date_text = str(material.get("publishTime")) if material.get("publishTime") else None
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if not title or not article_url:
            return None
        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        return NewsItem(
            title=title,
            summary=summary,
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _fetch_generated_summary(self, article_url: str) -> str:
        if not article_url:
            return ""
        try:
            response = self.client.get(article_url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
        except Exception:
            LOGGER.warning("Failed to fetch 36Kr detail page. url=%s", article_url)
            return ""

        paragraphs: list[str] = []
        for selector in [".kr-rich-text-wrapper p", "article p", "main p", "[class*=rich-text] p"]:
            for paragraph in soup.select(selector):
                cleaned = self._clean_text(paragraph.get_text(" ", strip=True))
                normalized = self._normalize_summary_paragraph(cleaned)
                if normalized:
                    paragraphs.append(normalized)
            if paragraphs:
                break

        if not paragraphs:
            meta_description = soup.select_one('meta[name="description"], meta[property="og:description"]')
            cleaned = self._clean_text(meta_description.get("content") if meta_description else "")
            normalized = self._normalize_summary_paragraph(cleaned)
            if normalized:
                paragraphs.append(normalized)

        if not paragraphs:
            return ""

        summary_body = " ".join(paragraphs[:2]).strip()
        if len(summary_body) > 180:
            summary_body = summary_body[:177].rstrip() + "..."
        return f"{self.SUMMARY_PREFIX}{summary_body}"

    def _normalize_summary_paragraph(self, text: str) -> str:
        if not text:
            return ""
        ignored_prefixes = ("本文来自", "作者", "题图来自", "封面来源")
        if text.startswith(ignored_prefixes):
            return ""
        return text

    def _parse_publish_time(self, value: object, source_timezone: str) -> datetime | None:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value) / 1000, tz=ZoneInfo(source_timezone))
        if isinstance(value, str) and value.isdigit():
            return datetime.fromtimestamp(int(value) / 1000, tz=ZoneInfo(source_timezone))
        return None

    def _build_article_url(self, item_id: object) -> str:
        if item_id is None:
            return ""
        item_id_text = str(item_id).strip()
        if not item_id_text:
            return ""
        return f"https://36kr.com/p/{item_id_text}"

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()


class BJNewsIndustrialParser(BaseNewsParser):
    SUMMARY_PREFIX = "【程序生成摘要】"
    LIST_HEADERS = {"User-Agent": USER_AGENT}
    DETAIL_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.bjnews.com.cn/industrial",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers=self.LIST_HEADERS,
            trust_env=False,
        )

    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        max_items = source.max_items or self.settings.max_items_per_source
        items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for list_url in source.list_urls:
            next_url: str | None = str(list_url)
            while next_url and len(items) < max_items:
                response = httpx.get(
                    next_url,
                    headers=self.LIST_HEADERS,
                    follow_redirects=True,
                    timeout=self.settings.request_timeout_seconds,
                    trust_env=False,
                )
                if response.status_code == 405:
                    LOGGER.warning("BJNews pagination page returned 405, stopping pagination. url=%s", next_url)
                    break
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "lxml")

                for block in soup.select(".pin_demo_out"):
                    parsed = self._parse_card(block, source, now)
                    if parsed is None or parsed.url in seen_urls:
                        continue
                    seen_urls.add(str(parsed.url))
                    items.append(parsed)
                    if len(items) >= max_items:
                        break

                if len(items) >= max_items:
                    break
                next_url = self._extract_next_page_url(soup, next_url)

        return items[:max_items]

    def _parse_card(self, block: Tag, source: SourceConfig, now: datetime) -> NewsItem | None:
        if self._is_special_block(block):
            return None

        title_link = block.select_one('.pin_demo > a[href*="/detail/"], .pin_tit a[href*="/detail/"]')
        if not title_link:
            return None

        article_url = self._normalize_url(title_link.get("href", ""))
        title = self._clean_text(title_link.get_text(" ", strip=True))
        if not article_url or not title or self._looks_like_pagination_text(title):
            return None

        raw_date_text, generated_summary = self._fetch_detail_metadata(article_url)
        published_at = parse_datetime_text(raw_date_text, source.timezone, source.date_format_hint)
        date_parse_status = "parsed" if published_at else ("failed" if raw_date_text else "missing")
        date_in_scope = is_in_yesterday_today_window(
            published_at,
            now,
            source.timezone,
            window_days=source.window_days,
        )

        if published_at is not None and date_in_scope is False:
            return None
        if published_at is None and not self.settings.include_items_without_parsed_date:
            return None

        return NewsItem(
            title=title,
            summary=generated_summary,
            published_at=published_at,
            url=article_url,
            source=source.name,
            collected_at=now,
            raw_date_text=raw_date_text,
            content_preview=generated_summary[:200] or None,
            date_parse_status=date_parse_status,
            date_in_scope=date_in_scope,
        )

    def _is_special_block(self, block: Tag) -> bool:
        if block.select_one(".index-overflow-zt"):
            return True

        source_node = block.select_one(".bom .source")
        source_text = self._clean_text(source_node.get_text(" ", strip=True) if source_node else "")
        if source_text == "专题":
            return True

        special_link = block.select_one('a[href*="h5special"]')
        return special_link is not None

    def _fetch_detail_metadata(self, article_url: str) -> tuple[str | None, str]:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = self.client.get(article_url, headers=self.DETAIL_HEADERS)
                response.raise_for_status()
                html = response.text
                soup = BeautifulSoup(html, "lxml")
                raw_date_text = self._extract_detail_date(soup)
                generated_summary = self._generate_summary_from_detail(soup)
                return raw_date_text, generated_summary
            except Exception as exc:
                last_error = exc
                LOGGER.warning(
                    "Failed to fetch BJNews detail page. url=%s attempt=%s",
                    article_url,
                    attempt,
                )

        if last_error is not None:
            LOGGER.debug("BJNews detail fetch exhausted retries. url=%s error=%s", article_url, last_error)
        return None, ""

    def _extract_detail_date(self, soup: BeautifulSoup) -> str | None:
        for selector in [".content-time", ".article-time", ".time", "time", ".detail_time"]:
            node = soup.select_one(selector)
            if not node:
                continue
            candidate = self._clean_text(node.get_text(" ", strip=True))
            matched = self._match_detail_date(candidate)
            if matched:
                return matched

        for text in soup.stripped_strings:
            matched = self._match_detail_date(text)
            if matched:
                return matched
        return None

    def _generate_summary_from_detail(self, soup: BeautifulSoup) -> str:
        # Disabled: LLM will generate summaries instead
        return ""

    def _normalize_paragraph(self, text: str) -> str:
        if not text:
            return ""
        ignored_prefixes = ("编辑 ", "编辑：", "编辑", "校对 ", "校对：", "校对", "记者 ", "记者：")
        if text.startswith(ignored_prefixes):
            return ""
        return text

    def _extract_next_page_url(self, soup: BeautifulSoup, current_url: str) -> str | None:
        next_link = soup.select_one('a.last[href*="/industrial/"], a[title="下一页"][href*="/industrial/"]')
        if not next_link:
            for candidate in soup.select('a[href*="/industrial/"]'):
                link_text = self._clean_text(candidate.get_text(" ", strip=True))
                if link_text == "下一页":
                    next_link = candidate
                    break
        if not next_link:
            return None

        href = next_link.get("href", "").strip()
        link_text = self._clean_text(next_link.get_text(" ", strip=True))
        title = self._clean_text(next_link.get("title"))
        if "下一页" not in f"{title} {link_text}" and not re.search(r"/industrial/\d+\.html$", href):
            return None
        return urljoin(current_url, href)

    def _match_detail_date(self, text: str) -> str | None:
        if not text:
            return None
        match = re.search(r"20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", text)
        if match:
            return match.group(0)
        return None

    def _normalize_url(self, value: str) -> str:
        return urljoin("https://www.bjnews.com.cn", (value or "").strip())

    def _looks_like_pagination_text(self, text: str) -> bool:
        return text in {"下一页", "上一页"} or text.isdigit()

    def _clean_text(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()
