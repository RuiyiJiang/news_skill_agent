from __future__ import annotations

import argparse
import logging
import sys

from app.config import SettingsError, get_settings
from app.feishu import FeishuNotifier
from app.pipeline import run_pipeline
from app.scheduler import start_scheduler
from app.sources_loader import SourceSelectionError, list_source_groups, normalize_groups
from app.utils.logging_utils import setup_logging


LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="News crawler and Feishu notifier.")
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run the pipeline immediately once instead of starting the scheduler.",
    )
    parser.add_argument(
        "--group",
        action="append",
        default=[],
        help="Only run sources in the given group. Can be repeated or comma-separated.",
    )
    parser.add_argument(
        "--list-groups",
        action="store_true",
        help="List enabled source groups and exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected_groups = normalize_groups(args.group)

    try:
        settings = get_settings()
        setup_logging(settings.log_level)

        if args.list_groups:
            groups = list_source_groups(settings.sources_file)
            if not groups:
                print("No enabled source groups configured.")
                return 0
            for group in groups:
                print(group)
            return 0

        if args.run_once:
            result = run_pipeline(settings, selected_groups=selected_groups)
            if result.is_successful():
                LOGGER.info("Run-once pipeline completed successfully.")
                return 0
            LOGGER.error("Run-once pipeline finished with failures.")
            return 1

        start_scheduler(settings, selected_groups=selected_groups)
        return 0
    except (SettingsError, SourceSelectionError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        LOGGER.exception("Application failed.")
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
                    selected_groups=selected_groups,
                )
        except Exception:
            LOGGER.exception("Failed to send Feishu error summary.")
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
