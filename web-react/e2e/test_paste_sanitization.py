"""F14 / A2：**从外部粘贴 HTML** 进富文本编辑器。

## 为什么这必须是 E2E

实测 jsdom：`ClipboardEvent`、`DataTransfer`、`navigator.clipboard` **全是 undefined**，
`new DataTransfer()` 直接抛 —— **连构造一次粘贴都做不到**。
不是"没写用例"，是**结构上写不了**。

## 为什么值得做

粘贴是富文本最常用的入口（从网页、文档、邮件里拷进来），而 ProseMirror 会按
schema 清洗输入。**清洗规则错了不会报错，只会静默地丢内容或留下不该留的东西**——
前者用户过一会儿才发现，后者是安全问题。

所以这里的断言都盯着**最终会被存下去的那份 HTML**（`RECEIVED_WRITES`），
而不只是屏幕上看着对不对：存进去的才是下次读回来的。

## 这些行为的取证方式

全部来自在真实 chromium 里跑一次粘贴后的实测，不是从 StarterKit 文档推断的。
"""

from __future__ import annotations

import unittest

from playwright.sync_api import sync_playwright

import stub_server
from stub_server import serve

EDITOR = '[data-testid="description-editor"]'
SAVE_BUTTON = '[data-testid="save-description"]'

# 一份"从外部拷进来"的脏 HTML：内联样式、事件处理器、脚本、schema 不支持的表格。
DIRTY_HTML = (
    "<h2>粘来的标题</h2>"
    "<p style=\"color:red\" onclick=\"evil()\">正文<b>粗体</b></p>"
    "<script>alert(1)</script>"
    "<table><tr><td>表格里的字</td></tr></table>"
)


class PasteCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._server, port = serve()
        cls._base = f"http://127.0.0.1:{port}"
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._browser.close()
        cls._pw.stop()
        cls._server.shutdown()
        cls._server.server_close()

    def setUp(self) -> None:
        # 每条用例独享一份写请求记录
        stub_server.RECEIVED_WRITES.clear()

    def open_editor(self):
        page = self._browser.new_page(viewport={"width": 1280, "height": 720})
        page.add_init_script("localStorage.setItem('calton-token', JSON.stringify('e2e-token'))")
        page.goto(f"{self._base}/tasks/1")
        page.wait_for_selector(EDITOR, timeout=15_000)
        self.addCleanup(page.close)
        return page

    @staticmethod
    def paste(page, html: str) -> None:
        """派发一次**真实的 `paste` 事件**，带 `text/html` 与 `text/plain` 两种格式。

        真实剪贴板里这两种格式是同时存在的，而编辑器该优先用 HTML 那份。
        只给 text/plain 的话，测的就变成"纯文本粘贴"，验不到清洗规则。
        """
        page.evaluate(
            """({ html, sel }) => {
                const dt = new DataTransfer();
                dt.setData('text/html', html);
                dt.setData('text/plain', '纯文本回退');
                const event = new ClipboardEvent('paste', {
                    clipboardData: dt,
                    bubbles: true,
                    cancelable: true,
                });
                document.querySelector(sel).dispatchEvent(event);
            }""",
            {"html": html, "sel": EDITOR},
        )
        page.wait_for_timeout(300)

    def saved_html(self, page) -> str:
        """点"保存"，返回**发给后端的那份 description**。"""
        page.click(SAVE_BUTTON)
        page.wait_for_timeout(300)
        writes = [w for w in stub_server.RECEIVED_WRITES if w.get("body")]
        self.assertTrue(writes, "没有收到任何写请求，保存这一步没成真")
        return str(writes[-1]["body"].get("description", ""))


class TestPasteSanitization(PasteCase):
    """★★★ 粘贴清洗：留下什么、丢掉什么。"""

    def test_script_tag_never_survives_a_paste(self) -> None:
        """★★★ `<script>` **既不进编辑器、也不进要存的那份**。

        这条是安全断言：描述会被原样存进后端、之后再渲染回来。
        """
        page = self.open_editor()
        self.paste(page, DIRTY_HTML)

        self.assertNotIn("<script", page.inner_html(EDITOR))
        self.assertNotIn("alert(1)", page.inner_html(EDITOR))
        self.assertNotIn("<script", self.saved_html(page))

    def test_event_handlers_and_inline_styles_are_stripped(self) -> None:
        """★★ `onclick` / `style` 不会跟着粘进来。"""
        page = self.open_editor()
        self.paste(page, DIRTY_HTML)

        saved = self.saved_html(page)
        self.assertNotIn("onclick", saved)
        self.assertNotIn("color:red", saved)
        self.assertNotIn('style="', saved)

    def test_bold_is_normalized_to_strong(self) -> None:
        """★★ `<b>` 被规范化成 `<strong>` —— **存进去的是规范化之后那份**。

        判别式：源 HTML 里用的是 `<b>`，与目标 `<strong>` 不同名，
        两者若同名这条就分辨不出"有没有规范化"。
        """
        page = self.open_editor()
        self.paste(page, DIRTY_HTML)

        saved = self.saved_html(page)
        self.assertIn("<strong>粗体</strong>", saved)
        self.assertNotIn("<b>", saved)

    def test_unsupported_block_keeps_its_text(self) -> None:
        """★★★ schema 不支持的块（表格）**降级但不丢字**。

        StarterKit 没有 table 扩展。要紧的不是"表格没了"，
        而是**格子里的文字还在** —— 静默丢内容才是这条用例真正在防的事。
        """
        page = self.open_editor()
        self.paste(page, DIRTY_HTML)

        saved = self.saved_html(page)
        self.assertIn("表格里的字", saved)
        self.assertNotIn("<table", saved)

    def test_paste_reaches_the_backend_at_all(self) -> None:
        """★ 前提：粘贴之后那次保存**真的发出去了**。

        不钉这一条的话，上面几条的 `assertNotIn` 会在"根本没发请求"时
        全部通过 —— 空字符串里当然找不到 `<script>`（第 28 条那类空对空）。
        """
        page = self.open_editor()
        self.paste(page, DIRTY_HTML)

        saved = self.saved_html(page)
        self.assertGreater(len(saved), 0)
        # 粘进来的正文确实到了要存的那份里
        self.assertIn("粘来的标题", saved)


if __name__ == "__main__":
    unittest.main()
