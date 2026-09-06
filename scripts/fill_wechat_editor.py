"""按图片数量选择指定模板填充微信公众号文章，最多保存为草稿。"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

from article_summary import (
    research_summary_items,
    validate_background_quotes,
    validate_research_summary,
)
from wechat_selectors import (
    ADD_TO_BODY,
    BODY,
    COMPLETE_LINK,
    FORBIDDEN_ACTION_TEXT,
    HOME_READY,
    HYPERLINK,
    NEW_ARTICLE,
    RECENT_ARTICLES,
    SAVE_DRAFT,
    SUMMARY,
    OTHER_IMAGE_TEMPLATE_CARD,
    SIX_IMAGE_TEMPLATE_CARD,
    TEMPLATE_SELECTED,
    TEMPLATE_TAB,
    TITLE,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
MATERIALS = ROOT / "materials"
CHROME_PROFILE = ROOT / "tmp" / "wechat_chrome_profile"
SCREENSHOT = OUTPUTS / "debug_screenshot.png"
DEFAULT_URL = "https://mp.weixin.qq.com"
SIX_IMAGE_TEMPLATE_NAME = "Template_图片刚好六张用这个"
OTHER_IMAGE_TEMPLATE_NAME = "Template_图片不为六张用这个"
MIN_RECENT_LINKS = 4
SUMMARY_LIMIT = 120

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
LOG = logging.getLogger("wechat-template-filler")

ALLOWED_CLICK_ACTIONS = {
    "进入文章编辑器",
    "打开模板面板",
    "选择文章模板",
    "添加模板到正文",
    "打开超链接面板",
    "选择往期文章",
    "完成超链接",
    "保存草稿",
}


def first_visible(page: Page, selectors: Iterable[str], timeout_ms: int = 1_500) -> Locator:
    tried: list[str] = []
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            locator.wait_for(state="visible", timeout=timeout_ms)
            LOG.info("定位成功：%s", selector)
            return locator
        except PlaywrightTimeoutError:
            tried.append(selector)
    raise RuntimeError("所有候选选择器均失效：" + " | ".join(tried))


def safe_click(locator: Locator, action_name: str, *, force: bool = False) -> None:
    if action_name not in ALLOWED_CLICK_ACTIONS:
        raise RuntimeError(f"动作不在点击白名单中：{action_name}")
    text = (locator.inner_text(timeout=2_000) or "").strip()
    attrs = " ".join(locator.get_attribute(name) or "" for name in ("aria-label", "title", "value"))
    evidence = f"{action_name} {text} {attrs}"
    if any(word in evidence for word in FORBIDDEN_ACTION_TEXT):
        raise RuntimeError(f"安全策略拒绝点击：{evidence!r}")
    if action_name == "保存草稿" and "草稿" not in evidence:
        raise RuntimeError(f"目标没有明确标识为草稿按钮：{evidence!r}")
    LOG.info("点击：%s%s", action_name, "（绕过模板卡片蒙层）" if force else "")
    locator.click(force=force, timeout=5_000)


def load_article() -> dict:
    path = OUTPUTS / "metadata.json"
    if not path.is_file():
        raise FileNotFoundError("缺少 outputs/metadata.json，请先生成文章")
    data = json.loads(path.read_text(encoding="utf-8"))
    required = (
        "title",
        "summary",
        "original_title",
        "translated_title",
        "corresponding_affiliation",
        "corresponding_country",
        "total_figure_count",
        "doi_url",
        "guide",
        "figures",
        "conclusion",
        "research_summary",
    )
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise ValueError("metadata.json 缺少模板填充字段：" + ", ".join(missing))
    validate_research_summary(data["research_summary"])
    if not re.fullmatch(r"https://doi\.org/10\.\d{4,9}/\S+", data["doi_url"]):
        raise ValueError("metadata.json 中 doi_url 不是标准 DOI 地址")
    if len(data["summary"]) > SUMMARY_LIMIT:
        raise ValueError(
            f"摘要共 {len(data['summary'])} 字符，超过简介框 "
            f"{SUMMARY_LIMIT} 字符限制"
        )
    figure_count = len(data["figures"])
    if not 1 <= figure_count <= 6:
        raise ValueError("metadata.json 中 figures 必须包含一至六项")
    numbered_images: list[tuple[int, Path]] = []
    for image in MATERIALS.rglob("*"):
        if not image.is_file() or image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        match = re.search(
            r"(?:^|[-_])(?:gr(?:aphic)?|fig(?:ure)?)[-_]?(\d+)(?:[-_]|$)",
            image.stem,
            flags=re.I,
        )
        if not match:
            match = re.search(r"^图[-_ ]?(\d+)(?:[-_ ]|$)", image.stem)
        if match:
            numbered_images.append((int(match.group(1)), image))
    numbered_images.sort(key=lambda item: (item[0], item[1].name.lower()))
    data["selected_figure_numbers"] = [number for number, _ in numbered_images]
    if len(data["selected_figure_numbers"]) != figure_count:
        raise ValueError(
            f"无法确定全部 {figure_count} 个入选图号：请确认 materials/ 中有一至六张图片，"
            "图片数量与 figures 一致，且文件名包含 gr1、fig1 或 图1 等原始图号"
        )
    LOG.info(
        "已从 materials/ 识别入选图号：%s",
        "、".join(map(str, data["selected_figure_numbers"])),
    )

    caption_numbers: list[int] = []
    pdf_texts: list[str] = []
    for pdf in MATERIALS.rglob("*.pdf"):
        text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
        pdf_texts.append(text)
        caption_numbers.extend(
            int(value)
            for value in re.findall(
                r"(?im)^\s*(?:figure|fig\.?)\s*(\d+)\b",
                text,
            )
        )
    doi = data["doi_url"].removeprefix("https://doi.org/")
    if not pdf_texts or not any(doi.lower() in text.lower() for text in pdf_texts):
        raise ValueError(f"metadata.json 中 DOI 未出现在 materials/ 的论文 PDF：{doi}")
    LOG.info("已从论文 PDF 核验 DOI：%s", data["doi_url"])
    validate_background_quotes(data["research_summary"], pdf_texts)
    LOG.info("已从论文 PDF 核验文章总结中的关键背景引句")
    if caption_numbers:
        data["total_figure_count"] = max(caption_numbers)
        LOG.info("已从论文正文图注识别原文共有图%d", data["total_figure_count"])
    else:
        LOG.warning("未从 PDF 识别到 Fig. 图注，将沿用 metadata.json 中的图总数")
    LOG.info("已读取结构化文章：%s", data["title"])
    return data


def collect_recent_links(page: Page) -> list[dict[str, str]]:
    """从公众号首页读取至少四篇文章，并为每个 URL 选择真实标题。"""
    links = page.evaluate(
        """() => {
            const byUrl = new Map();
            const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
            for (const a of document.querySelectorAll('a[href]')) {
                const url = a.href || '';
                if (!/^https:\\/\\/mp\\.weixin\\.qq\\.com\\/s\\//.test(url)) continue;
                const candidates = [
                    [a.getAttribute('data-title'), 600],
                    [a.querySelector('[data-title]')?.getAttribute('data-title'), 550],
                    [a.getAttribute('title'), 500],
                    [a.querySelector('.weui-desktop-mass__title, .article_title, .title')?.textContent, 450],
                    [a.getAttribute('aria-label'), 300],
                    [a.textContent, 100],
                ].map(([value, priority]) => ({title: clean(value), priority}))
                    .filter(item =>
                        item.title &&
                        !/^(查看|阅读|详情|封面|图片)$/.test(item.title)
                    );
                candidates.sort((left, right) =>
                    right.priority - left.priority ||
                    right.title.length - left.title.length
                );
                const choice = candidates[0];
                const title = choice?.title || '';
                if (!title) continue;
                const previous = byUrl.get(url);
                if (!previous || choice.priority > previous.priority ||
                    (choice.priority === previous.priority &&
                     title.length > previous.title.length)) {
                    byUrl.set(url, {title, url, priority:choice.priority});
                }
            }
            return Array.from(byUrl.values())
                .slice(0, 4)
                .map(({title, url}) => ({title, url}));
        }"""
    )
    if len(links) < MIN_RECENT_LINKS:
        raise RuntimeError(
            f"公众号首页只识别到 {len(links)} 篇带标题的公开文章链接，"
            f"至少需要 {MIN_RECENT_LINKS} 篇"
        )
    LOG.info("已从公众号首页读取%d篇不重复的往期文章及标题", len(links))
    return links


def active_body(page: Page) -> Locator:
    """识别真正的正文 ProseMirror，避开标题等空编辑区域。"""
    candidates: list[tuple[int, Locator]] = []
    for selector in BODY:
        group = page.locator(selector)
        for index in range(group.count()):
            candidate = group.nth(index)
            if not candidate.is_visible():
                continue
            text = (candidate.inner_text(timeout=2_000) or "").strip()
            if "本公众号解读的文献" in text:
                LOG.info("已根据模板免责声明定位正文编辑器：%s[%d]", selector, index)
                return candidate
            candidates.append((len(text), candidate))
    if candidates:
        length, candidate = max(candidates, key=lambda item: item[0])
        if length >= 100:
            LOG.info("已根据正文长度定位编辑器，当前文本长度：%d", length)
            return candidate
    raise RuntimeError("没有找到包含已插入模板的正文编辑器")


def insert_after_marker(body: Locator, heading: str, marker: str, text: str) -> None:
    """在正文中指定标题之后的 marker 文本末尾插入内容。"""
    result = body.evaluate(
        """(root, args) => {
            const elements = Array.from(root.querySelectorAll('p,div,section,span'));
            const headingNode = elements.find(el => el.textContent.trim() === args.heading);
            if (!headingNode) return {ok:false, reason:`未找到标题：${args.heading}`};
            const headingTop = headingNode.getBoundingClientRect().top;
            const markerNode = elements.find(el => {
                const top = el.getBoundingClientRect().top;
                return top >= headingTop && el.textContent.trim() === args.marker;
            });
            if (!markerNode) return {ok:false, reason:`未找到占位文本：${args.marker}`};
            markerNode.append(document.createTextNode(args.text));
            markerNode.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText'}));
            return {ok:true};
        }""",
        {"heading": heading, "marker": marker, "text": text},
    )
    if not result["ok"]:
        raise RuntimeError(result["reason"])
    LOG.info("已填充区块：%s", heading)


def fill_six_figure_cards(body: Locator, figures: list[dict]) -> None:
    result = body.evaluate(
        """(root, figures) => {
            const heading = Array.from(root.querySelectorAll('*'))
                .find(el => el.textContent.trim() === '图文解读');
            if (!heading) return {ok:false, reason:'未找到“图文解读”'};
            const candidates = Array.from(root.querySelectorAll('p,div,span'))
                .filter(el => el.textContent.trim() === '图' &&
                    el.getBoundingClientRect().top >= heading.getBoundingClientRect().top);
            if (candidates.length < figures.length) {
                return {ok:false, reason:`只找到 ${candidates.length} 个“图”占位符`};
            }
            figures.forEach((item, index) => {
                const label = item.label || `图${index + 1}`;
                candidates[index].append(document.createTextNode(
                    `${index + 1}｜${label}：${item.description}`
                ));
                candidates[index].dispatchEvent(new InputEvent(
                    'input', {bubbles:true, inputType:'insertText'}
                ));
            });
            return {ok:true};
        }""",
        figures,
    )
    if not result["ok"]:
        raise RuntimeError(result["reason"])
    LOG.info("%d 张图解读已按图片顺序填充", len(figures))


def place_caret_after_recommendation(body: Locator) -> None:
    result = body.evaluate(
        """root => {
            const nodes = Array.from(root.querySelectorAll('*'));
            const heading = nodes.find(el => el.textContent.trim() === '往期推荐');
            if (!heading) return {ok:false, reason:'未找到“往期推荐”'};
            const block = heading.closest('section,div') || heading.parentElement;
            const editable = block.querySelector('p:empty, p br') || block.lastElementChild;
            if (!editable) return {ok:false, reason:'未找到往期推荐空行'};
            const range = document.createRange();
            range.selectNodeContents(editable);
            range.collapse(false);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            editable.focus();
            return {ok:true};
        }"""
    )
    if not result["ok"]:
        raise RuntimeError(result["reason"])


def add_recent_links(page: Page, body: Locator) -> None:
    used_titles: set[str] = set()
    for index in range(3):
        place_caret_after_recommendation(body)
        pages_before = set(page.context.pages)
        safe_click(first_visible(page, HYPERLINK, 3_000), "打开超链接面板")
        page.wait_for_timeout(800)
        popup_pages = [item for item in page.context.pages if item not in pages_before]
        picker = popup_pages[-1] if popup_pages else page
        if picker is not page:
            picker.wait_for_load_state("domcontentloaded")
            LOG.info("超链接选择器已在新窗口打开")
        else:
            LOG.info("超链接选择器在当前编辑器内打开")

        options = None
        for selector in RECENT_ARTICLES:
            candidate = picker.locator(selector)
            if candidate.count() > 0:
                options = candidate
                break
        if options is None:
            raise RuntimeError("没有找到最近文章列表，请更新 RECENT_ARTICLES 选择器")

        chosen = None
        for candidate in options.all():
            title = (candidate.inner_text(timeout=2_000) or "").strip()
            if title and title not in used_titles:
                chosen = candidate
                used_titles.add(title)
                break
        if chosen is None:
            raise RuntimeError("无法找到未重复的往期文章")
        safe_click(chosen, "选择往期文章")
        safe_click(first_visible(picker, COMPLETE_LINK, 5_000), "完成超链接")
        page.bring_to_front()
        body.press("End")
        body.press("Enter")
        LOG.info("已插入第 %d 条不重复往期文章链接", index + 1)


def open_editor(page: Page) -> Page:
    try:
        if urlparse(page.url).path != "/cgi-bin/appmsg":
            raise RuntimeError("当前 URL 不是文章编辑器")
        first_visible(page, TITLE, 1_500)
        return page
    except RuntimeError:
        pass
    entry = first_visible(page, NEW_ARTICLE, 5_000)
    LOG.info("点击文章入口并监听新页面事件")
    try:
        with page.context.expect_page(timeout=12_000) as page_info:
            safe_click(entry, "进入文章编辑器")
        editor = page_info.value
        # 微信会先创建 about:blank，再异步跳转至编辑器；仅等待
        # domcontentloaded 会在空白页上立即返回，导致过早判定失败。
        editor.wait_for_url(
            "**/cgi-bin/appmsg**", wait_until="domcontentloaded", timeout=12_000
        )
        if urlparse(editor.url).path != "/cgi-bin/appmsg":
            raise RuntimeError(f"新标签页不是文章编辑器：{editor.url}")
        first_visible(editor, TITLE, 5_000)
        LOG.info("文章编辑器已在新标签页打开：%s", editor.url)
        return editor
    except PlaywrightTimeoutError:
        LOG.warning("新页面未在时限内进入文章编辑器，检查当前页面是否已跳转")
        if urlparse(page.url).path == "/cgi-bin/appmsg":
            first_visible(page, TITLE, 5_000)
            LOG.info("文章编辑器已在当前标签页打开：%s", page.url)
            return page

    # 微信首页的“文章”卡片依赖页面脚本打开新窗口，在部分自动化
    # 环境中会被浏览器弹窗策略拦截。此时使用当前登录会话中的动态
    # token 进入微信官方新建文章地址，不涉及账号、Cookie 或验证绕过。
    current = urlparse(page.url)
    token = parse_qs(current.query).get("token", [""])[0]
    if not token:
        raise RuntimeError(
            "点击“文章”后未进入编辑器，且当前网址中没有登录 token；"
            "请确认已经登录公众号首页"
        )
    editor_url = (
        "https://mp.weixin.qq.com/cgi-bin/appmsg"
        "?t=media/appmsg_edit_v2"
        "&action=edit"
        "&isNew=1"
        "&type=77"
        "&createType=0"
        f"&token={token}"
        "&lang=zh_CN"
    )
    LOG.warning("首页入口未打开，改用当前登录会话直接进入官方文章编辑地址")
    page.goto(editor_url, wait_until="domcontentloaded")
    first_visible(page, TITLE, 15_000)
    LOG.info("文章编辑器已打开：%s", page.url)
    return page


def batch_fill_template(page: Page, data: dict) -> None:
    """一次性填充除往期链接之外的全部字段。"""
    data = dict(data)
    data["research_summary_items"] = {
        section: research_summary_items(data["research_summary"], section)
        for section in ("why", "how", "next")
    }
    body = active_body(page)
    result = body.evaluate(
        """(root, data) => {
            const paragraphs = Array.from(root.querySelectorAll('p'));
            const exact = value => paragraphs.filter(
                node => (node.textContent || '').trim() === value
            );
            const markers = exact('本文');
            const figureMarkers = exact('图');
            if (markers.length < 2) {
                return {ok:false, reason:`只找到 ${markers.length} 个“本文”占位符`};
            }
            if (figureMarkers.length < data.figures.length) {
                return {ok:false, reason:`只找到 ${figureMarkers.length} 个“图”占位符`};
            }

            const append = (node, text) => {
                node.append(document.createTextNode(text));
                node.dispatchEvent(new InputEvent('input', {
                    bubbles:true, inputType:'insertText', data:text
                }));
            };

            const appendSummaryItems = (node, items) => {
                items.forEach(item => {
                    node.append(document.createElement('br'));
                    const label = document.createElement('span');
                    label.textContent = `${item.number}. ${item.label}：`;
                    label.style.color = '#007aaa';
                    label.style.fontWeight = '700';
                    node.append(label);
                    const text = document.createElement('span');
                    text.textContent = item.text;
                    text.style.fontWeight = '400';
                    node.append(text);
                });
                node.dispatchEvent(new InputEvent('input', {
                    bubbles:true,
                    inputType:'insertText',
                    data:items.map(item =>
                        `${item.number}. ${item.label}：${item.text}`
                    ).join('\\n')
                }));
            };

            const replacePlaceholder = (node, placeholder, text) => {
                const walker = document.createTreeWalker(
                    node, NodeFilter.SHOW_TEXT
                );
                let current;
                while ((current = walker.nextNode())) {
                    const index = current.data.indexOf(placeholder);
                    if (index < 0) continue;
                    current.data =
                        current.data.slice(0, index) +
                        text +
                        current.data.slice(index + placeholder.length);
                    node.dispatchEvent(new InputEvent('input', {
                        bubbles:true,
                        inputType:'insertReplacementText',
                        data:text
                    }));
                    return true;
                }
                return false;
            };

            const insertAfterText = (node, anchor, text) => {
                const walker = document.createTreeWalker(
                    node, NodeFilter.SHOW_TEXT
                );
                let current;
                while ((current = walker.nextNode())) {
                    const index = current.data.indexOf(anchor);
                    if (index < 0) continue;
                    const position = index + anchor.length;
                    current.data =
                        current.data.slice(0, position) +
                        text +
                        current.data.slice(position);
                    node.dispatchEvent(new InputEvent('input', {
                        bubbles:true,
                        inputType:'insertText',
                        data:text
                    }));
                    return true;
                }
                return false;
            };

            const articleInfo = [
                ['原文标题', data.original_title],
                ['标题翻译', data.translated_title],
                ['通讯作者单位', data.corresponding_affiliation],
            ];
            for (const [label, value] of articleInfo) {
                const node = paragraphs.find(item =>
                    (item.textContent || '').trim().startsWith(label)
                );
                if (!node) {
                    return {ok:false, reason:`未找到“${label}”信息栏`};
                }
                append(node, value);
            }

            const figureSummary = paragraphs.find(node =>
                (node.textContent || '').trim().startsWith('原文共有图')
            );
            if (!figureSummary) {
                return {ok:false, reason:'未找到“原文共有图”说明行'};
            }
            const selectedFigureNumbers = data.selected_figure_numbers.map(String);
            if (selectedFigureNumbers.some(value => !value)) {
                return {ok:false, reason:'入选图片缺少论文原始图号'};
            }
            // 模板已写好固定句式，只在两个“图”字后填写总数和入选图号。
            if (!insertAfterText(
                figureSummary, '原文共有图', String(data.total_figure_count)
            )) {
                return {ok:false, reason:'无法在“原文共有图”后填写图总数'};
            }
            if (!insertAfterText(
                figureSummary,
                '本文图片选自原文中图',
                selectedFigureNumbers.join('、')
            )) {
                return {ok:false, reason:'无法在“本文图片选自原文中图”后填写图号'};
            }

            // 用正文直接替换定位词，兼容“本文”位于嵌套样式节点的模板。
            if (!replacePlaceholder(markers[0], '本文', data.guide)) {
                return {ok:false, reason:'无法替换文章导读中的“本文”占位符'};
            }

            const figureSlots = figureMarkers.slice(-data.figures.length);
            data.figures.forEach((item, index) => {
                // 模板已含“图”，追加论文原始图号，不按入选顺序重新编号。
                const originalNumber = String(item.label || '')
                    .replace(/^图(?:片)?\\s*/i, '')
                    .trim();
                if (!originalNumber) {
                    throw new Error(`第 ${index + 1} 幅图缺少论文原始图号`);
                }
                // 将纯文本追加到“图”字所在的同一带样式节点，继承原格式。
                const styledNodes = Array.from(figureSlots[index].querySelectorAll('*'))
                    .filter(node => (node.textContent || '').trim() === '图');
                const target = styledNodes.length
                    ? styledNodes[styledNodes.length - 1]
                    : figureSlots[index];
                // description 可能来自旧数据，先移除其中重复的“图N：”前缀。
                const description = String(item.description || '')
                    .replace(/^图(?:片)?\\s*\\d+\\s*[：:]\\s*/i, '')
                    .trim();
                append(target, `${originalNumber}：${description}`);
            });

            if (!replacePlaceholder(
                markers[markers.length - 1], '本文', data.conclusion
            )) {
                return {ok:false, reason:'无法替换文章总结中的“本文”占位符'};
            }

            const researchSummarySections = [
                ['为什么做？', data.research_summary_items.why],
                ['怎么做的？', data.research_summary_items.how],
                ['还需要做什么？', data.research_summary_items.next],
            ];
            for (const [heading, items] of researchSummarySections) {
                const node = paragraphs.find(item =>
                    (item.textContent || '').trim().startsWith(heading)
                );
                if (!node) {
                    return {ok:false, reason:`未找到“${heading}”文章总结栏`};
                }
                appendSummaryItems(node, items);
            }

            const disclaimer = paragraphs.find(node =>
                (node.textContent || '').trim().startsWith('①本公众号解读的文献')
            );
            if (!disclaimer) {
                return {ok:false, reason:'未找到往期推荐下方的免责声明定位点'};
            }
            if (!Array.isArray(data.recent_links) ||
                data.recent_links.length < 4) {
                return {ok:false, reason:'往期文章链接少于四条'};
            }
            const markerCandidates = Array.from(root.querySelectorAll('p'))
                .filter(node =>
                    (node.textContent || '').trim() === '1' &&
                    Boolean(node.compareDocumentPosition(disclaimer) &
                        Node.DOCUMENT_POSITION_FOLLOWING)
                );
            const linkMarker = markerCandidates[markerCandidates.length - 1];
            if (!linkMarker) {
                return {ok:false, reason:'未找到免责声明之前内容恰好为“1”的链接插入标记'};
            }
            const linkFragment = document.createDocumentFragment();
            data.recent_links.forEach(item => {
                const paragraph = document.createElement('p');
                const anchor = document.createElement('a');
                anchor.href = item.url;
                anchor.target = '_blank';
                anchor.rel = 'noopener noreferrer';
                anchor.textContent = item.title;
                paragraph.append(anchor);
                linkFragment.append(paragraph);
            });
            linkMarker.parentNode.insertBefore(linkFragment, linkMarker.nextSibling);
            linkMarker.remove();

            const doiText = `DOI：${data.doi_url}`;
            const existingDoi = Array.from(root.querySelectorAll('p')).find(node =>
                (node.textContent || '').trim() === doiText
            );
            if (!existingDoi) {
                const doiParagraph = document.createElement('p');
                doiParagraph.textContent = doiText;
                root.append(doiParagraph);
            }

            root.dispatchEvent(new InputEvent('input', {
                bubbles:true, inputType:'insertText', data:null
            }));
            return {
                ok:true,
                markers:markers.length,
                figures:figureMarkers.length,
                doiAtEnd:(root.lastElementChild?.textContent || '').trim() === doiText
            };
        }""",
        data,
    )
    if not result["ok"]:
        raise RuntimeError(result["reason"])
    if not result["doiAtEnd"]:
        raise RuntimeError("DOI 未处于文章正文末尾")

    title = first_visible(page, TITLE, 5_000)
    title.fill(data["title"])
    summary = first_visible(page, SUMMARY, 5_000)
    summary.fill(data["summary"])
    LOG.info(
        "标题、导读、%d 段图解、主要结论、文章总结三栏、摘要及正文末尾 DOI 已批量填充",
        len(data["figures"]),
    )


def apply_template(page: Page, data: dict) -> None:
    if len(data["figures"]) == 6:
        template_name = SIX_IMAGE_TEMPLATE_NAME
        template_selectors = SIX_IMAGE_TEMPLATE_CARD
    else:
        template_name = OTHER_IMAGE_TEMPLATE_NAME
        template_selectors = OTHER_IMAGE_TEMPLATE_CARD
    safe_click(first_visible(page, TEMPLATE_TAB, 5_000), "打开模板面板")
    template = first_visible(page, template_selectors, 5_000)
    safe_click(template, "选择文章模板", force=True)
    # 当前编辑器会以卡片变灰而非“已选择”文字确认选中状态；
    # 以“添加到正文”按钮可见作为跨版本的可操作确认。
    first_visible(page, ADD_TO_BODY, 5_000)
    LOG.info("模板已选中，添加到正文按钮已就绪")
    safe_click(first_visible(page, ADD_TO_BODY, 5_000), "添加模板到正文")
    active_body(page)
    LOG.info("模板已添加到正文：%s", template_name)


def fill_template(page: Page, data: dict) -> None:
    batch_fill_template(page, data)
    LOG.info("模板全部填充完成；未执行发布、群发或提交审核")


def main() -> int:
    parser = argparse.ArgumentParser(description="按图片数量选择模板填充公众号文章")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--save-draft", action="store_true")
    parser.add_argument(
        "--temporary-session",
        action="store_true",
        help="使用不保留 Cookie 的临时 Chrome 会话",
    )
    args = parser.parse_args()
    data = load_article()
    OUTPUTS.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        if args.temporary_session:
            LOG.info("启动 Google Chrome 临时会话；关闭后不保留 Cookie")
            browser = playwright.chromium.launch(channel="chrome", headless=False)
            context = browser.new_context()
        else:
            CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
            LOG.info("启动 Google Chrome 持久会话：%s", CHROME_PROFILE)
            LOG.info("Cookie 和登录状态将在关闭后保留；脚本不读取或处理账号密码")
            browser = None
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_PROFILE),
                channel="chrome",
                headless=False,
            )
        page = context.new_page()
        try:
            page.goto(args.url, wait_until="domcontentloaded")
            LOG.info("当前页面：%s", page.url)
            try:
                first_visible(page, HOME_READY, 2_000)
                LOG.info("已复用公众号登录状态，无需重新扫码")
            except RuntimeError:
                LOG.info("尚未登录，请手动完成登录及平台安全验证")
                input("登录完成并进入公众号首页后，按 Enter 继续……")
                first_visible(page, HOME_READY, 10_000)
            data["recent_links"] = collect_recent_links(page)
            page = open_editor(page)
            apply_template(page, data)
            fill_template(page, data)
            if args.save_draft:
                safe_click(first_visible(page, SAVE_DRAFT, 5_000), "保存草稿")
                LOG.info("已保存草稿；未发布")
            else:
                LOG.info("默认安全模式：保留在编辑器中，不点击保存按钮")
            input("请检查内容；完成后按 Enter 关闭浏览器……")
            return 0
        except Exception as exc:
            LOG.exception("自动填充失败：%s", exc)
            page.screenshot(path=str(SCREENSHOT), full_page=True)
            LOG.error(
                "已保存截图：%s；请在 scripts/wechat_selectors.py 调整选择器",
                SCREENSHOT,
            )
            return 1
        finally:
            context.close()
            if browser is not None:
                browser.close()


if __name__ == "__main__":
    sys.exit(main())
