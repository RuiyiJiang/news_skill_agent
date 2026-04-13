from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime

from app.config import Settings
from app.crawlers.factory import get_parser
from app.excel_writer import write_news_report
from app.feishu import FeishuNotifier
from app.llm_filter import OpenAIFoodIndustryFilter
from app.models import CrawlResult, NewsItem, PipelineResult
from app.sources_loader import load_sources, load_sources_by_names, normalize_groups
from app.tagging import assign_label
from app.utils.dedupe import dedupe_news_items


LOGGER = logging.getLogger(__name__)


def run_pipeline(
    settings: Settings,
    now: datetime | None = None,
    selected_groups: Sequence[str] | None = None,
) -> PipelineResult:
    started_at = now or datetime.now().astimezone()
    normalized_groups = normalize_groups(selected_groups)
    sources = load_sources(settings.sources_file, selected_groups=normalized_groups)
    return _run_pipeline_for_sources(
        settings,
        sources=sources,
        started_at=started_at,
        report_scope=normalized_groups,
        notify_groups=normalized_groups,
    )


def run_pipeline_for_source_names(
    settings: Settings,
    source_names: Sequence[str],
    now: datetime | None = None,
    report_scope: Sequence[str] | None = None,
) -> PipelineResult:
    started_at = now or datetime.now().astimezone()
    sources = load_sources_by_names(settings.sources_file, source_names)
    normalized_scope = list(report_scope or source_names)
    return _run_pipeline_for_sources(
        settings,
        sources=sources,
        started_at=started_at,
        report_scope=normalized_scope,
        notify_groups=[],
    )


def _run_pipeline_for_sources(
    settings: Settings,
    sources: Sequence,
    started_at: datetime,
    report_scope: Sequence[str],
    notify_groups: Sequence[str],
) -> PipelineResult:
    crawl_results: list[CrawlResult] = []
    aggregated_items: list[NewsItem] = []

    for source in sources:
        parser = get_parser(source.parser_type, settings)
        try:
            items = parser.fetch_recent(source, started_at)
            crawl_results.append(
                CrawlResult(
                    source=source.name,
                    source_url=str(source.list_urls[0]) if source.list_urls else str(source.base_url),
                    items=items,
                    success=True,
                )
            )
            aggregated_items.extend(items)
            LOGGER.info("Fetched items from source. source=%s count=%s", source.name, len(items))
        except Exception as exc:
            LOGGER.exception("Source crawl failed. source=%s", source.name)
            crawl_results.append(
                CrawlResult(
                    source=source.name,
                    source_url=str(source.list_urls[0]) if source.list_urls else str(source.base_url),
                    items=[],
                    success=False,
                    error_message=str(exc),
                )
            )

    for item in aggregated_items:
        item.label = assign_label(item.title, item.summary)

    all_items = dedupe_news_items(aggregated_items)
    llm_filter = OpenAIFoodIndustryFilter(settings)
    final_items = llm_filter.filter_items(all_items)
    unresolved_date_items = sum(1 for item in final_items if item.published_at is None)
    status_rows = _build_empty_source_rows(crawl_results)
    raw_report_name = _build_report_name("news_report_all", report_scope)
    filtered_report_name = _build_report_name("news_report_filtered", report_scope)
    raw_output_file = write_news_report(
        all_items,
        settings.output_dir,
        started_at,
        report_name=raw_report_name,
        extra_rows=status_rows,
    )
    filtered_output_file = write_news_report(
        final_items,
        settings.output_dir,
        started_at,
        report_name=filtered_report_name,
        extra_rows=status_rows,
    )

    finished_at = datetime.now().astimezone()
    failed_sources = [result.source for result in crawl_results if not result.success]
    pipeline_result = PipelineResult(
        started_at=started_at,
        finished_at=finished_at,
        total_sources=len(sources),
        successful_sources=len(sources) - len(failed_sources),
        failed_sources=len(failed_sources),
        total_items=len(final_items),
        raw_total_items=len(all_items),
        filtered_total_items=len(final_items),
        unresolved_date_items=unresolved_date_items,
        output_file=filtered_output_file,
        raw_output_file=raw_output_file,
        filtered_output_file=filtered_output_file,
        failed_source_names=failed_sources,
        selected_groups=list(notify_groups),
    )

    _notify(settings, pipeline_result)
    return pipeline_result


def _notify(settings: Settings, result: PipelineResult) -> None:
    if not settings.enable_feishu:
        LOGGER.info("Feishu notification disabled.")
        return

    notifier = FeishuNotifier(
        webhook_url=settings.feishu_webhook_url,
        secret=settings.feishu_secret,
        timeout=settings.request_timeout_seconds,
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        receive_id_type=settings.feishu_receive_id_type,
        receive_id=settings.feishu_receive_id,
    )
    if result.failed_sources == result.total_sources and result.total_sources > 0:
        try:
            notifier.send_error_summary(
                error_message="All configured sources failed during this run.",
                started_at=result.started_at,
                output_file=result.filtered_output_file,
                selected_groups=result.selected_groups,
            )
        except Exception:
            LOGGER.exception("Failed to deliver Feishu error summary.")
        return

    try:
        notifier.send_text_summary(result)
    except Exception:
        LOGGER.exception("Failed to deliver Feishu text summary.")
    try:
        notifier.send_report_files(result)
    except Exception:
        LOGGER.exception("Failed to deliver Feishu report files.")


def _build_report_name(base_name: str, selected_groups: Sequence[str]) -> str:
    if not selected_groups:
        return base_name
    suffix = "_".join(_sanitize_report_component(group) for group in selected_groups)
    return f"{base_name}_{suffix}"


def _sanitize_report_component(value: str) -> str:
    sanitized = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("_")
    return sanitized or "group"


def _build_empty_source_rows(crawl_results: Sequence[CrawlResult]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for result in crawl_results:
        if not result.success or result.items:
            continue
        rows.append(
            {
                "Source": result.source,
                "Title": "本次未抓到符合时间窗口的资讯",
                "Summary": "抓取成功，但当前时间窗口内没有可入表内容。",
                "Published At": "",
                "URL": result.source_url or "",
                "标签": "状态记录",
            }
        )
    return rows
