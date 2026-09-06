"""文章总结三栏的结构、格式化与原文引句校验。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


SECTION_FIELDS = {
    "why": (
        ("author_stated_motivation", "作者明确动机"),
        ("inferred_deeper_motivation", "深层动机"),
        ("pain_point", "现实/理论痛点"),
        ("existing_approach_limitations", "现有方案不足"),
        ("core_driver", "核心驱动"),
        ("expected_contribution", "预期贡献"),
    ),
    "how": (
        ("author_claimed_approach", "作者声称的做法"),
        ("research_objects_and_data", "研究对象与数据"),
        ("technical_route", "技术路线/方法论"),
        ("validation_and_evaluation", "验证与评估"),
        ("methodological_innovation", "方法创新定位"),
        ("inferred_implementation_details", "隐含细节"),
    ),
    "next": (
        ("author_stated_remaining_work", "作者明确提出"),
        ("inferred_open_questions", "尚未解决"),
        ("recommended_next_steps", "建议下一步"),
    ),
}

META_DEFENSIVE_PHRASES = (
    "本文从上下文推导",
    "本文基于上下文推导",
    "基于全文证据链",
    "并非作者逐字给出",
    "并非作者逐字表述",
)


def _required_text(container: dict, key: str, path: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}.{key} 必须是非空文本")
    return value.strip()


def validate_research_summary(value: object) -> dict:
    """校验三栏对象的形状，并返回已确认的字典。"""
    if not isinstance(value, dict):
        raise ValueError("research_summary 必须是对象")

    for section, fields in SECTION_FIELDS.items():
        section_data = value.get(section)
        if not isinstance(section_data, dict):
            raise ValueError(f"research_summary.{section} 必须是对象")
        for key, _ in fields:
            text = _required_text(section_data, key, f"research_summary.{section}")
            for phrase in META_DEFENSIVE_PHRASES:
                if phrase in text:
                    raise ValueError(
                        f"research_summary.{section}.{key} 含自我解释式套话“{phrase}”；"
                        "请直接写判断、证据和实际边界"
                    )

    why = value["why"]
    quotes = why.get("key_background_quotes")
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= 3:
        raise ValueError("research_summary.why.key_background_quotes 必须包含 1 至 3 条原文引句")
    for index, item in enumerate(quotes, 1):
        if not isinstance(item, dict):
            raise ValueError(
                f"research_summary.why.key_background_quotes[{index}] 必须是对象"
            )
        quote = _required_text(
            item,
            "quote",
            f"research_summary.why.key_background_quotes[{index}]",
        )
        _required_text(
            item,
            "supports",
            f"research_summary.why.key_background_quotes[{index}]",
        )
        supports = item["supports"].strip()
        for phrase in META_DEFENSIVE_PHRASES:
            if phrase in supports:
                raise ValueError(
                    f"第 {index} 条原文引句说明含自我解释式套话“{phrase}”；"
                    "请直接说明它支持的判断"
                )
        if not 8 <= len(quote) <= 400:
            raise ValueError(f"第 {index} 条原文引句应为 8 至 400 个字符")
    return value


def _normalize_evidence_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).replace("\u00ad", "")
    text = re.sub(r"(?<=\w)-\s*\r?\n\s*(?=\w)", "", text)
    text = text.translate(
        str.maketrans({
            "“": '"',
            "”": '"',
            "‘": "'",
            "’": "'",
            "–": "-",
            "—": "-",
        })
    )
    return re.sub(r"\s+", " ", text).strip().casefold()


def validate_background_quotes(research_summary: object, sources: Iterable[str]) -> None:
    """确认“为什么做”中的逐字引句可以在当前论文文本中找到。"""
    summary = validate_research_summary(research_summary)
    normalized_sources = [_normalize_evidence_text(source) for source in sources if source]
    if not normalized_sources:
        raise ValueError("没有可用于核验原文引句的论文文本")
    for index, item in enumerate(summary["why"]["key_background_quotes"], 1):
        quote = _normalize_evidence_text(item["quote"])
        if not any(quote in source for source in normalized_sources):
            raise ValueError(f"第 {index} 条关键背景引句未在当前论文 PDF 中找到")


def research_summary_items(research_summary: object, section: str) -> list[dict]:
    """把一个总结栏格式化为带顺序号、标题和正文的结构化条目。"""
    summary = validate_research_summary(research_summary)
    if section not in SECTION_FIELDS:
        raise ValueError(f"未知文章总结分栏：{section}")

    items: list[dict] = []
    section_data = summary[section]
    if section == "why":
        for index, item in enumerate(section_data["key_background_quotes"], 1):
            quote_label = "原文依据" if len(section_data["key_background_quotes"]) == 1 else f"原文依据{index}"
            items.append({
                "label": quote_label,
                "text": f"“{item['quote']}”——{item['supports']}",
            })
    items.extend(
        {"label": label, "text": section_data[key]}
        for key, label in SECTION_FIELDS[section]
    )
    return [
        {"number": index, "label": item["label"], "text": item["text"]}
        for index, item in enumerate(items, 1)
    ]


def research_summary_lines(research_summary: object, section: str) -> list[str]:
    """把一个总结栏格式化为带顺序号的纯文本行。"""
    return [
        f"{item['number']}. {item['label']}：{item['text']}"
        for item in research_summary_items(research_summary, section)
    ]
