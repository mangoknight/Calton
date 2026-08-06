"""F14：**只有真浏览器才验得了**的那一类行为。

## 这个套件存在的理由（不是"再测一遍已经测过的东西"）

单测跑在 jsdom 上，而 **jsdom 没有排版引擎**。`src/test/setup.ts` 里补了三个
缺失的几何 API，让 ProseMirror 不再抛错——但**补出来的几何量全是零**。

后果写在那段注释里，这里再说一次，因为它决定了本文件的选题：

> 依赖真实坐标的行为（光标落点、点击命中、滚动到选区）在 jsdom 里**测了也是假绿**。

"假绿"比"没测"更危险，因为它让人停止寻找覆盖。**本文件就是那块覆盖。**

⛔ 不要往这里加"响应体字段对不对""请求体形状对不对"之类的用例。
那些归对拍语料与 T36，而 **T36 用的是真实 MCP 客户端，不是我们模仿的形状**——
在这里再造一份我们想象中的形状，只会得到一个绿的 F14 和一个红的 T36。

## 跑法

    cd web-react && npm run build && npm run test:e2e

打的是 **`dist/` 生产构建**，不是 dev server：F14 要覆盖的东西里
包含"构建产物本身有没有问题"（比如 TipTap 被拆成懒加载 chunk 之后还能不能正常加载）。
"""

from __future__ import annotations

import sys
import unittest

from playwright.sync_api import sync_playwright

from stub_server import serve

EDITOR = '[data-testid="description-editor"]'

# ⚠️ 滚动的**不是** `document.scrollingElement`，是外壳里那个 `<main>`。
#
# 本文件最初写作 `document.scrollingElement.scrollTop`，当时是对的：外壳用 `min-h-screen`，
# 整页跟着内容长高，真正在滚的就是 `<html>`。
# 后来 A3 发现那样会让**全站 sticky 失效**（外壳不收高度，内层 overflow 容器一个都不成立），
# 外壳改成 `h-screen` + 内层 `<main>` 自己滚。
#
# **被测行为一个字没变**（实测：Meta+ArrowDown 后滚动量 1508、光标落在 379/396，视口 720 内；
# PageDown 后滚动量 624、光标 -336 在视口外）—— 变的只是"谁在滚"。
# 所以这里改的是**测量对象**，不是期望值（第 38 条：改期望前先解释差异，本例差异出在测量点）。
SCROLLER = '[data-testid="app-main"]'

# 把光标移到文档末尾的快捷键是**平台相关**的。
# ⚠️ 实测：macOS 上 `Control+End` **什么也不做**（scrollTop 0→0，光标不动），
# 必须用 `Meta+ArrowDown`。只有 darwin 这条路径是实测过的，
# 其余平台按常规取 Control+End，**标注为未实测**。
END_OF_DOC_KEY = "Meta+ArrowDown" if sys.platform == "darwin" else "Control+End"


class BrowserCase(unittest.TestCase):
    """共用一个浏览器与一台桩服务器；每条用例开新页面，互不串状态。"""

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
        # 只 shutdown 不 close 会留下未释放的 socket（ResourceWarning）。
        # 单跑无害，但 E2E 未来要在 CI 里反复起停，攒着就是端口泄漏 ——
        # 对拍台刚因为端口耗尽查过一轮，不重复同一个坑。
        cls._server.server_close()

    def open_task(self):
        page = self._browser.new_page(viewport={"width": 1280, "height": 720})
        # 业务页在登录闸门后面；直接把 token 塞进 localStorage，登录流程不是这里的被测对象
        page.add_init_script("localStorage.setItem('calton-token', JSON.stringify('e2e-token'))")
        page.goto(f"{self._base}/tasks/1")
        # 编辑器是懒加载 chunk（TipTap 不进主包），要等它真的到位
        page.wait_for_selector(EDITOR, timeout=15_000)
        self.addCleanup(page.close)
        return page

    @staticmethod
    def text_rect(page) -> dict:
        """首段**文字本身**的矩形。

        ⚠️ 不能用段落元素的 box：段落宽 982px 而文字只有 393px，
        按元素宽度取比例会点到右边的空白处，三个点全落在行尾同一个偏移量上——
        那样"点击命中"这条用例就变成了不动点，什么也验不出来（第 45 条）。
        """
        return page.evaluate(
            """(sel) => {
                const p0 = document.querySelector(sel + ' p');
                const range = document.createRange();
                range.selectNodeContents(p0);
                return range.getBoundingClientRect().toJSON();
            }""",
            EDITOR,
        )

    @staticmethod
    def scroll_top(page) -> float:
        return page.evaluate("(sel) => document.querySelector(sel).scrollTop", SCROLLER)

    @staticmethod
    def caret(page) -> dict:
        return page.evaluate(
            """() => {
                const sel = getSelection();
                if (!sel.rangeCount) return { offset: null, top: null };
                const rect = sel.getRangeAt(0).getBoundingClientRect();
                return { offset: sel.anchorOffset, top: rect.top, bottom: rect.bottom };
            }"""
        )


class TestGeometryPremise(BrowserCase):
    """★★ 前提：这个环境**真的有排版**。

    不先钉住这一条的话，下面所有用例都可能在"几何量全是零"的环境里
    以某种看起来合理的方式通过，而我们会以为坐标行为被覆盖了——
    那正好是本套件要消灭的那种假绿（第 28 条：先证明验证装置自己是好的）。
    """

    def test_range_geometry_is_non_zero(self) -> None:
        page = self.open_task()
        geom = page.evaluate(
            """(sel) => {
                const p0 = document.querySelector(sel + ' p');
                const range = document.createRange();
                range.selectNodeContents(p0);
                const rects = range.getClientRects();
                return { count: rects.length, width: rects.length ? rects[0].width : 0 };
            }""",
            EDITOR,
        )
        # jsdom 里这两个数分别是 0 和 0 —— 那正是单测测不了坐标的原因
        self.assertGreater(geom["count"], 0, "Range.getClientRects() 为空：这个环境没有排版")
        self.assertGreater(geom["width"], 0, "文字矩形宽度为 0：这个环境没有排版")

    def test_scroll_container_is_taller_than_its_viewport(self) -> None:
        """滚动类用例的前提：内容必须高于**那个真正在滚的盒子**，否则"滚了"与"没滚"同解。

        ⚠️ 量的是 `<main>` 而不是 `document.scrollingElement`（见文件头 SCROLLER 那段）。
        这一条同时也钉住了"外壳真的把高度收住了"——它一旦回到 `min-h-screen`，
        `<main>` 的 scrollHeight 会等于 clientHeight，这里立刻红。
        """
        page = self.open_task()
        sizes = page.evaluate(
            "(sel) => ({ scroll: document.querySelector(sel).scrollHeight,"
            " client: document.querySelector(sel).clientHeight })",
            SCROLLER,
        )
        self.assertGreater(sizes["scroll"], sizes["client"])


class TestClickHitTesting(BrowserCase):
    """★★★ 点击命中：屏幕坐标 → 文档位置。

    这是 jsdom **结构上**做不到的事情：它没有 `elementFromPoint`、
    也没有文字矩形，`posAtCoords` 拿不到任何可用信息。
    """

    def test_click_x_position_maps_to_increasing_caret_offset(self) -> None:
        page = self.open_task()
        rect = self.text_rect(page)
        y = rect["top"] + rect["height"] / 2

        offsets = []
        for fraction in (0.05, 0.5, 0.95):
            page.mouse.click(rect["left"] + rect["width"] * fraction, y)
            offsets.append(self.caret(page)["offset"])

        # 严格递增：点得越靠右，光标落在越后面的字符上。
        #
        # ⚠️ **一条被实测推翻的判断，留在这里当反例（第 44 条）。**
        # 这段注释原来写的是"四组字符必须互不相同，全是同一个字的话点哪儿都一样"。
        # 实测：把首段换成 43 个连续的 `A`，三次点击给出的偏移量是 **2 / 22 / 41**——
        # 照样严格递增。**光标偏移量是按位置算的，与字符长什么样无关**，
        # 所以那个理由是错的（结论没错：这条用例确实在验点击命中）。
        #
        # 这组数据真正的判别式条件是**文字要够长**：
        # 短到只有几个字符时，三个 x 会落进同一个偏移量，用例就成了不动点。
        # 当前首段 43 字符 / 393px，足够把三次点击分开。
        self.assertEqual(offsets, sorted(offsets), f"光标偏移量没有随 x 递增: {offsets}")
        self.assertLess(offsets[0], offsets[1])
        self.assertLess(offsets[1], offsets[2])
        # 最左边应当落在开头附近、最右边落在结尾附近（实测 2 / 23 / 41，全长 43）
        self.assertLess(offsets[0], 5)
        self.assertGreater(offsets[2], 35)


class TestScrollToSelection(BrowserCase):
    """★★★ 滚动到选区：光标移出视口时，视图要把它带回来。

    这条路径就是单测里那 33 条 `Range.getClientRects` 异常的来源
    （`scrollToSelection` → `coordsAtPos` → `singleRect`）。
    在 jsdom 里它抛错；补了补丁之后它**静默什么也不做**（矩形全是零）。
    两种情况下"滚动到选区对不对"都测不了。
    """

    def test_moving_caret_to_document_end_brings_it_into_view(self) -> None:
        page = self.open_task()
        page.click(f"{EDITOR} p")
        self.assertEqual(self.scroll_top(page), 0)

        page.keyboard.press(END_OF_DOC_KEY)
        page.wait_for_timeout(400)

        scroll_top = self.scroll_top(page)
        caret = self.caret(page)
        viewport_height = page.evaluate("() => innerHeight")

        # 两条都要：视图确实滚了，**而且**光标落在视口里
        self.assertGreater(scroll_top, 0, "移到文档末尾后视图没有滚动")
        self.assertGreaterEqual(caret["top"], 0)
        self.assertLessEqual(caret["bottom"], viewport_height)

    def test_plain_scrolling_does_not_count_as_scroll_to_selection(self) -> None:
        """★ 判别式：证明上一条验的是"把光标带进视口"，而不只是"有东西滚了"。

        `PageDown` 同样会让 scrollTop 变大，但**光标不动**，于是光标被滚出视口。
        如果上一条只断言 `scrollTop > 0`，这种情况也会让它变绿 —— 那它就没在验
        scroll-to-selection（第 20 条：断言通过 ≠ 断言在验它声称要验的东西）。
        """
        page = self.open_task()
        page.click(f"{EDITOR} p")

        page.keyboard.press("PageDown")
        page.wait_for_timeout(400)

        scroll_top = self.scroll_top(page)
        caret = self.caret(page)

        self.assertGreater(scroll_top, 0, "PageDown 没有滚动，这条判别式不成立")
        # 实测 caret.top = -392：光标被留在了视口上方
        self.assertLess(caret["top"], 0, "PageDown 之后光标仍在视口内，判别不出两者差别")


if __name__ == "__main__":
    unittest.main()
