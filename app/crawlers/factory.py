from __future__ import annotations

from app.config import Settings
from app.crawlers.base import BaseNewsParser
from app.crawlers.custom_parsers import (
    ABInBevNewsMediaParser,
    AjinomotoNewsroomParser,
    AsahiBeerYearNewsParser,
    AsahiNewsroomParser,
    AsahiRDReportParser,
    BJNewsIndustrialParser,
    BeijingBusinessTodayParser,
    CninfoAnnouncementParser,
    CocaColaMediaCenterParser,
    CJKoreaNewsroomParser,
    DanonePressReleasesParser,
    ChineseGovernmentPagedListParser,
    ExampleCustomNewsParser,
    FerreroNewsParser,
    FamilyMartNewsReleaseParser,
    FeedNewsParser,
    FoodBevHomepageParser,
    FoodTalksFlashParser,
    FoodTalksNewsParser,
    GeneralMillsPressReleaseParser,
    ItoEnReleaseParser,
    JiemianNewsflashParser,
    KirinNewsroomParser,
    KraftHeinzPressReleaseParser,
    LawsonNewsReleaseParser,
    LotteChilsungNewsParser,
    MarsNewsAndStoriesParser,
    MegSnowNewsParser,
    MeijiRDTopicsParser,
    MeijiPressReleaseParser,
    MondelezNewsParser,
    MorinagaMilkReleaseParser,
    NissinNewsParser,
    NestleChinaMediaListParser,
    NestleHealthScienceNewsroomParser,
    NestleMediaNewsSitemapParser,
    PedailyNewsflashParser,
    PRTimesGourmetParser,
    PepsiCoChinaMediaCenterParser,
    PRNasiaIndustryParser,
    PepsiCoPressReleaseParser,
    SSNPNewsParser,
    SevenElevenJapanNewsReleaseParser,
    SuntoryNewsListParser,
    ThePaperExpressNewsParser,
    ThirtySixKrWebNewsParser,
    UnileverNewsSearchParser,
    YakultInformationParser,
)
from app.crawlers.generic_news_parser import GenericNewsParser


def get_parser(parser_type: str, settings: Settings) -> BaseNewsParser:
    normalized = parser_type.strip().lower()
    if normalized == "generic":
        return GenericNewsParser(settings=settings)
    if normalized == "custom_bbt_channel":
        return BeijingBusinessTodayParser(settings=settings)
    if normalized == "custom_bjnews_industrial":
        return BJNewsIndustrialParser(settings=settings)
    if normalized == "custom_jiemian_newsflash":
        return JiemianNewsflashParser(settings=settings)
    if normalized == "custom_feed":
        return FeedNewsParser(settings=settings)
    if normalized == "custom_foodbev_homepage":
        return FoodBevHomepageParser(settings=settings)
    if normalized == "custom_foodtalks_flash":
        return FoodTalksFlashParser(settings=settings)
    if normalized == "custom_foodtalks_news":
        return FoodTalksNewsParser(settings=settings)
    if normalized == "custom_thepaper_express":
        return ThePaperExpressNewsParser(settings=settings)
    if normalized == "custom_36kr_web_news":
        return ThirtySixKrWebNewsParser(settings=settings)
    if normalized == "custom_cn_gov_paged_list":
        return ChineseGovernmentPagedListParser(settings=settings)
    if normalized == "custom_cninfo_announcements":
        return CninfoAnnouncementParser(settings=settings)
    if normalized == "custom_coca_cola_media_center":
        return CocaColaMediaCenterParser(settings=settings)
    if normalized == "custom_cj_korea_newsroom":
        return CJKoreaNewsroomParser(settings=settings)
    if normalized == "custom_familymart_news_releases":
        return FamilyMartNewsReleaseParser(settings=settings)
    if normalized == "custom_seven_eleven_japan_news_releases":
        return SevenElevenJapanNewsReleaseParser(settings=settings)
    if normalized == "custom_lawson_news":
        return LawsonNewsReleaseParser(settings=settings)
    if normalized == "custom_lotte_chilsung_news":
        return LotteChilsungNewsParser(settings=settings)
    if normalized == "custom_danone_press_releases":
        return DanonePressReleasesParser(settings=settings)
    if normalized == "custom_ferrero_news":
        return FerreroNewsParser(settings=settings)
    if normalized == "custom_suntory_news_list":
        return SuntoryNewsListParser(settings=settings)
    if normalized == "custom_pepsico_press_releases":
        return PepsiCoPressReleaseParser(settings=settings)
    if normalized == "custom_pepsico_china_media_center":
        return PepsiCoChinaMediaCenterParser(settings=settings)
    if normalized == "custom_unilever_news_search":
        return UnileverNewsSearchParser(settings=settings)
    if normalized == "custom_ab_inbev_news_media":
        return ABInBevNewsMediaParser(settings=settings)
    if normalized == "custom_asahi_newsroom":
        return AsahiNewsroomParser(settings=settings)
    if normalized == "custom_asahi_beer_year_news":
        return AsahiBeerYearNewsParser(settings=settings)
    if normalized == "custom_asahi_rd_report":
        return AsahiRDReportParser(settings=settings)
    if normalized == "custom_ito_en_release":
        return ItoEnReleaseParser(settings=settings)
    if normalized == "custom_nissin_news":
        return NissinNewsParser(settings=settings)
    if normalized == "custom_ajinomoto_newsroom":
        return AjinomotoNewsroomParser(settings=settings)
    if normalized == "custom_meiji_pressrelease":
        return MeijiPressReleaseParser(settings=settings)
    if normalized == "custom_meiji_rd_topics":
        return MeijiRDTopicsParser(settings=settings)
    if normalized == "custom_meg_snow_news":
        return MegSnowNewsParser(settings=settings)
    if normalized == "custom_yakult_information":
        return YakultInformationParser(settings=settings)
    if normalized == "custom_kirin_newsroom":
        return KirinNewsroomParser(settings=settings)
    if normalized == "custom_morinaga_milk_release":
        return MorinagaMilkReleaseParser(settings=settings)
    if normalized == "custom_nestle_media_news_sitemap":
        return NestleMediaNewsSitemapParser(settings=settings)
    if normalized == "custom_nestle_china_media_list":
        return NestleChinaMediaListParser(settings=settings)
    if normalized == "custom_nestle_health_science_newsroom":
        return NestleHealthScienceNewsroomParser(settings=settings)
    if normalized == "custom_mars_news_and_stories":
        return MarsNewsAndStoriesParser(settings=settings)
    if normalized == "custom_mondelez_news":
        return MondelezNewsParser(settings=settings)
    if normalized == "custom_kraft_heinz_press_releases":
        return KraftHeinzPressReleaseParser(settings=settings)
    if normalized == "custom_general_mills_press_releases":
        return GeneralMillsPressReleaseParser(settings=settings)
    if normalized == "custom_prnasia_industry":
        return PRNasiaIndustryParser(settings=settings)
    if normalized == "custom_pedaily_newsflash":
        return PedailyNewsflashParser(settings=settings)
    if normalized == "custom_prtimes_gourmet":
        return PRTimesGourmetParser(settings=settings)
    if normalized == "custom_ssnp_news":
        return SSNPNewsParser(settings=settings)
    if normalized.startswith("custom_"):
        return ExampleCustomNewsParser()
    raise ValueError(f"Unsupported parser_type: {parser_type}")
