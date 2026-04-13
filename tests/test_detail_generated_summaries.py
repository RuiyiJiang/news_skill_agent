from __future__ import annotations

from types import SimpleNamespace

from app.crawlers.custom_parsers import FoodTalksFlashParser, ThirtySixKrWebNewsParser


def make_settings() -> SimpleNamespace:
    return SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )


def test_foodtalks_generated_summary_is_prefixed() -> None:
    parser = FoodTalksFlashParser(make_settings())

    summary = parser._extract_summary(
        "<p>第一段快讯正文。</p><p>第二段补充信息。</p><p>第三段背景说明。</p>"
    )

    assert summary.startswith("【程序生成摘要】")
    assert "第一段快讯正文" in summary
    assert "第二段补充信息" in summary


def test_36kr_generated_summary_is_prefixed(monkeypatch) -> None:
    parser = ThirtySixKrWebNewsParser(make_settings())

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        text = """
        <html>
          <body>
            <div class="kr-rich-text-wrapper">
              <p>第一段正文。</p>
              <p>第二段正文。</p>
            </div>
          </body>
        </html>
        """

    monkeypatch.setattr(parser.client, "get", lambda *args, **kwargs: FakeResponse())

    summary = parser._fetch_generated_summary("https://36kr.com/p/123")

    assert summary.startswith("【程序生成摘要】")
    assert "第一段正文" in summary
    assert "第二段正文" in summary
