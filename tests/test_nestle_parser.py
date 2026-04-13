from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import (
    NestleChinaMediaListParser,
    NestleHealthScienceNewsroomParser,
    NestleMediaNewsSitemapParser,
)
from app.models import SourceConfig


def test_nestle_sitemap_parser_filters_recent_media_news_entries(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = NestleMediaNewsSitemapParser(settings)
    source = SourceConfig(
        name="雀巢",
        base_url="https://www.nestle.com",
        list_urls=["https://www.nestle.com/media/news"],
        parser_type="custom_nestle_media_news_sitemap",
        timezone="Europe/Zurich",
        max_items=10,
        window_days=3,
    )
    now = datetime.fromisoformat("2026-04-08T12:00:00+02:00")

    sitemap_xml = """
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://www.nestle.com/media/news/latest-product-update</loc>
        <lastmod>2026-04-07T09:15:00+02:00</lastmod>
      </url>
      <url>
        <loc>https://www.nestle.com/media/news/older-story</loc>
        <lastmod>2026-04-03T09:15:00+02:00</lastmod>
      </url>
      <url>
        <loc>https://www.nestle.com/stories/not-media-news</loc>
        <lastmod>2026-04-08T10:00:00+02:00</lastmod>
      </url>
    </urlset>
    """

    monkeypatch.setattr(parser, "_fetch_sitemap_xml", lambda source: sitemap_xml)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["Latest Product Update"]
    assert str(items[0].url) == "https://www.nestle.com/media/news/latest-product-update"
    assert items[0].raw_date_text == "2026-04-07T09:15:00+02:00"
    assert items[0].date_parse_status == "parsed"
    assert items[0].date_in_scope is True


def test_nestle_sitemap_parser_supports_press_releases_prefixes(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = NestleMediaNewsSitemapParser(settings)
    source = SourceConfig(
        name="雀巢日本",
        base_url="https://www.nestle.co.jp",
        list_urls=["https://www.nestle.co.jp/media/pressreleases"],
        parser_type="custom_nestle_media_news_sitemap",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=3,
    )
    now = datetime.fromisoformat("2026-04-08T12:00:00+09:00")

    sitemap_xml = """
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://www.nestle.co.jp/media/pressreleases/20260407_nescafe</loc>
        <lastmod>2026-04-07T10:30:00+09:00</lastmod>
      </url>
      <url>
        <loc>https://www.nestle.co.jp/media/pressreleases/20260401_nestle</loc>
        <lastmod>2026-04-01T09:00:00+09:00</lastmod>
      </url>
      <url>
        <loc>https://www.nestle.co.jp/media/newsandfeatures/20260407-feature</loc>
        <lastmod>2026-04-07T09:00:00+09:00</lastmod>
      </url>
    </urlset>
    """

    monkeypatch.setattr(parser, "_fetch_sitemap_xml", lambda source: sitemap_xml)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["Nescafe"]
    assert str(items[0].url) == "https://www.nestle.co.jp/media/pressreleases/20260407_nescafe"
    assert items[0].raw_date_text == "2026-04-07T10:30:00+09:00"
    assert items[0].date_parse_status == "parsed"
    assert items[0].date_in_scope is True


def test_nestle_sitemap_parser_supports_multiple_media_sections(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = NestleMediaNewsSitemapParser(settings)
    source = SourceConfig(
        name="雀巢中国",
        base_url="https://www.nestle.com.cn",
        list_urls=[
            "https://www.nestle.com.cn/media/pressreleases",
            "https://www.nestle.com.cn/media/nestleinnews",
            "https://www.nestle.com.cn/media/news-feed",
        ],
        parser_type="custom_nestle_media_news_sitemap",
        timezone="Asia/Shanghai",
        max_items=10,
        window_days=3,
    )
    now = datetime.fromisoformat("2026-04-08T12:00:00+08:00")

    sitemap_xml = """
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://www.nestle.com.cn/media/pressreleases/20260407-brand-upgrade</loc>
        <lastmod>2026-04-07T09:15:00+08:00</lastmod>
      </url>
      <url>
        <loc>https://www.nestle.com.cn/media/nestleinnews/20260407-china-news</loc>
        <lastmod>2026-04-07T08:30:00+08:00</lastmod>
      </url>
      <url>
        <loc>https://www.nestle.com.cn/media/news-feed/20260406-sustainability-update</loc>
        <lastmod>2026-04-06T15:00:00+08:00</lastmod>
      </url>
      <url>
        <loc>https://www.nestle.com.cn/media/other-section/20260407-ignore-me</loc>
        <lastmod>2026-04-07T10:00:00+08:00</lastmod>
      </url>
    </urlset>
    """

    monkeypatch.setattr(parser, "_fetch_sitemap_xml", lambda source: sitemap_xml)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "Brand Upgrade",
        "China News",
        "Sustainability Update",
    ]


def test_nestle_china_media_list_parser_extracts_real_titles_and_dates(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=24,
        include_items_without_parsed_date=True,
    )
    parser = NestleChinaMediaListParser(settings)
    source = SourceConfig(
        name="雀巢中国新闻稿",
        base_url="https://www.nestle.com.cn",
        list_urls=["https://www.nestle.com.cn/media/pressreleases"],
        parser_type="custom_nestle_china_media_list",
        timezone="Asia/Shanghai",
        max_items=24,
        window_days=3,
    )
    now = datetime.fromisoformat("2026-04-08T12:00:00+08:00")

    page_html = """
    <html>
      <body>
        <div class="view view-article-list view-display-id-block_press_releases">
          <div class="view-content">
            <div class="table-responsive">
              <table class="table table-hover table-striped">
                <tbody>
                  <tr>
                    <td class="views-field views-field-title">
                      <a href="/media/pressreleases/allpressreleases/%E4%B8%80%E7%B1%B3%E5%85%AC%E7%9B%8A%E9%A1%B9%E7%9B%AE%E5%9C%A8%E6%B2%AA%E5%90%AF%E5%8A%A8">
                        “一米公益”项目在沪启动，首站落地太太乐“爱心厨房”
                      </a>
                    </td>
                    <td class="views-field views-field-published-at">
                      <time datetime="2026-04-08T09:47:08+02:00">Apr 08, 2026</time>
                    </td>
                  </tr>
                  <tr>
                    <td class="views-field views-field-title">
                      <a href="/media/pressreleases/allpressreleases/%E9%9B%80%E5%B7%A2%E4%BB%A5%E5%93%81%E8%B4%A8%E4%B8%8E%E5%88%9B%E9%80%A0%E5%85%B1%E4%BA%AB%E4%BB%B7%E5%80%BC%E6%8A%A4%E8%88%AA%E5%92%96%E5%95%A1%E6%8B%89%E8%8A%B1%E5%A4%A7%E8%B5%9B">
                        扎根云南近四十载 雀巢以品质与创造共享价值护航咖啡拉花大赛
                      </a>
                    </td>
                    <td class="views-field views-field-published-at">
                      <time datetime="2026-04-07T09:18:52+02:00">Apr 07, 2026</time>
                    </td>
                  </tr>
                  <tr>
                    <td class="views-field views-field-title">
                      <a href="/media/pressreleases/allpressreleases/%E9%9B%80%E5%B7%A2%E4%B8%93%E4%B8%9A%E9%A4%90%E9%A5%AE%E4%BA%AE%E7%9B%B8%E6%AD%A6%E6%B1%89">
                        雀巢专业餐饮亮相武汉·良之隆食材节 推广"餐+饮"一站式解决方案
                      </a>
                    </td>
                    <td class="views-field views-field-published-at">
                      <time datetime="2026-04-02T17:12:15+02:00">Apr 02, 2026</time>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <ul class="js-pager__items pager">
            <li class="pager__item">
              <a href="?page=%2C%2C%2C%2C%2C1" rel="next">加载更多内容......</a>
            </li>
          </ul>
        </div>
      </body>
    </html>
    """
    second_page_html = """
    <html>
      <body>
        <div class="view view-article-list view-display-id-block_press_releases">
          <div class="view-content">
            <div class="table-responsive">
              <table class="table table-hover table-striped">
                <tbody>
                  <tr>
                    <td class="views-field views-field-title">
                      <a href="/media/pressreleases/allpressreleases/%E5%A4%AA%E5%A4%AA%E4%B9%90%E4%BA%94%E5%B9%B4%E8%9D%89%E8%81%94">
                        太太乐五年蝉联“最佳女性™工作场所”，以多元包容倡文明新风
                      </a>
                    </td>
                    <td class="views-field views-field-published-at">
                      <time datetime="2026-03-26T15:47:52+01:00">Mar 26, 2026</time>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </body>
    </html>
    """

    html_by_url = {
        "https://www.nestle.com.cn/media/pressreleases": page_html,
        "https://www.nestle.com.cn/media/pressreleases?page=%2C%2C%2C%2C%2C1": second_page_html,
    }
    monkeypatch.setattr(parser, "_fetch_html", lambda url: html_by_url[url])

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "“一米公益”项目在沪启动，首站落地太太乐“爱心厨房”",
        "扎根云南近四十载 雀巢以品质与创造共享价值护航咖啡拉花大赛",
    ]
    assert str(items[0].url) == (
        "https://www.nestle.com.cn/media/pressreleases/allpressreleases/"
        "%E4%B8%80%E7%B1%B3%E5%85%AC%E7%9B%8A%E9%A1%B9%E7%9B%AE%E5%9C%A8%E6%B2%AA%E5%90%AF%E5%8A%A8"
    )
    assert items[0].raw_date_text == "2026-04-08T09:47:08+02:00"
    assert items[0].date_parse_status == "parsed"
    assert items[0].date_in_scope is True


def test_nestle_health_science_newsroom_parser_fetches_full_title_from_detail(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        max_detail_fetch_per_source=5,
        include_items_without_parsed_date=True,
    )
    parser = NestleHealthScienceNewsroomParser(settings)
    source = SourceConfig(
        name="雀巢健康科学",
        base_url="https://www.nestlehealthscience.com",
        list_urls=["https://www.nestlehealthscience.com/newsroom"],
        parser_type="custom_nestle_health_science_newsroom",
        timezone="UTC",
        max_items=10,
        window_days=3,
    )
    now = datetime.fromisoformat("2026-04-08T12:00:00+00:00")
    requested_urls: list[str] = []

    page_one_html = """
    <html><body>
      <div class="view-pagination">
        <ul class="pagination js-pager__items">
          <li class="page-item"><a href="?page=1" class="page-link">2</a></li>
        </ul>
      </div>
      <div class="latest-news-item mb-5">
        <div class="date"><time datetime="2026-04-08T08:32:12Z">8 Apr 2026</time></div>
        <h5><a href="/newsroom/nestle-introduces-solution-children">Nestlé introduces an innovative complete nutritional solution for children…</a></h5>
        <div class="area-of-expertise">Press Releases</div>
      </div>
      <div class="latest-news-item mb-5">
        <div class="date"><time datetime="2026-04-07T14:35:32Z">7 Apr 2026</time></div>
        <h5><a href="/newsroom/press-releases/nad-study">Nestlé-led clinical study finds that NAD+ boosters could promote health in unexpected ways</a></h5>
        <div class="area-of-expertise">Press Releases</div>
      </div>
    </body></html>
    """
    page_two_html = """
    <html><body>
      <div class="latest-news-item mb-5">
        <div class="date"><time datetime="2026-04-06T12:00:00Z">6 Apr 2026</time></div>
        <h5><a href="/newsroom/press-releases/page-two-story">Page two story</a></h5>
        <div class="area-of-expertise">Research and Innovation</div>
      </div>
    </body></html>
    """
    detail_html = """
    <html><body>
      <h1 class="h1-heading">Nestlé introduces an innovative complete nutritional solution for children with special medical nutrition needs</h1>
    </body></html>
    """

    html_by_url = {
        "https://www.nestlehealthscience.com/newsroom": page_one_html,
        "https://www.nestlehealthscience.com/newsroom?page=1": page_two_html,
        "https://www.nestlehealthscience.com/newsroom/nestle-introduces-solution-children": detail_html,
    }

    def fake_fetch_html(url: str) -> str:
        requested_urls.append(url)
        if url in html_by_url:
            return html_by_url[url]
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(parser, "_fetch_html", fake_fetch_html)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "Nestlé introduces an innovative complete nutritional solution for children with special medical nutrition needs",
        "Nestlé-led clinical study finds that NAD+ boosters could promote health in unexpected ways",
        "Page two story",
    ]
    assert items[0].summary == "Press Releases"
    assert items[0].raw_date_text == "2026-04-08T08:32:12Z"
    assert str(items[2].url) == "https://www.nestlehealthscience.com/newsroom/press-releases/page-two-story"
    assert requested_urls == [
        "https://www.nestlehealthscience.com/newsroom",
        "https://www.nestlehealthscience.com/newsroom",
        "https://www.nestlehealthscience.com/newsroom/nestle-introduces-solution-children",
        "https://www.nestlehealthscience.com/newsroom?page=1",
    ]


def test_nestle_health_science_newsroom_parser_builds_paged_urls() -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        max_detail_fetch_per_source=5,
        include_items_without_parsed_date=True,
    )
    parser = NestleHealthScienceNewsroomParser(settings)

    assert parser._build_page_url("https://www.nestlehealthscience.com/newsroom", 0) == (
        "https://www.nestlehealthscience.com/newsroom"
    )
    assert parser._build_page_url("https://www.nestlehealthscience.com/newsroom", 1) == (
        "https://www.nestlehealthscience.com/newsroom?page=1"
    )
