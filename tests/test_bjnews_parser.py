from __future__ import annotations

from types import SimpleNamespace

from bs4 import BeautifulSoup

from app.crawlers.custom_parsers import BJNewsIndustrialParser


def make_parser() -> BJNewsIndustrialParser:
    return BJNewsIndustrialParser(
        SimpleNamespace(
            request_timeout_seconds=15.0,
            max_items_per_source=10,
            include_items_without_parsed_date=True,
        )
    )


def test_bjnews_special_block_is_skipped() -> None:
    parser = make_parser()
    html = """
    <div class="pin_demo_out">
      <div class="pin_demo">
        <div class="index-overflow-zt"></div>
      </div>
      <div class="bom">
        <span class="source">专题</span>
      </div>
    </div>
    """
    block = BeautifulSoup(html, "lxml").select_one(".pin_demo_out")

    assert block is not None
    assert parser._is_special_block(block) is True


def test_bjnews_generated_summary_is_prefixed() -> None:
    parser = make_parser()
    html = """
    <html>
      <body>
        <div class="article-text">
          <p>新京报讯（记者王子扬）这是第一段正文，用来生成摘要。</p>
          <p>这是第二段正文，用来补充摘要信息。</p>
          <p>编辑 李严</p>
        </div>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "lxml")

    summary = parser._generate_summary_from_detail(soup)

    assert summary.startswith("【程序生成摘要】")
    assert "第一段正文" in summary
    assert "第二段正文" in summary
    assert "编辑 李严" not in summary
