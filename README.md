# 微信公众号论文文章自动化工作流

## 项目结构

```text
materials/                     论文 PDF、补充材料、术语表
outputs/
  article.md                   Markdown 文章（生成后出现）
  article.html                 公众号友好的内联样式 HTML（生成后出现）
  metadata.json                标题、摘要、关键词等（生成后出现）
  debug_screenshot.png         定位失败时的页面截图
scripts/
  generate_article.py          读取素材并生成三种内容产物
  fill_wechat_editor.py        Playwright 安全填充脚本
  wechat_selectors.py          集中维护 CSS/XPath 选择器
requirements.txt
```

## 实现思路

1. `generate_article.py` 扫描 `materials/` 中的 PDF、Markdown 和文本文件，提取可检索正文。
2. 模型严格依据素材生成导读、6 幅重点图解读、主要结论、120 字内摘要及 10 个候选标题。
3. 脚本校验字段、图解数量、标题数量和摘要长度，再输出 Markdown、内联样式 HTML 和 JSON。
4. `fill_wechat_editor.py` 打开指定网址，等待人工登录，进入“新的创作 → 文章”，选择
   `Template_图片刚好六张用这个` 并添加到正文。
5. 脚本按模板锚点填入文章导读、六段带序号图解、文章结论和 120 字概括，并插入三条不重复的最近文章链接。
6. 默认不保存；只有显式传入 `--save-draft` 才保存草稿。发布、群发、发送和提交审核始终被安全策略拦截。

## 安装依赖

要求 Python 3.10+：

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 生成文章

先把论文 PDF 或文本素材放入 `materials/`。API 密钥只放在当前终端环境变量中，不写入项目：

```powershell
$env:OPENAI_API_KEY="你的 API Key"
python scripts/generate_article.py
```

可选参数：

```powershell
python scripts/generate_article.py --model gpt-5.2
```

若没有真实素材，脚本会直接停止，不生成虚构内容。成功后得到：

- `outputs/article.md`
- `outputs/article.html`
- `outputs/metadata.json`

## 浏览器填充

默认打开微信公众号后台，只填充、不点击保存：

```powershell
python scripts/fill_wechat_editor.py
```

指定其他编辑器网址：

```powershell
python scripts/fill_wechat_editor.py --url "https://example.com/editor"
```

允许自动保存草稿（自动化操作的上限）：

```powershell
python scripts/fill_wechat_editor.py --save-draft
```

首次运行时请手动完成登录、验证码及安全验证，再回到终端按 Enter。默认使用
`tmp/wechat_chrome_profile/` 作为专用 Chrome 配置目录，关闭后会保留 Cookie 和公众号登录状态，
后续通常无需重复扫码。脚本不会读取或处理账号密码。

如需恢复原来的无痕临时会话：

```powershell
python scripts/fill_wechat_editor.py --temporary-session
```

## 选择器失效时

所有候选选择器都在 `scripts/wechat_selectors.py`。定位失败时会生成
`outputs/debug_screenshot.png`；根据截图和浏览器开发者工具修改对应的 `TITLE`、`SUMMARY`、
`BODY`、`NEW_ARTICLE`、`TEMPLATE_*`、`HYPERLINK`、`RECENT_ARTICLES` 或 `SAVE_DRAFT` 列表。

## 防止误发布

- 代码中没有发布、群发或提交审核按钮的选择器。
- 默认模式不点击任何按钮。
- 所有点击必须经过 `safe_click()`；元素文本、按钮属性或动作名称只要包含发布、群发、发送、提交审核等词就会被拒绝。
- `--save-draft` 仅开放白名单动作“保存草稿”，不会放宽其他限制。
- 最终发布必须由人在本脚本之外手动完成。
