"""
Article content extraction module.

Extracts full article body text from URLs using Playwright and BeautifulSoup.
"""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup

from app.config import Settings
from app.utils.browser_fetch import fetch_html_with_playwright

LOGGER = logging.getLogger(__name__)


class ContentExtractor:
    """Extract article content from URLs."""

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
        ".entry-content",
        ".post-content",
        ".article-content",
        ".news-content",
        ".content-detail",
    ]

    # Tags to remove (ads, navigation, comments, etc.)
    REMOVE_SELECTORS = [
        "script", "style", "nav", "header", "footer", "aside",
        "[class*='sidebar']", "[class*='advertisement']",
        "[class*='ad-']", "[class*='related']",
        "[class*='comment']", "[class*='share']",
        "[class*='author']", "[class*='tag']",
        "[class*='recommend']", "[class*='hot']",
        "[class*='popular']", "[class*='trending']",
        # User account / login UI
        "[class*='user-menu']", "[class*='user-nav']",
        "[class*='account']", "[class*='login']",
        "[class*='logout']", "[class*='signup']",
        "[class*='register']", "[class*='profile-menu']",
        "[class*='member']", "[class*='my-']",
        # Breadcrumbs
        "[class*='breadcrumb']", "[class*='crumb']",
        # Search bar
        "[class*='search']", "[class*='searchbar']",
        # Subscription / follow / social
        "[class*='subscribe']", "[class*='follow']",
        "[class*='social']", "[class*='widget']",
        # Misc chrome
        "[class*='toolbar']", "[class*='topbar']",
        "[class*='bottom-bar']", "[class*='float']",
        "[class*='cookie']", "[class*='banner']",
        "[class*='popup']", "[class*='modal']",
        "[class*='overlay']", "[class*='dialog']",
        "[class*='toast']", "[class*='notice']",
        "[class*='copyright']", "[class*='disclaimer']",
        "[class*='footer-']", "[class*='header-']",
        "[role='navigation']", "[role='banner']",
        "[role='contentinfo']", "[role='complementary']",
    ]

    # Noise line patterns (text-level filtering after CSS removal)
    NOISE_PATTERNS = [
        "账号设置", "我的关注", "我的收藏", "申请的报道", "退出登录",
        "登录", "注册", "登录/注册", "登 录", "注 册",
        "设为首页", "加入收藏", "收藏本站",
        "意见反馈", "联系我们", "关于我们", "加入我们",
        "版权所有", "Copyright", "All Rights Reserved",
        "免责声明", "隐私政策", "用户协议", "服务条款",
        "下载APP", "扫码下载", "手机版", "触屏版", "电脑版",
        "分享到", "分享至", "分享：",
        "关注我们", "官方微信", "官方微博",
        "上一篇", "下一篇", "返回首页", "返回顶部",
        "我要评论", "发表评论", "热门评论",
        "扫码关注", "微信公众号", "微博",
        "APP下载", "客户端下载",
        "订阅", "退订", "RSS订阅",
        "字号：", "字体：", "A+", "A-",
        "打印", "转发", "收藏", "点赞",
        "举报", "投诉", "纠错",
        "编辑：", "责编：", "审核：", "来源：",
        "记者：", "通讯员：", "摄影：",
        "关键词", "标签：", "分类：",
        "浏览量", "阅读量", "次浏览", "次阅读",
        "参与评论", "查看评论",
        "Sign In", "Sign Up", "Log In", "Log Out",
        "Sign out", "Log out", "Register",
        "My Account", "My Profile", "My Favorites",
        "Settings", "Preferences",
        "Follow us", "Share this", "Share on",
        "Newsletter", "Subscribe", "Unsubscribe",
        "Cookie Policy", "Privacy Policy", "Terms of Service",
        "Terms of Use", "Cookie Preferences",
        "Download App", "Get the App",
        "Read more", "Load more", "View more",
        "Previous", "Next", "Back to top",
        "Related Articles", "Related Posts",
        "Recommended for you", "You may also like",
        "Leave a comment", "Add a comment",
        "Report", "Report this",
    ]

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def extract_content(self, url: str) -> str:
        """
        Fetch article page and extract main body text.

        Args:
            url: Article URL

        Returns:
            Extracted article text or empty string if failed
        """
        try:
            html = fetch_html_with_playwright(
                url,
                timeout_seconds=self.settings.request_timeout_seconds,
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            )
            if not html:
                return ""

            return self._extract_from_html(html)

        except Exception as exc:
            LOGGER.debug("Failed to extract content from %s: %s", url, exc)
            return ""

    def _extract_from_html(self, html: str) -> str:
        """Extract content from HTML string."""
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

        return self._filter_noise_lines(body_content.strip())

    def _filter_noise_lines(self, text: str) -> str:
        """Remove short UI noise lines from extracted text."""
        if not text:
            return text

        lines = text.split("\n")
        filtered = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Skip very short lines (likely UI fragments)
            if len(stripped) <= 6:
                continue
            # Skip lines that exactly match or start with known noise patterns
            is_noise = False
            for pattern in self.NOISE_PATTERNS:
                if stripped == pattern or stripped.startswith(pattern):
                    is_noise = True
                    break
            if not is_noise:
                filtered.append(stripped)

        return "\n".join(filtered)

    def _extract_text_from_element(self, element: BeautifulSoup) -> str:
        """Extract clean text from a BeautifulSoup element."""
        # Remove script and style tags first
        for tag in element.find_all(["script", "style", "noscript"]):
            tag.decompose()

        text = element.get_text(separator="\n", strip=True)
        # Clean up excessive newlines
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        return "\n".join(lines)


def extract_content_batch(urls: list[str], settings: Settings) -> dict[str, str]:
    """
    Extract content from multiple URLs.

    Args:
        urls: List of URLs to extract
        settings: Application settings

    Returns:
        Dictionary mapping URL to extracted content
    """
    extractor = ContentExtractor(settings)
    results = {}

    for url in urls:
        content = extractor.extract_content(url)
        results[url] = content
        if content:
            LOGGER.info("Extracted %d chars from %s", len(content), url[:60])
        else:
            LOGGER.warning("Failed to extract content from %s", url[:60])

    return results
