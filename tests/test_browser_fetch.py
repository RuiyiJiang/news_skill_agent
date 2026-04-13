from __future__ import annotations

from app.utils.browser_fetch import _build_profiles, _build_proxy_settings, _looks_like_access_denied


def test_browser_fetch_detects_access_denied_markers() -> None:
    html = """
    <html><head><title>Access Denied</title></head>
    <body>You don't have permission to access this resource.
    https://errors.edgesuite.net/example</body></html>
    """
    assert _looks_like_access_denied(html) is True
    assert _looks_like_access_denied("<html><title>Normal Page</title></html>") is False


def test_browser_fetch_builds_multiple_profiles() -> None:
    profiles = _build_profiles(default_locale="ja-JP", explicit_user_agent="")

    assert [profile.engine for profile in profiles] == ["chromium", "chromium", "webkit"]
    assert profiles[0].locale == "ja-JP"
    assert profiles[0].timezone_id == "Asia/Tokyo"
    assert profiles[2].user_agent


def test_browser_fetch_can_filter_profiles_by_engine() -> None:
    profiles = _build_profiles(
        default_locale="zh-CN",
        explicit_user_agent="",
        preferred_engine="chromium",
    )

    assert [profile.engine for profile in profiles] == ["chromium", "chromium"]


def test_browser_fetch_builds_proxy_settings() -> None:
    assert _build_proxy_settings(proxy_server="", proxy_username="", proxy_password="") is None
    assert _build_proxy_settings(
        proxy_server="http://127.0.0.1:8080",
        proxy_username="user",
        proxy_password="pass",
    ) == {
        "server": "http://127.0.0.1:8080",
        "username": "user",
        "password": "pass",
    }
