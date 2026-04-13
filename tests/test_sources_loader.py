from __future__ import annotations

from pathlib import Path

import pytest

from app.sources_loader import SourceSelectionError, list_source_groups, load_sources, load_sources_by_names


def test_load_sources_filters_enabled_groups(tmp_path: Path) -> None:
    source_file = tmp_path / "sources.yaml"
    source_file.write_text(
        """
- name: Source A
  base_url: https://example.com
  list_urls:
    - https://example.com/news
  enabled: true
  parser_type: generic
  timezone: Asia/Shanghai
  groups:
    - 新媒体
    - 国内媒体

- name: Source B
  base_url: https://example.org
  list_urls:
    - https://example.org/news
  enabled: true
  parser_type: generic
  timezone: Asia/Shanghai
  groups:
    - 巨头官网

- name: Source C
  base_url: https://example.net
  list_urls:
    - https://example.net/news
  enabled: false
  parser_type: generic
  timezone: Asia/Shanghai
  groups:
    - 国家政府
""".strip(),
        encoding="utf-8",
    )

    sources = load_sources(source_file, selected_groups=["新媒体, 巨头官网", "新媒体"])

    assert [source.name for source in sources] == ["Source A", "Source B"]


def test_load_sources_raises_when_group_not_found(tmp_path: Path) -> None:
    source_file = tmp_path / "sources.yaml"
    source_file.write_text(
        """
- name: Source A
  base_url: https://example.com
  list_urls:
    - https://example.com/news
  enabled: true
  parser_type: generic
  timezone: Asia/Shanghai
  groups:
    - 新媒体
    - 国内媒体
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(SourceSelectionError) as exc_info:
        load_sources(source_file, selected_groups=["国家政府"])

    assert "国家政府" in str(exc_info.value)
    assert "新媒体" in str(exc_info.value)


def test_list_source_groups_returns_enabled_unique_groups(tmp_path: Path) -> None:
    source_file = tmp_path / "sources.yaml"
    source_file.write_text(
        """
- name: Source A
  base_url: https://example.com
  list_urls:
    - https://example.com/news
  enabled: true
  parser_type: generic
  timezone: Asia/Shanghai
  groups:
    - 新媒体
    - 国内媒体

- name: Source B
  base_url: https://example.org
  list_urls:
    - https://example.org/news
  enabled: true
  parser_type: generic
  timezone: Asia/Shanghai
  groups:
    - 新媒体
    - 巨头官网

- name: Source C
  base_url: https://example.net
  list_urls:
    - https://example.net/news
  enabled: false
  parser_type: generic
  timezone: Asia/Shanghai
  groups:
    - 国家政府
""".strip(),
        encoding="utf-8",
    )

    assert list_source_groups(source_file) == ["国内媒体", "巨头官网", "新媒体"]


def test_load_sources_by_names_filters_enabled_sources(tmp_path: Path) -> None:
    source_file = tmp_path / "sources.yaml"
    source_file.write_text(
        """
- name: Source A
  base_url: https://example.com
  list_urls:
    - https://example.com/news
  enabled: true
  parser_type: generic
  timezone: Asia/Shanghai
  groups:
    - 国家政府

- name: Source B
  base_url: https://example.org
  list_urls:
    - https://example.org/news
  enabled: true
  parser_type: generic
  timezone: Asia/Shanghai
  groups:
    - 国家政府

- name: Source C
  base_url: https://example.net
  list_urls:
    - https://example.net/news
  enabled: false
  parser_type: generic
  timezone: Asia/Shanghai
  groups:
    - 国家政府
""".strip(),
        encoding="utf-8",
    )

    sources = load_sources_by_names(source_file, ["Source B", "Source A"])

    assert [source.name for source in sources] == ["Source A", "Source B"]
