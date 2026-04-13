from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.crawlers.custom_parsers import KirinNewsroomParser
from app.models import SourceConfig


def test_kirin_newsroom_parser_reads_yearly_xml_and_filters_window(monkeypatch) -> None:
    settings = SimpleNamespace(
        request_timeout_seconds=15.0,
        max_items_per_source=10,
        include_items_without_parsed_date=True,
    )
    parser = KirinNewsroomParser(settings)
    source = SourceConfig(
        name="麒麟控股",
        base_url="https://www.kirinholdings.com",
        list_urls=["https://www.kirinholdings.com/jp/newsroom/"],
        parser_type="custom_kirin_newsroom",
        timezone="Asia/Tokyo",
        max_items=10,
        window_days=4,
    )
    now = datetime.fromisoformat("2026-04-09T10:00:00+09:00")

    xml_2026 = """<?xml version="1.0" encoding="utf-8"?>
    <news>
      <item>
        <date>2026-04-08 11:00:05</date>
        <area>03</area>
        <categories><category>02</category><category>05</category></categories>
        <title><![CDATA[軽症から中等症の花粉症症状の緩和が確認された研究成果を発表]]></title>
        <companies><company>KH</company></companies>
        <link>/jp/newsroom/release/2026/0408_01.html</link>
        <target>self</target>
        <filesize></filesize>
      </item>
      <item>
        <date>2026-04-06 11:00:05</date>
        <area>01</area>
        <categories><category>03</category></categories>
        <title><![CDATA[「淡麗グリーンラベル」を2年ぶりにリニューアル]]></title>
        <companies><company>KB</company></companies>
        <link>/jp/newsroom/release/2026/0406_01.html</link>
        <target>self</target>
        <filesize></filesize>
      </item>
      <item>
        <date>2026-03-31 11:00:05</date>
        <area>03</area>
        <categories><category>02</category><category>03</category></categories>
        <title><![CDATA[old item]]></title>
        <companies><company>KBC</company></companies>
        <link>/jp/newsroom/release/2026/0331_02.html</link>
        <target>self</target>
        <filesize></filesize>
      </item>
    </news>
    """
    xml_2025 = """<?xml version="1.0" encoding="utf-8"?>
    <news>
      <item>
        <date>2025-12-30 09:00:05</date>
        <area>01</area>
        <categories><category>08</category></categories>
        <title><![CDATA[older year item]]></title>
        <companies><company>ME</company></companies>
        <link>/jp/newsroom/release/2025/1230_01.html</link>
        <target>self</target>
        <filesize>201KB</filesize>
      </item>
    </news>
    """

    class DummyResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str) -> DummyResponse:
        if url.endswith("news_2026.xml"):
            return DummyResponse(xml_2026)
        if url.endswith("news_2025.xml"):
            return DummyResponse(xml_2025)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(parser.client, "get", fake_get)

    items = parser.fetch_recent(source, now)

    assert [item.title for item in items] == [
        "軽症から中等症の花粉症症状の緩和が確認された研究成果を発表",
        "「淡麗グリーンラベル」を2年ぶりにリニューアル",
    ]
    assert str(items[0].url) == "https://www.kirinholdings.com/jp/newsroom/release/2026/0408_01.html"
    assert items[0].published_at == datetime.fromisoformat("2026-04-08T11:00:05+09:00")
    assert items[0].summary == "キリンホールディングス | CSV / 研究・技術 | 飲料・ヘルスサイエンス領域"
    assert items[1].summary == "キリンビール | 商品・サービス | 酒類"
