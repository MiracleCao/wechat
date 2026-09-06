"""从 materials/ 中的论文素材生成公众号文章产物。"""

from __future__ import annotations

import argparse
import base64
import html
import json
import logging
import mimetypes
import os
import re
import sys
from pathlib import Path

from pypdf import PdfReader

from article_summary import (
    research_summary_items,
    research_summary_lines,
    validate_background_quotes,
    validate_research_summary,
)

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ROOT / "materials"
OUTPUTS = ROOT / "outputs"
SUPPORTED_TEXT = {".pdf", ".md", ".txt"}
SUPPORTED_IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED = SUPPORTED_TEXT | SUPPORTED_IMAGES
LOG = logging.getLogger("article-generator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def material_figure_number(path: Path) -> int | None:
    """从常见论文配图文件名中提取原始图号，如 gr12_lrg -> 12。"""
    patterns = (
        r"(?:^|[-_])gr(?:aphic)?[-_]?(\d+)(?:[-_]|$)",
        r"(?:^|[-_])fig(?:ure)?[-_. ]?(\d+)(?:[-_ .]|$)",
        r"^图[-_ ]?(\d+)(?:[-_ .]|$)",
    )
    stem = path.stem.lower()
    for pattern in patterns:
        match = re.search(pattern, stem, flags=re.I)
        if match:
            return int(match.group(1))
    return None


def material_sort_key(path: Path) -> tuple[int, int, str]:
    number = material_figure_number(path)
    if path.suffix.lower() not in SUPPORTED_IMAGES:
        return (0, 0, path.name.lower())
    return (1, number if number is not None else sys.maxsize, path.name.lower())


def is_chinese_affiliation(data: dict) -> bool:
    country = str(data.get("corresponding_country", "")).strip().lower()
    return country in {"中国", "中国大陆", "china", "pr china", "p.r. china", "prc"} or "中国" in country


def normalize_title(title: str, data: dict) -> str:
    """按通讯作者国家强制规范标题前缀，避免模型误带或漏带机构。"""
    parts = [part.strip() for part in str(title).split("|") if part.strip()]
    journal = str(data["journal_abbr"]).strip()
    if journal in parts:
        description = " | ".join(parts[parts.index(journal) + 1:]).strip()
    else:
        description = parts[-1] if parts else str(title).strip()
    if not description:
        raise ValueError(f"候选标题缺少正文：{title}")
    if is_chinese_affiliation(data):
        institution = str(data["institution"]).strip()
        return f"{institution} | {journal} | {description}"
    return f"{journal} | {description}"


def detect_total_figure_count(material_text: str) -> int | None:
    """以正文中出现的 Fig. 图注编号为准，取最后/最大的主图编号。"""
    numbers = [
        int(value)
        for value in re.findall(
            r"(?im)^\s*(?:figure|fig\.?)\s*(\d+)\b",
            material_text,
        )
    ]
    return max(numbers) if numbers else None


def detect_doi_url(material_text: str) -> str | None:
    """从论文元数据或正文中提取 DOI，并统一为可点击地址。"""
    match = re.search(r"\b10\.\d{4,9}/[^\s<>\"]+", material_text, flags=re.I)
    if not match:
        return None
    doi = match.group(0).rstrip(".,;:)]}")
    return f"https://doi.org/{doi}"


SYSTEM_PROMPT = """你是严谨的材料科学论文编辑。只能依据用户提供的素材写作，不得补造机构、期刊、
实验结果、图号或结论。输出一个合法 JSON 对象，不要 Markdown 代码围栏。JSON 必须含：
institution, journal_abbr, doi_url, original_title, translated_title, corresponding_affiliation,
corresponding_country, total_figure_count,
guide, figures, conclusion, research_summary, summary, alternative_titles, keywords,
cover_image_suggestion, publish_time_suggestion。
figures 的对象数量必须与用户提供的图片数量完全一致（允许 1 至 6 张），每个对象含 label 和 description；
若素材不足以确认每张图片，必须报错字段 error 说明缺失，不得猜测。summary 不超过 120 个汉字。
每个 figure.label 必须保留论文中的原始图号（如“图2”“图5”），不得按照入选顺序重新编号。
corresponding_affiliation 必须翻译成中文；figure.description 不得以“图N：”开头，
只写说明性正文。
alternative_titles 必须恰好 10 个，每个至少 25 个字。若通讯作者单位在中国，格式为
“机构名 | 刊名缩写 | 描述”；若通讯作者单位不在中国，省略机构名，格式为“刊名缩写 | 描述”。
标题应准确说明创新点和主要工作，不夸张。guide 用一整段介绍研究背景和主要工作；
每个 figure description 用一整段；conclusion 用一整段。

research_summary 必须是包含 why、how、next 的对象：
1. why 必须含 key_background_quotes、author_stated_motivation、inferred_deeper_motivation、
pain_point、existing_approach_limitations、core_driver、expected_contribution。
key_background_quotes 必须是 1 至 3 个对象，每项含 quote 和 supports；quote 必须逐字摘自论文原文，
supports 用中文解释该引句支持什么动机判断。author_stated_motivation 只写作者明确陈述的动机；
inferred_deeper_motivation 写有文本依据的深层动机，不得冒充作者结论；字段和呈现小标题已经负责区分，
正文不要再写“本文推导”“基于全文证据链”“并非作者逐字给出”等自我解释。
2. how 必须含 author_claimed_approach、research_objects_and_data、technical_route、
validation_and_evaluation、methodological_innovation、inferred_implementation_details，按“数据→方法→验证”展开。
author_claimed_approach 只写论文明确声称的做法；inferred_implementation_details 只写由方法上下文支持、
但论文未直接交代的潜在细节。字段和小标题已经表达其性质，正文直接说具体细节及其依据。
规模、来源、预处理、基线、消融、指标或统计检验若未报告，不得补造；只有缺失会影响读者判断时才简洁说明。
3. next 必须含 author_stated_remaining_work、inferred_open_questions、recommended_next_steps。
作者若没有明确提出局限或后续工作，应简洁说明，再把开放问题与建议写在各自字段中。
三栏应信息密集、避免与 guide、逐图解读和 conclusion 原句式重复。语言要像有经验的科研编辑：
直接写判断、证据和实际限制，不解释自己的写作过程；不要机械使用“需要强调的是”“值得注意的是”开头，
也不要用“并非……”“不能等同于……”“仍需进一步……”为每一点例行收尾。真正影响结论适用范围的限制照常写清。"""


def read_materials() -> tuple[list[Path], str, list[str]]:
    files = sorted([
        path for path in MATERIALS.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED and path.name.lower() != "readme.md"
    ], key=material_sort_key)
    if not files:
        raise FileNotFoundError("materials/ 中没有论文素材；请先放入 PDF、Markdown 或文本文件")
    image_files = [path for path in files if path.suffix.lower() in SUPPORTED_IMAGES]
    if not 1 <= len(image_files) <= 6:
        raise ValueError(
            f"检测到 {len(image_files)} 张重点图片；使用图片模式时请提供 1 至 6 张"
        )

    chunks: list[str] = []
    paper_texts: list[str] = []
    for path in files:
        LOG.info("读取素材：%s", path.relative_to(ROOT))
        if path.suffix.lower() in SUPPORTED_IMAGES:
            chunks.append(f"\n===== 用户提供的重点图片：{path.name} =====")
            continue
        if path.suffix.lower() == ".pdf":
            reader = PdfReader(path)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            paper_texts.append(text)
        else:
            text = path.read_text(encoding="utf-8")
        if text.strip():
            chunks.append(f"\n===== {path.name} =====\n{text.strip()}")
    if not chunks:
        raise ValueError("素材文件存在，但没有提取到可用文本；扫描版 PDF 请先进行 OCR")
    if len(paper_texts) != 1:
        raise ValueError(f"materials/ 必须恰好包含一篇论文 PDF；当前检测到 {len(paper_texts)} 篇")
    return files, "\n".join(chunks), paper_texts


def parse_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    return json.loads(cleaned)


def validate(data: dict, expected_figure_count: int, paper_texts: list[str]) -> None:
    if data.get("error"):
        raise ValueError(f"素材不足：{data['error']}")
    required = (
        "institution", "journal_abbr", "guide", "figures", "conclusion", "research_summary", "summary",
        "doi_url", "original_title", "translated_title", "corresponding_affiliation",
        "corresponding_country", "total_figure_count",
        "alternative_titles", "keywords", "cover_image_suggestion", "publish_time_suggestion",
    )
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise ValueError("模型输出缺少字段：" + ", ".join(missing))
    validate_research_summary(data["research_summary"])
    validate_background_quotes(data["research_summary"], paper_texts)
    if len(data["figures"]) != expected_figure_count:
        raise ValueError(f"重点图解读必须恰好 {expected_figure_count} 项")
    if len(data["alternative_titles"]) != 10:
        raise ValueError("候选标题必须恰好 10 个")
    if len(re.sub(r"\s+", "", data["summary"])) > 120:
        raise ValueError("摘要超过 120 个汉字")
    for title in data["alternative_titles"]:
        expected_separators = 2 if data["corresponding_country"].strip() == "中国" else 1
        if len(title) < 25 or title.count(" | ") != expected_separators:
            raise ValueError(f"候选标题格式或长度不合格：{title}")


def markdown_document(data: dict) -> str:
    title = data["alternative_titles"][0]
    parts = [
        f"# {title}", "", f"DOI：{data['doi_url']}", "",
        "## 导读", "", data["guide"], "", f"## {len(data['figures'])} 幅关键图解读", "",
    ]
    for figure in data["figures"]:
        parts.extend([f"**{figure['label']}：**{figure['description']}", ""])
    parts.extend(["## 主要结论", "", data["conclusion"], "", "## 文章总结", ""])
    for heading, section in (("为什么做？", "why"), ("怎么做的？", "how"), ("还需要做什么？", "next")):
        parts.extend([f"### {heading}", ""])
        parts.extend(research_summary_lines(data["research_summary"], section))
        parts.append("")
    parts.extend(["## 120 字内概括", "", data["summary"],
                  "", "## 备选标题", ""])
    parts.extend(f"{index}. {title}" for index, title in enumerate(data["alternative_titles"], 1))
    return "\n".join(parts).strip() + "\n"


def html_document(data: dict) -> str:
    esc = lambda value: html.escape(str(value), quote=True)
    figure_html = "".join(
        f'<p style="margin:16px 0;line-height:1.85;color:#333;">'
        f'<strong style="color:#1f5f8b;">{esc(item["label"])}：</strong>{esc(item["description"])}</p>'
        for item in data["figures"]
    )
    summary_html = "".join(
        f'<h3 style="font-size:17px;margin:20px 0 8px;">{esc(heading)}</h3>'
        + "".join(
            f'<p style="margin:8px 0;line-height:1.85;color:#333;">'
            f'<strong style="color:#007aaa;">{item["number"]}. {esc(item["label"])}：</strong>'
            f'{esc(item["text"])}</p>'
            for item in research_summary_items(data["research_summary"], section)
        )
        for heading, section in (("为什么做？", "why"), ("怎么做的？", "how"), ("还需要做什么？", "next"))
    )
    return f"""<section style="max-width:680px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;color:#222;">
<h1 style="font-size:24px;line-height:1.45;margin:20px 0;">{esc(data["alternative_titles"][0])}</h1>
<p style="line-height:1.85;color:#555;">DOI：{esc(data["doi_url"])}</p>
<h2 style="font-size:19px;border-left:4px solid #1f5f8b;padding-left:10px;margin-top:28px;">导读</h2>
<p style="line-height:1.85;color:#333;">{esc(data["guide"])}</p>
<h2 style="font-size:19px;border-left:4px solid #1f5f8b;padding-left:10px;margin-top:28px;">{len(data["figures"])} 幅关键图解读</h2>
{figure_html}
<h2 style="font-size:19px;border-left:4px solid #1f5f8b;padding-left:10px;margin-top:28px;">主要结论</h2>
<p style="line-height:1.85;color:#333;">{esc(data["conclusion"])}</p>
<h2 style="font-size:19px;border-left:4px solid #1f5f8b;padding-left:10px;margin-top:28px;">文章总结</h2>
{summary_html}
<h2 style="font-size:19px;border-left:4px solid #1f5f8b;padding-left:10px;margin-top:28px;">论文概括</h2>
<p style="line-height:1.85;color:#333;">{esc(data["summary"])}</p>
</section>
"""


def main() -> int:
    from openai import OpenAI

    parser = argparse.ArgumentParser(description="根据论文素材生成公众号文章")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-5.2"))
    args = parser.parse_args()
    try:
        if not os.getenv("OPENAI_API_KEY"):
            raise EnvironmentError("未设置 OPENAI_API_KEY 环境变量")
        files, material_text, paper_texts = read_materials()
        image_files = [path for path in files if path.suffix.lower() in SUPPORTED_IMAGES]
        selected_figure_numbers = [material_figure_number(path) for path in image_files]
        if image_files and any(number is None for number in selected_figure_numbers):
            unknown = [
                path.name for path, number in zip(image_files, selected_figure_numbers)
                if number is None
            ]
            raise ValueError(
                "无法从以下图片文件名识别原始图号，请使用 gr1、fig1 或 图1 等命名："
                + "、".join(unknown)
            )
        total_figure_count = detect_total_figure_count(material_text)
        doi_url = detect_doi_url(material_text)
        if doi_url is None:
            raise ValueError("未能从论文中识别 DOI")
        LOG.info("调用模型生成文章；模型：%s", args.model)
        content: list[dict] = [
            {
                "type": "input_text",
                "text": (
                    "请根据以下论文素材生成文章。务必核对图号与图注。"
                    f"本次共提供 {len(image_files)} 张图片，figures 必须恰好包含 "
                    f"{len(image_files)} 项，并按图片文件名排序逐一解读：\n"
                    + material_text
                ),
            }
        ]
        for path in files:
            if path.suffix.lower() not in SUPPORTED_IMAGES:
                continue
            figure_number = material_figure_number(path)
            content.append(
                {
                    "type": "input_text",
                    "text": f"下一张重点图片是论文原图{figure_number}，label 必须写为“图{figure_number}”。",
                }
            )
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{mime};base64,{encoded}",
                }
            )
        response = OpenAI().responses.create(
            model=args.model,
            instructions=SYSTEM_PROMPT,
            input=[{"role": "user", "content": content}],
        )
        data = parse_json(response.output_text)
        data["doi_url"] = doi_url
        data["alternative_titles"] = [
            normalize_title(title, data) for title in data.get("alternative_titles", [])
        ]
        if data["alternative_titles"]:
            data["title"] = data["alternative_titles"][0]
        if image_files:
            for figure, number in zip(data.get("figures", []), selected_figure_numbers):
                figure["label"] = f"图{number}"
        if total_figure_count is not None:
            data["total_figure_count"] = total_figure_count
        validate(data, len(image_files), paper_texts)
        OUTPUTS.mkdir(parents=True, exist_ok=True)

        metadata = {
            "title": data["alternative_titles"][0],
            "doi_url": data["doi_url"],
            "summary": data["summary"],
            "original_title": data["original_title"],
            "translated_title": data["translated_title"],
            "corresponding_affiliation": data["corresponding_affiliation"],
            "corresponding_country": data["corresponding_country"],
            "total_figure_count": data["total_figure_count"],
            "selected_figure_numbers": selected_figure_numbers,
            "guide": data["guide"],
            "figures": data["figures"],
            "conclusion": data["conclusion"],
            "research_summary": data["research_summary"],
            "keywords": data["keywords"],
            "cover_image_suggestion": data["cover_image_suggestion"],
            "publish_time_suggestion": data["publish_time_suggestion"],
            "alternative_titles": data["alternative_titles"],
            "institution": data["institution"],
            "journal_abbr": data["journal_abbr"],
            "source_files": [str(path.relative_to(ROOT)) for path in files],
        }
        (OUTPUTS / "article.md").write_text(markdown_document(data), encoding="utf-8")
        (OUTPUTS / "article.html").write_text(html_document(data), encoding="utf-8")
        (OUTPUTS / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        LOG.info("生成完成：outputs/article.md、article.html、metadata.json")
        return 0
    except Exception as exc:
        LOG.error("文章生成失败：%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
