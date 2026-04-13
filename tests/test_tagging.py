from __future__ import annotations

from app.tagging import assign_label


def test_assign_label_funding_priority() -> None:
    label = assign_label("某公司完成融资并发布新品", "宣布战略投资")
    assert label == "企业融投资"


def test_assign_label_policy() -> None:
    label = assign_label("新规发布", "监管部门发布通知")
    assert label == "政策法规"


def test_assign_label_uncategorized() -> None:
    label = assign_label("普通行业新闻", "没有关键词")
    assert label == "未分类"
