from __future__ import annotations

import argparse
import logging
import sys

from app.config import SettingsError, get_settings
from app.feishu import FeishuNotifier
from app.pipeline import run_pipeline_for_source_names
from app.utils.logging_utils import setup_logging


LOGGER = logging.getLogger(__name__)

CN_GOV_SOURCE_NAMES = [
    "国家卫健委政策法规",
    "国家卫健委规范性文件",
    "国家卫健委法律法规",
    "国家市场监督管理总局特殊食品安全监督管理司政策文件",
    "国家市场监督管理总局食品生产经营安全监督管理司政策文件",
    "国家市场监督管理总局食品安全抽检监测司信息发布",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the dedicated crawler for the six CN government sites.")
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run the dedicated CN government pipeline immediately.",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="List the dedicated CN government sources and exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        settings = get_settings()
        setup_logging(settings.log_level)

        if args.list_sources:
            for source_name in CN_GOV_SOURCE_NAMES:
                print(source_name)
            return 0

        if not args.run_once:
            print("Use --run-once to execute the dedicated CN government crawler.", file=sys.stderr)
            return 1

        result = run_pipeline_for_source_names(
            settings,
            CN_GOV_SOURCE_NAMES,
            report_scope=["国家政府专项"],
        )
        if result.is_successful():
            LOGGER.info("Dedicated CN government pipeline completed successfully.")
            return 0
        LOGGER.error("Dedicated CN government pipeline finished with failures.")
        return 1
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        LOGGER.exception("Dedicated CN government pipeline failed.")
        try:
            settings = get_settings()
            if settings.enable_feishu:
                notifier = FeishuNotifier(
                    webhook_url=settings.feishu_webhook_url,
                    secret=settings.feishu_secret,
                    timeout=settings.request_timeout_seconds,
                )
                from datetime import datetime

                notifier.send_error_summary(
                    str(exc),
                    datetime.now().astimezone(),
                    selected_groups=["国家政府专项"],
                )
        except Exception:
            LOGGER.exception("Failed to send Feishu error summary.")
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
