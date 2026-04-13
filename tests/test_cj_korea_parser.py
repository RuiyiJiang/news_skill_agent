from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import CJKoreaNewsroomParser
from app.models import SourceConfig


def _build_parser() -> CJKoreaNewsroomParser:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    return CJKoreaNewsroomParser(settings)


def _build_source(name: str, url: str) -> SourceConfig:
    return SourceConfig(
        name=name,
        base_url="https://www.cj.co.kr",
        list_urls=[url],
        parser_type="custom_cj_korea_newsroom",
        timezone="Asia/Seoul",
        max_items=10,
        window_days=30,
    )


def test_cj_korea_press_releases_parser_reads_cards() -> None:
    parser = _build_parser()
    source = _build_source("CJ韩国新闻稿", "https://www.cj.co.kr/kr/newsroom/pressreleases")
    now = datetime.fromisoformat("2026-04-10T10:00:00+09:00")

    html = """
    <html><body>
      <div class="grid bbs-news-list">
        <div class="grid list">
          <div class="item js-inview">
            <div class="inner">
              <div class="background"><span><img alt="친환경 소재" /></span></div>
              <div class="module">
                <a href="/kr/newsroom/pressreleases/news-detail/1753" class="anchor">
                  <h2 class="name">업사이클링 기술 개발</h2>
                  <p class="date">2026.03.30</p>
                </a>
              </div>
            </div>
          </div>
        </div>
      </div>
    </body></html>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    parser.client.get = lambda url: DummyResponse(html)  # type: ignore[method-assign]

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["업사이클링 기술 개발"]
    assert str(items[0].url) == "https://www.cj.co.kr/kr/newsroom/pressreleases/news-detail/1753"
    assert items[0].published_at == datetime.fromisoformat("2026-03-30T00:00:00+09:00")
    assert items[0].summary == "친환경 소재"


def test_cj_korea_stories_parser_reads_categories() -> None:
    parser = _build_parser()
    source = _build_source("CJ韩国企划专栏", "https://www.cj.co.kr/kr/newsroom/stories")
    now = datetime.fromisoformat("2026-04-10T10:00:00+09:00")

    html = """
    <html><body>
      <div class="storyList">
        <div class="newsroom-grid newsroom-stories">
          <div class="item">
            <a href="/kr/newsroom/stories/detail/92" alt="일본 소비자 사진">
              <div class="category"><span>Food</span><span>Global</span></div>
              <h2 class="name">K-푸드인데 ‘Made in Japan?’ 글로벌 매출 6조에 숨겨진 비밀</h2>
              <div class="date_views"><p class="date">2026.03.31</p></div>
            </a>
          </div>
        </div>
      </div>
    </body></html>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    parser.client.get = lambda url: DummyResponse(html)  # type: ignore[method-assign]

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["K-푸드인데 ‘Made in Japan?’ 글로벌 매출 6조에 숨겨진 비밀"]
    assert str(items[0].url) == "https://www.cj.co.kr/kr/newsroom/stories/detail/92"
    assert items[0].published_at == datetime.fromisoformat("2026-03-31T00:00:00+09:00")
    assert items[0].summary == "Food, Global"


def test_cj_korea_in_the_media_parser_reads_media_name() -> None:
    parser = _build_parser()
    source = _build_source("CJ韩国媒体报道", "https://www.cj.co.kr/kr/newsroom/inthemedia")
    now = datetime.fromisoformat("2026-04-10T10:00:00+09:00")

    html = """
    <html><body>
      <div class="newsroom-grid newsroom-press">
        <div id="news-all">
          <div class="item">
            <a href="https://www.fnnews.com/news/202604051829252711" target="_blank">
              <p class="media">파이낸셜뉴스</p>
              <div class="name_date">
                <h2 class="name">해외서도 줄서서 먹는 비비고 김밥… K분식 열풍 이끈다 [K푸드, 글로벌 푸드로]</h2>
                <p class="date">2026.04.05</p>
              </div>
            </a>
          </div>
        </div>
      </div>
    </body></html>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    parser.client.get = lambda url: DummyResponse(html)  # type: ignore[method-assign]

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == ["해외서도 줄서서 먹는 비비고 김밥… K분식 열풍 이끈다 [K푸드, 글로벌 푸드로]"]
    assert str(items[0].url) == "https://www.fnnews.com/news/202604051829252711"
    assert items[0].published_at == datetime.fromisoformat("2026-04-05T00:00:00+09:00")
    assert items[0].summary == "파이낸셜뉴스"
