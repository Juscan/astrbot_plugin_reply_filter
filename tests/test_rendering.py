"""Markdown safety and async browser-service contract tests (no real browser)."""

from html.parser import HTMLParser
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from astrbot_plugin_reply_filter.processing import Settings
from astrbot_plugin_reply_filter.rendering import BrowserRenderer, MarkdownDocument


class DocumentInspector(HTMLParser):
    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.text = []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


class MarkdownDocumentTests(unittest.TestCase):
    def setUp(self):
        self.document = MarkdownDocument()

    def test_markdown_block_types_are_detected(self):
        examples = {
            "atx heading": "# 标题",
            "setext heading": "标题\n====",
            "bullet list": "- 第一项\n- 第二项",
            "ordered list": "1. first\n2. second",
            "blockquote": "> 引用",
            "fenced code": "```python\nprint(1)\n```",
            "tilde code fence": "~~~\ncode\n~~~",
            "indented code": "    code()",
            "horizontal rule": "---",
            "table": "| A | B |\n| --- | --- |\n| 1 | 2 |",
        }
        for name, text in examples.items():
            with self.subTest(name=name):
                self.assertTrue(self.document.contains_markdown(text))

    def test_markdown_inline_types_are_detected(self):
        examples = {
            "inline code": "运行 `print(1)`",
            "emphasis": "一个 *重点*",
            "underscore emphasis": "一个 _重点_",
            "strong": "一个 **重点**",
            "strikethrough": "一个 ~~删除~~",
            "link": "[网站](https://example.com)",
            "image": "![替代文本](https://example.com/picture.png)",
            "autolink": "<https://example.com>",
            "reference link": "[网站][site]\n\n[site]: https://example.com",
        }
        for name, text in examples.items():
            with self.subTest(name=name):
                self.assertTrue(self.document.contains_markdown(text))

    def test_plain_text_is_not_misclassified_as_markdown(self):
        examples = (
            "", "普通中文回复。", "first line\nsecond line", "issue #123",
            "a * b = c", "2 ** 3 = 8", "snake_case_name", "https://example.com/a?b=1",
            "first.last+tag@example.com", "版本 v1.2.3，价格 3.14",
            "#没有空格不是标题", "1.2.3 is a version", "-5 is negative", "a > b < c",
            "unmatched * marker", r"\*literal stars\*", "<script>alert(1)</script>",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertFalse(self.document.contains_markdown(text))

    def test_generated_html_preserves_expected_markdown_structure(self):
        source = "# 标题\n\n**粗体** ~~删除~~\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n```python\nprint(1)\n```"
        result = self.document.to_html(source, Settings())
        tags = [tag for tag, _ in DocumentInspector(result).elements]
        for expected in ("h1", "strong", "s", "table", "th", "td", "pre", "code"):
            with self.subTest(tag=expected):
                self.assertIn(expected, tags)
        self.assertIn("lang=\"zh-CN\"", result)
        self.assertIn("<main id=\"reply\">", result)

    def test_raw_html_script_and_event_handlers_remain_text(self):
        source = '<script>alert("x")</script>\n<img src="https://evil.test/p.png" onerror="alert(1)">\n<iframe src="file:///secret"></iframe>'
        result = self.document.to_html(source, Settings())
        inspected = DocumentInspector(result)
        tags = [tag for tag, _ in inspected.elements]
        for forbidden in ("script", "img", "iframe"):
            self.assertNotIn(forbidden, tags)
        self.assertIn('&lt;script&gt;', result)
        self.assertIn('<script>alert("x")</script>', "".join(inspected.text))
        self.assertTrue(all(not name.lower().startswith("on") for _, attrs in inspected.elements for name in attrs))

    def test_content_security_policy_blocks_network_and_active_content(self):
        result = self.document.to_html("[外链](https://example.com)", Settings())
        metas = [attrs for tag, attrs in DocumentInspector(result).elements if tag == "meta"]
        csp = next(attrs["content"] for attrs in metas if attrs.get("http-equiv") == "Content-Security-Policy")
        for directive in (
            "default-src 'none'", "style-src 'unsafe-inline'", "img-src 'none'",
            "font-src 'none'", "base-uri 'none'", "form-action 'none'",
        ):
            with self.subTest(directive=directive):
                self.assertIn(directive, csp)

    def test_external_local_and_data_images_never_create_fetchable_elements(self):
        sources = (
            "https://remote.invalid/tracker.png",
            "http://127.0.0.1/private.png",
            "file:///C:/secret.png",
            "data:image/png;base64,AAAA",
            "/local/path.png",
        )
        for source in sources:
            with self.subTest(source=source):
                result = self.document.to_html("![替代文字](" + source + ")", Settings())
                inspected = DocumentInspector(result)
                self.assertNotIn("img", [tag for tag, _ in inspected.elements])
                self.assertFalse(any("src" in attrs or "srcset" in attrs for _, attrs in inspected.elements))
                self.assertNotIn("<script", result.lower())
                # Unsupported protocols may remain literal Markdown, but are never fetched.
                if "image-placeholder" in result.split("</style>", 1)[-1]:
                    self.assertIn("[图片：替代文字]", "".join(inspected.text))

    def test_remote_image_is_replaced_with_an_escaped_alt_placeholder(self):
        source = '![<img src=x onerror=alert(1)> & "caption"](https://example.com/a.png)'
        result = self.document.to_html(source, Settings())
        inspected = DocumentInspector(result)
        self.assertIn("image-placeholder", [attrs.get("class") for _, attrs in inspected.elements])
        self.assertIn('[图片：<img src=x onerror=alert(1)> & "caption"]', "".join(inspected.text))
        self.assertNotIn("img", [tag for tag, _ in inspected.elements])
        self.assertNotIn("https://example.com/a.png", result)

    def test_empty_image_alt_uses_a_readable_placeholder(self):
        result = self.document.to_html("![](https://example.com/a.png)", Settings())
        self.assertIn("[图片：图片]", "".join(DocumentInspector(result).text))

    def test_dangerous_link_schemes_are_not_emitted_as_hrefs(self):
        for scheme in ("javascript:alert%281%29", "vbscript:msgbox%281%29", "file:///etc/passwd", "data:text/html,hello"):
            with self.subTest(scheme=scheme):
                result = self.document.to_html("[危险](" + scheme + ")", Settings())
                self.assertFalse(any("href" in attrs for _, attrs in DocumentInspector(result).elements))

    def test_https_link_is_static_and_html_escaped(self):
        result = self.document.to_html("[外链](https://example.com/?a=1&b=2)", Settings())
        anchors = [attrs for tag, attrs in DocumentInspector(result).elements if tag == "a"]
        self.assertEqual(anchors, [{"href": "https://example.com/?a=1&b=2"}])
        self.assertIn("a=1&amp;b=2", result)
        self.assertNotIn("<script", result.lower())

    def test_fenced_html_code_is_not_executed(self):
        source = '```html\n<script>alert(1)</script>\n<img src="https://example.com/a">\n```'
        result = self.document.to_html(source, Settings())
        tags = [tag for tag, _ in DocumentInspector(result).elements]
        self.assertIn("code", tags)
        self.assertNotIn("script", tags)
        self.assertNotIn("img", tags)

    def test_font_size_is_applied_without_leaving_template_tokens(self):
        result = self.document.to_html("正文", Settings(font_size=28))
        self.assertIn("font-size: 28px", result)
        self.assertNotIn("FONT_SIZE", result)


class BrowserRendererTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_service(image=b"\x89PNG\r\n\x1a\nimage", pdf=b"%PDF-1.7\npdf"):
        return SimpleNamespace(
            ready=True,
            render_html=AsyncMock(return_value=image),
            render_pdf=AsyncMock(return_value=pdf),
        )

    @staticmethod
    def make_metadata(service):
        return SimpleNamespace(activated=True, star_cls=SimpleNamespace(service=service))

    def make_renderer(self, service=None, settings=None):
        service = service if service is not None else self.make_service()
        context = SimpleNamespace(get_registered_star=Mock(return_value=self.make_metadata(service)))
        return BrowserRenderer(context, settings or Settings()), service, context

    async def test_png_uses_public_render_html_contract(self):
        settings = Settings(image_width=1280, render_timeout=12.5)
        renderer, service, context = self.make_renderer(settings=settings)
        actual = await renderer.render("**正文**", "image")
        self.assertEqual(actual, b"\x89PNG\r\n\x1a\nimage")
        document = renderer.document.to_html("**正文**", settings)
        service.render_html.assert_awaited_once_with(
            document, selector="#reply", full_page=True, max_pixels=32000000,
            viewport={"width": 1280, "height": 720}, javascript_enabled=False, timeout=12.5,
        )
        service.render_pdf.assert_not_awaited()
        context.get_registered_star.assert_called_once_with("astrbot_plugin_browser")

    async def test_pdf_uses_public_render_pdf_contract(self):
        settings = Settings(image_width=800, render_timeout=20)
        renderer, service, _ = self.make_renderer(settings=settings)
        actual = await renderer.render("# 文档", "pdf")
        self.assertEqual(actual, b"%PDF-1.7\npdf")
        service.render_pdf.assert_awaited_once_with(
            renderer.document.to_html("# 文档", settings),
            format="A4", print_background=True, prefer_css_page_size=True,
            margin={side: "12mm" for side in ("top", "right", "bottom", "left")},
            viewport={"width": 800, "height": 720}, javascript_enabled=False, timeout=20,
        )
        service.render_html.assert_not_awaited()

    def test_service_is_resolved_from_metadata_star_cls_not_metadata_itself(self):
        renderer, service, context = self.make_renderer()
        decoy = self.make_service()
        context.get_registered_star.return_value.service = decoy
        self.assertIs(renderer.get_service(), service)

    def test_missing_inactive_or_uninstantiated_plugin_is_rejected(self):
        metadata_values = (
            None,
            SimpleNamespace(activated=False, star_cls=SimpleNamespace(service=self.make_service())),
            SimpleNamespace(activated=True, star_cls=None),
        )
        for metadata in metadata_values:
            with self.subTest(metadata=metadata):
                renderer, _, context = self.make_renderer()
                context.get_registered_star.return_value = metadata
                with self.assertRaisesRegex(RuntimeError, "安装并启用"):
                    renderer.get_service()

    def test_missing_or_unready_service_is_rejected(self):
        metadata_values = (
            SimpleNamespace(activated=True, star_cls=SimpleNamespace()),
            self.make_metadata(None),
            self.make_metadata(SimpleNamespace(ready=False)),
        )
        for metadata in metadata_values:
            with self.subTest(metadata=metadata):
                renderer, _, context = self.make_renderer()
                context.get_registered_star.return_value = metadata
                with self.assertRaisesRegex(RuntimeError, "尚未就绪"):
                    renderer.get_service()

    async def test_unknown_kind_is_rejected_without_calling_a_renderer(self):
        renderer, service, _ = self.make_renderer()
        with self.assertRaisesRegex(ValueError, "未知渲染类型"):
            await renderer.render("text", "jpeg")
        service.render_html.assert_not_awaited()
        service.render_pdf.assert_not_awaited()

    async def test_service_exceptions_propagate_for_the_callers_fallback(self):
        renderer, service, _ = self.make_renderer()
        service.render_html.side_effect = TimeoutError("image timed out")
        service.render_pdf.side_effect = RuntimeError("pdf failed")
        with self.assertRaisesRegex(TimeoutError, "image timed out"):
            await renderer.render("text", "image")
        with self.assertRaisesRegex(RuntimeError, "pdf failed"):
            await renderer.render("text", "pdf")

    async def test_service_is_resolved_again_after_plugin_reload(self):
        old_service = self.make_service(image=b"old image")
        new_service = self.make_service(image=b"new image")
        renderer, _, context = self.make_renderer(old_service)
        self.assertEqual(await renderer.render("first", "image"), b"old image")
        old_service.ready = False
        context.get_registered_star.return_value = self.make_metadata(new_service)
        self.assertEqual(await renderer.render("second", "image"), b"new image")
        self.assertEqual(context.get_registered_star.call_count, 2)
        old_service.render_html.assert_awaited_once()
        new_service.render_html.assert_awaited_once()

    async def test_same_plugin_instance_can_replace_its_service(self):
        renderer, old_service, context = self.make_renderer()
        self.assertEqual(await renderer.render("before", "pdf"), b"%PDF-1.7\npdf")
        new_service = self.make_service(pdf=b"%PDF-2.0\nnew")
        context.get_registered_star.return_value.star_cls.service = new_service
        self.assertEqual(await renderer.render("after", "pdf"), b"%PDF-2.0\nnew")
        old_service.render_pdf.assert_awaited_once()
        new_service.render_pdf.assert_awaited_once()

    async def test_service_that_becomes_unready_is_not_cached(self):
        renderer, service, _ = self.make_renderer()
        await renderer.render("first", "image")
        service.ready = False
        with self.assertRaisesRegex(RuntimeError, "尚未就绪"):
            await renderer.render("second", "image")
        service.render_html.assert_awaited_once()

    async def test_plugin_removed_after_first_render_is_not_cached(self):
        renderer, service, context = self.make_renderer()
        await renderer.render("first", "image")
        context.get_registered_star.return_value = None
        with self.assertRaisesRegex(RuntimeError, "安装并启用"):
            await renderer.render("second", "image")
        service.render_html.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
