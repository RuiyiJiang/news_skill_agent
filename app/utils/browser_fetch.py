from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import BrowserType, Error as PlaywrightError, sync_playwright


DEFAULT_CHROME_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)
DEFAULT_SAFARI_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Safari/605.1.15"
)


@dataclass(frozen=True)
class BrowserProfile:
    name: str
    engine: str
    user_agent: str
    locale: str
    timezone_id: str
    platform: str
    sec_ch_ua: str | None = None


def fetch_html_with_playwright(
    url: str,
    *,
    timeout_seconds: float = 30.0,
    user_agent: str = DEFAULT_CHROME_USER_AGENT,
    locale: str = "ja-JP",
    wait_selector: str | None = None,
    headless: bool = True,
    preferred_engine: str | None = None,
    proxy_server: str | None = None,
    proxy_username: str | None = None,
    proxy_password: str | None = None,
) -> str:
    profiles = _build_profiles(
        default_locale=locale,
        explicit_user_agent=user_agent,
        preferred_engine=preferred_engine,
    )
    timeout_ms = int(timeout_seconds * 1000)
    last_html = ""
    last_error: Exception | None = None

    with sync_playwright() as playwright:
        for profile in profiles:
            try:
                html = _fetch_with_profile(
                    playwright=playwright,
                    profile=profile,
                    url=url,
                    timeout_ms=timeout_ms,
                    wait_selector=wait_selector,
                    headless=headless,
                    proxy_server=proxy_server,
                    proxy_username=proxy_username,
                    proxy_password=proxy_password,
                )
            except Exception as exc:
                last_error = exc
                continue

            last_html = html
            if not _looks_like_access_denied(html):
                return html

    if last_html:
        return last_html
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Playwright fetch returned no content for {url}")


def _build_profiles(
    default_locale: str,
    explicit_user_agent: str,
    preferred_engine: str | None = None,
) -> list[BrowserProfile]:
    locale = default_locale or "ja-JP"
    timezone_id = "Asia/Tokyo" if locale.startswith("ja") else "Asia/Shanghai"
    language_platform = "MacIntel"
    profiles = [
        BrowserProfile(
            name="chromium-mac-ja",
            engine="chromium",
            user_agent=explicit_user_agent or DEFAULT_CHROME_USER_AGENT,
            locale=locale,
            timezone_id=timezone_id,
            platform=language_platform,
            sec_ch_ua='"Google Chrome";v="135", "Chromium";v="135", "Not.A/Brand";v="24"',
        ),
        BrowserProfile(
            name="chromium-mac-en",
            engine="chromium",
            user_agent=DEFAULT_CHROME_USER_AGENT,
            locale="en-US",
            timezone_id="Asia/Tokyo" if locale.startswith("ja") else "Asia/Shanghai",
            platform=language_platform,
            sec_ch_ua='"Google Chrome";v="135", "Chromium";v="135", "Not.A/Brand";v="24"',
        ),
        BrowserProfile(
            name="webkit-mac-ja",
            engine="webkit",
            user_agent=DEFAULT_SAFARI_USER_AGENT,
            locale=locale,
            timezone_id=timezone_id,
            platform=language_platform,
        ),
    ]
    normalized_engine = (preferred_engine or "").strip().lower()
    if normalized_engine:
        filtered_profiles = [profile for profile in profiles if profile.engine == normalized_engine]
        if filtered_profiles:
            return filtered_profiles
    return profiles


def _fetch_with_profile(
    *,
    playwright,
    profile: BrowserProfile,
    url: str,
    timeout_ms: int,
    wait_selector: str | None,
    headless: bool,
    proxy_server: str | None,
    proxy_username: str | None,
    proxy_password: str | None,
) -> str:
    browser_type: BrowserType = getattr(playwright, profile.engine)
    launch_kwargs = {"headless": headless}
    proxy_settings = _build_proxy_settings(
        proxy_server=proxy_server,
        proxy_username=proxy_username,
        proxy_password=proxy_password,
    )
    if proxy_settings is not None:
        launch_kwargs["proxy"] = proxy_settings
    browser = browser_type.launch(**launch_kwargs)
    context = browser.new_context(
        user_agent=profile.user_agent,
        locale=profile.locale,
        viewport={"width": 1440, "height": 900},
        timezone_id=profile.timezone_id,
        extra_http_headers=_build_headers(profile),
    )
    context.add_init_script(_build_stealth_script(profile))
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=timeout_ms)
            except PlaywrightError:
                pass
        page.wait_for_timeout(1200)
        return page.content()
    finally:
        context.close()
        browser.close()


def _build_headers(profile: BrowserProfile) -> dict[str, str]:
    locale = profile.locale
    if locale.startswith("ja"):
        accept_language = "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"
    else:
        accept_language = "en-US,en;q=0.9,ja;q=0.7"
    headers = {
        "Accept-Language": accept_language,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Upgrade-Insecure-Requests": "1",
    }
    if profile.sec_ch_ua:
        headers["sec-ch-ua"] = profile.sec_ch_ua
        headers["sec-ch-ua-mobile"] = "?0"
        headers["sec-ch-ua-platform"] = '"macOS"'
    return headers


def _build_stealth_script(profile: BrowserProfile) -> str:
    if profile.locale.startswith("ja"):
        languages = "['ja-JP', 'ja', 'en-US', 'en']"
        language = "ja-JP"
    else:
        languages = "['en-US', 'en', 'ja-JP', 'ja']"
        language = "en-US"

    chrome_snippet = "window.chrome = { runtime: {} };" if profile.engine == "chromium" else ""
    return f"""
Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});
Object.defineProperty(navigator, 'language', {{get: () => '{language}'}});
Object.defineProperty(navigator, 'languages', {{get: () => {languages}}});
Object.defineProperty(navigator, 'platform', {{get: () => '{profile.platform}'}});
Object.defineProperty(navigator, 'hardwareConcurrency', {{get: () => 8}});
Object.defineProperty(navigator, 'deviceMemory', {{get: () => 8}});
Object.defineProperty(navigator, 'maxTouchPoints', {{get: () => 0}});
{chrome_snippet}
Object.defineProperty(navigator, 'plugins', {{get: () => [1, 2, 3, 4, 5]}});
"""


def _looks_like_access_denied(html: str) -> bool:
    normalized = html.casefold()
    markers = [
        "access denied",
        "errors.edgesuite.net",
        "akamai",
        "you don't have permission to access",
    ]
    return any(marker in normalized for marker in markers)


def _build_proxy_settings(
    *,
    proxy_server: str | None,
    proxy_username: str | None = None,
    proxy_password: str | None = None,
) -> dict[str, str] | None:
    server = (proxy_server or "").strip()
    if not server:
        return None

    settings = {"server": server}
    username = (proxy_username or "").strip()
    password = (proxy_password or "").strip()
    if username:
        settings["username"] = username
    if password:
        settings["password"] = password
    return settings
