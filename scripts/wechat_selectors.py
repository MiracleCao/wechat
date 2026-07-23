"""微信公众号编辑器候选选择器。

页面改版后只需修改本文件。每类元素按优先级提供多个 CSS/XPath 候选。
"""

HOME_READY = [
    "text=新的创作",
    "text=图文消息",
    ".weui-desktop-layout__main",
]

NEW_ARTICLE = [
    ".new-creation__item:has-text('文章')",
    ".weui-desktop-card__inner:has-text('文章')",
    "[class*='create'] [class*='item']:has-text('文章')",
    "text=文章",
    "a:has-text('文章')",
    "button:has-text('文章')",
]

TEMPLATE_TAB = [
    "#js_editor_insert_template",
    ".js_editor_insert_template",
    ".edui-for-template",
    "li:has-text('模板')",
    "text=模板",
    "button:has-text('模板')",
]

SIX_IMAGE_TEMPLATE_CARD = [
    "h4:has-text('Template_图片刚好六张用这个')",
    "text=Template_图片刚好六张用这个",
]

OTHER_IMAGE_TEMPLATE_CARD = [
    "h4:has-text('Template_图片不为六张用这个')",
    "text=Template_图片不为六张用这个",
]

TEMPLATE_SELECTED = [
    "text=已选择",
]

ADD_TO_BODY = [
    "button:has-text('添加到正文')",
    "a:has-text('添加到正文')",
    "text=添加到正文",
]

HYPERLINK = [
    "button:has-text('超链接')",
    "[title='超链接']",
    "text=超链接",
]

RECENT_ARTICLES = [
    ".weui-desktop-mass-appmsg__bd .weui-desktop-radio",
    ".weui-desktop-dialog__bd [role='radio']",
    ".weui-desktop-dialog__bd li",
]

COMPLETE_LINK = [
    "button:has-text('完成')",
    "a:has-text('完成')",
]

TITLE = [
    "#title",
    "input[placeholder*='标题']",
    "textarea[placeholder*='标题']",
    "[contenteditable='true'][data-placeholder*='标题']",
]

SUMMARY = [
    "textarea[placeholder*='摘要']",
    "textarea[placeholder*='选填']",
    "#js_description",
]

BODY = [
    ".ProseMirror[contenteditable='true']",
    "#ueditor_0[contenteditable='true']",
    "[contenteditable='true'].edui-body-container",
]

SAVE_DRAFT = [
    "button:has-text('保存为草稿')",
    "button:has-text('保存草稿')",
    "a:has-text('保存为草稿')",
]

FORBIDDEN_ACTION_TEXT = (
    "发表",
    "发布",
    "群发",
    "发送",
    "提交审核",
    "审核发布",
)
