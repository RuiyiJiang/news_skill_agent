from __future__ import annotations

import re

from app.models import Label


LABEL_RULES: list[tuple[Label, tuple[str, ...]]] = [
    (
        Label.FUNDING,
        ("融资", "融投", "投资", "募资", "战略投资", "并购", "收购", "投后"),
    ),
    (
        Label.IPO,
        ("ipo", "招股书", "港交所", "纳斯达克", "纽交所", "挂牌", "上市"),
    ),
    (
        Label.NEW_PRODUCT,
        ("新品", "上新", "推出", "首发", "上市销售", "launch"),
    ),
    (
        Label.INNOVATION,
        ("技术创新", "研发", "专利", "ai", "自动化", "智能化", "工艺突破", "平台升级"),
    ),
    (
        Label.POLICY,
        ("政策", "法规", "监管", "标准", "征求意见", "通知", "办法", "条例"),
    ),
]


def assign_label(title: str, summary: str) -> str:
    haystack = normalize_text(f"{title} {summary}")
    for label, keywords in LABEL_RULES:
        if any(keyword.lower() in haystack for keyword in keywords):
            return label.value
    return Label.UNCATEGORIZED.value


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()
