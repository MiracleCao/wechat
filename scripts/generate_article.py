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
guide, figures, conclusion, summary, alternative_titles, keywords,
cover_image_suggestion, publish_time_suggestion。
figures 必须恰好为 6 个对象，每个对象含 label 和 description；优先选择能构成完整证据链的图，
若素材不足以确认 6 幅图，必须报错字段 error 说明缺失，不得猜测。summary 不超过 120 个汉字。
每个 figure.label 必须保留论文中的原始图号（如“图2”“图5”），不得按照入选顺序重新编号。
corresponding_affiliation 必须翻译成中文；figure.description 不得以“图N：”开头，
只写说明性正文。
alternative_titles 必须恰好 10 个，每个至少 25 个字。若通讯作者单位在中国，格式为
“机构名 | 刊名缩写 | 描述”；若通讯作者单位不在中国，省略机构名，格式为“刊名缩写 | 描述”。
标题应准确说明创新点和主要工作，不夸张。guide 用一整段介绍研究背景和主要工作；
每个 figure description 用一整段；conclusion 用一整段。"""


def read_materials() -> tuple[list[Path], str]:
    files = sorted([
        path for path in MATERIALS.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED and path.name.lower() != "readme.md"
    ], key=material_sort_key)
    if not files:
        raise FileNotFoundError("materials/ 中没有论文素材；请先放入 PDF、Markdown 或文本文件")
    image_files = [path for path in files if path.suffix.lower() in SUPPORTED_IMAGES]
    if image_files and len(image_files) != 6:
        raise ValueError(
            f"检测到 {len(image_files)} 张重点图片；使用图片模式时请恰好提供 6 张"
        )

    chunks: list[str] = []
    for path in files:
        LOG.info("读取素材：%s", path.relative_to(ROOT))
        if path.suffix.lower() in SUPPORTED_IMAGES:
            chunks.append(f"\n===== 用户提供的重点图片：{path.name} =====")
            continue
        if path.suffix.lower() == ".pdf":
            reader = PdfReader(path)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            text = path.read_text(encoding="utf-8")
        if text.strip():
            chunks.append(f"\n===== {path.name} =====\n{text.strip()}")
    if not chunks:
        raise ValueError("素材文件存在，但没有提取到可用文本；扫描版 PDF 请先进行 OCR")
    return files, "\n".join(chunks)


def parse_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    return json.loads(cleaned)


def validate(data: dict) -> None:
    if data.get("error"):
        raise ValueError(f"素材不足：{data['error']}")
    required = (
        "institution", "journal_abbr", "guide", "figures", "conclusion", "summary",
        "doi_url", "original_title", "translated_title", "corresponding_affiliation",
        "corresponding_country", "total_figure_count",
        "alternative_titles", "keywords", "cover_image_suggestion", "publish_time_suggestion",
    )
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise ValueError("模型输出缺少字段：" + ", ".join(missing))
    if len(data["figures"]) != 6:
        raise ValueError("重点图解读必须恰好 6 项")
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
        "## 导读", "", data["guide"], "", "## 六幅关键图解读", "",
    ]
    for figure in data["figures"]:
        parts.extend([f"**{figure['label']}：**{figure['description']}", ""])
    parts.extend(["## 主要结论", "", data["conclusion"], "", "## 120 字内概括", "", data["summary"],
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
    return f"""<section style="max-width:680px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;color:#222;">
<h1 style="font-size:24px;line-height:1.45;margin:20px 0;">{esc(data["alternative_titles"][0])}</h1>
<p style="line-height:1.85;color:#555;">DOI：{esc(data["doi_url"])}</p>
<h2 style="font-size:19px;border-left:4px solid #1f5f8b;padding-left:10px;margin-top:28px;">导读</h2>
<p style="line-height:1.85;color:#333;">{esc(data["guide"])}</p>
<h2 style="font-size:19px;border-left:4px solid #1f5f8b;padding-left:10px;margin-top:28px;">六幅关键图解读</h2>
{figure_html}
<h2 style="font-size:19px;border-left:4px solid #1f5f8b;padding-left:10px;margin-top:28px;">主要结论</h2>
<p style="line-height:1.85;color:#333;">{esc(data["conclusion"])}</p>
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
        files, material_text = read_materials()
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
                    "若提供了图片，figures 必须按图片文件名排序逐一解读：\n"
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
        validate(data)
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
