"""Offline Markdown HTML and the browser plugin's *public* rendering API."""

from __future__ import annotations

import html
from typing import Any

from markdown_it import MarkdownIt

from .processing import Settings

_STYLE = """
* { box-sizing: border-box; }
html, body { margin: 0; background: #fff; color: #243047; }
body { font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', 'PingFang SC',
    'WenQuanYi Micro Hei', sans-serif; font-size: FONT_SIZEpx; line-height: 1.7; }
main { padding: 32px 40px; overflow-wrap: anywhere; }
h1,h2,h3,h4 { color: #17233a; line-height: 1.4; margin: 1em 0 .55em; }
main > :first-child { margin-top: 0; }
main > :last-child { margin-bottom: 0; }
p { margin: .6em 0; white-space: pre-wrap; }
a { color: #2563eb; text-decoration: none; overflow-wrap: anywhere; }
pre { background: #f3f5f9; border: 1px solid #e0e5ed; border-radius: 8px;
    padding: 16px; white-space: pre-wrap; overflow-wrap: anywhere;
    break-inside: auto; }
code { font-family: 'Cascadia Code', Consolas, 'DejaVu Sans Mono', monospace;
    font-size: .88em; background: #f3f5f9; }
pre code { background: transparent; }
blockquote { margin: .8em 0; padding: 4px 18px; border-left: 4px solid #8ba3cd;
    background: #f5f7fb; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; table-layout: fixed; }
th,td { padding: 8px 12px; border: 1px solid #cbd5e1; overflow-wrap: anywhere; }
th { background: #eff3f9; }
tr { break-inside: avoid; }
ul,ol { padding-left: 1.6em; }
hr { border: 0; border-top: 1px solid #cbd5e1; }
.image-placeholder { color: #64748b; }
@page { size: A4; margin: 12mm; }
@media print {
    body { font-size: 12pt; }
    main { padding: 0; }
    h1,h2,h3,h4 { break-after: avoid; }
    thead { display: table-header-group; }
}
"""


def _image_placeholder(tokens, index, options, env) -> str:
    # No fetching LLM-provided URLs, data URLs, or local file paths.
    alt = tokens[index].content or "图片"
    return '<span class="image-placeholder">[图片：' + html.escape(alt) + "]</span>"


class MarkdownDocument:
    def __init__(self) -> None:
        self.parser = MarkdownIt(
            "commonmark", {"html": False, "breaks": True, "linkify": False}
        ).enable(["table", "strikethrough"])
        self.parser.renderer.rules["image"] = _image_placeholder

    def contains_markdown(self, text: str) -> bool:
        markdown_tokens = {
            "heading_open", "bullet_list_open", "ordered_list_open",
            "blockquote_open", "fence", "code_block", "code_inline",
            "em_open", "strong_open", "s_open", "link_open", "image",
            "hr", "table_open",
        }
        pending = list(self.parser.parse(text))
        while pending:
            token = pending.pop()
            if token.type in markdown_tokens:
                return True
            if token.children:
                pending.extend(token.children)
        return False

    def to_html(self, text: str, settings: Settings) -> str:
        body = self.parser.render(text)
        style = _STYLE.replace("FONT_SIZE", str(settings.font_size))
        return (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="'
            "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; "
            "font-src 'none'; base-uri 'none'; form-action 'none'\">"
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>模型回复</title><style>" + style + "</style></head>"
            '<body><main id="reply">' + body + "</main></body></html>"
        )


class BrowserRenderer:
    """Resolve the current service on every request (safe across plugin reload)."""

    def __init__(self, context: Any, settings: Settings) -> None:
        self.context = context
        self.settings = settings
        self.document = MarkdownDocument()

    def get_service(self):
        metadata = self.context.get_registered_star("astrbot_plugin_browser")
        if (
            metadata is None
            or not metadata.activated
            or metadata.star_cls is None
        ):
            raise RuntimeError("请先安装并启用 astrbot_plugin_browser")
        service = getattr(metadata.star_cls, "service", None)
        if service is None or not service.ready:
            raise RuntimeError("astrbot_plugin_browser 渲染服务尚未就绪")
        return service

    async def render(self, text: str, kind: str) -> bytes:
        service = self.get_service()
        document = self.document.to_html(text, self.settings)
        common = {
            "viewport": {"width": self.settings.image_width, "height": 720},
            "javascript_enabled": False,
            "timeout": self.settings.render_timeout,
        }
        if kind == "pdf":
            return await service.render_pdf(
                document,
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                margin={side: "12mm" for side in ("top", "right", "bottom", "left")},
                **common,
            )
        if kind != "image":
            raise ValueError(f"未知渲染类型：{kind}")
        return await service.render_html(
            document, selector="#reply", full_page=True,
            max_pixels=32000000, **common,
        )
