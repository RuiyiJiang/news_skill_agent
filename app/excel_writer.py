from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font

from app.models import NewsItem


EXCEL_COLUMNS = ["Source", "Title", "Summary", "Published At", "URL", "标签"]
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")
DISPLAY_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def write_news_report(
    items: list[NewsItem],
    output_dir: Path,
    now: datetime,
    *,
    report_name: str = "news_report",
    extra_rows: list[dict[str, str]] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{report_name}_{now.strftime('%Y-%m-%d_%H%M%S')}.xlsx"
    output_path = output_dir / filename

    rows = [
        {
            "Source": item.source,
            "Title": item.title,
            "Summary": item.summary,
            "Published At": format_published_at(item.published_at),
            "URL": str(item.url),
            "标签": item.label,
        }
        for item in items
    ]
    if extra_rows:
        rows.extend(
            {
                "Source": row.get("Source", ""),
                "Title": row.get("Title", ""),
                "Summary": row.get("Summary", ""),
                "Published At": row.get("Published At", ""),
                "URL": row.get("URL", ""),
                "标签": row.get("标签", ""),
            }
            for row in extra_rows
        )

    dataframe = pd.DataFrame(rows, columns=EXCEL_COLUMNS)
    if dataframe.empty:
        dataframe = pd.DataFrame(columns=EXCEL_COLUMNS)
    dataframe.to_excel(output_path, index=False, sheet_name="news", engine="openpyxl")

    workbook = load_workbook(output_path)
    worksheet = workbook["news"]
    worksheet.freeze_panes = "A2"

    for cell in worksheet[1]:
        cell.font = Font(bold=True)

    widths = {
        "A": 18,
        "B": 42,
        "C": 56,
        "D": 28,
        "E": 60,
        "F": 18,
    }
    for column, width in widths.items():
        worksheet.column_dimensions[column].width = width

    workbook.save(output_path)
    return output_path


def format_published_at(value: datetime | None) -> str:
    if value is None:
        return ""
    localized = value.astimezone(DISPLAY_TIMEZONE) if value.tzinfo else value.replace(tzinfo=DISPLAY_TIMEZONE)
    return localized.strftime(DISPLAY_TIME_FORMAT)
