from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml

from app.models import SourceConfig


class SourceSelectionError(ValueError):
    """Raised when the requested source group selection is invalid."""


def load_sources(path: Path, selected_groups: Iterable[str] | None = None) -> list[SourceConfig]:
    sources = [source for source in _load_source_configs(path) if source.enabled]
    normalized_groups = normalize_groups(selected_groups)
    if not normalized_groups:
        return sources

    requested = {group.casefold() for group in normalized_groups}
    filtered = [
        source
        for source in sources
        if requested.intersection({group.casefold() for group in source.groups})
    ]
    if filtered:
        return filtered

    available_groups = list_source_groups(path)
    requested_text = ", ".join(normalized_groups)
    if available_groups:
        available_text = ", ".join(available_groups)
        raise SourceSelectionError(
            f"No enabled sources matched groups: {requested_text}. "
            f"Available groups: {available_text}."
        )
    raise SourceSelectionError(
        f"No enabled sources matched groups: {requested_text}. "
        f"No groups are configured in {path}."
    )


def load_sources_by_names(path: Path, selected_names: Iterable[str]) -> list[SourceConfig]:
    sources = [source for source in _load_source_configs(path) if source.enabled]
    normalized_names = normalize_names(selected_names)
    if not normalized_names:
        raise SourceSelectionError("No source names were provided.")

    requested = {name.casefold() for name in normalized_names}
    filtered = [source for source in sources if source.name.casefold() in requested]
    if len(filtered) == len(requested):
        return filtered

    available_names = [source.name for source in sources]
    missing = [name for name in normalized_names if name.casefold() not in {source.name.casefold() for source in filtered}]
    available_text = ", ".join(available_names)
    missing_text = ", ".join(missing)
    raise SourceSelectionError(
        f"No enabled sources matched names: {missing_text}. "
        f"Available source names: {available_text}."
    )


def list_source_groups(path: Path) -> list[str]:
    groups: list[str] = []
    seen: set[str] = set()
    for source in _load_source_configs(path):
        if not source.enabled:
            continue
        for group in source.groups:
            key = group.casefold()
            if key in seen:
                continue
            seen.add(key)
            groups.append(group)
    return sorted(groups, key=lambda value: value.casefold())


def normalize_groups(values: Iterable[str] | None) -> list[str]:
    return _normalize_values(values)


def normalize_names(values: Iterable[str] | None) -> list[str]:
    return _normalize_values(values)


def _normalize_values(values: Iterable[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    if not values:
        return normalized

    for value in values:
        for part in value.split(","):
            group = part.strip()
            if not group:
                continue
            key = group.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(group)
    return normalized


def _load_source_configs(path: Path) -> list[SourceConfig]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [SourceConfig.model_validate(item) for item in raw]
