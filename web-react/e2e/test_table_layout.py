"""F14 / A3：表格排版 —— 横向滚动与 sticky 表头。

## 为什么这组必须进 E2E（而不是补单测）

判据是「**结构上做不到**，不是只是没做」。这三样量在 jsdom 里全部恒为 0：

    getBoundingClientRect() -> 全 0
    scrollWidth / scrollHeight / clientWidth / clientHeight -> 全 0
    position: sticky -> jsdom 不做布局，"粘住"这个行为根本不存在

于是「**有没有溢出**」「**列头有没有粘住**」在 jsdom 里**没有可读的量**——
不是断言写得不好，是被观测的量不存在。这正是 F14 存在的理由。

## 每条用例都先证明"这个环境真的能测它"

`TestTableLayoutPremise` 三条是**前提断言**，不是凑数：
没有它们的话，「表头没动」与「根本没滚」**同解**，两种情况下断言都会绿。
（同 `test_caret_geometry.py` 里"文档必须高于视口"那条。）

## ⚠️ 这组用例抓出来的东西

写这组时实测发现 **sticky 表头此前完全没生效**：`AppShell` 用的是 `min-h-screen`，
外壳跟着内容长高，于是整条 `flex-1 min-h-0 overflow-auto` 链上
**没有任何一层真的是滚动容器**，在滚的是 `<html>`。
`position: sticky` 只相对自己那个 overflow 祖先粘 —— 祖先不滚，它就不粘。
实测：滚动文档 600px，表头与首行**各移动 -600px**（完全跟着走）。

标记 `thead.sticky top-0` 一直都在，**看代码完全看不出问题**；
789 条单测也全绿，因为 jsdom 里这些量是 0。
修法是 `AppShell` 改 `h-screen`（见那里的文件头注释），本文件就是守它的那道。
"""

from __future__ import annotations

import json
import unittest

from playwright.sync_api import sync_playwright

from stub_server import TABLE_PROJECT_ID, serve

SCROLL_BOX = '[data-testid="table-scroll"]'
ROW = '[data-testid="task-table-row"]'

# 打开**全部 11 列**。
# ⚠️ 横向溢出要由**列数**造成，不要依赖那条 200 字符的长标题：
# 长标题截断/换行目前没实现（见 README 的覆盖缺口登记），将来一旦实现，
# 靠长标题撑宽的用例会跟着变绿变红 —— 那时红的原因与横向滚动毫无关系。
ALL_COLUMNS = [
    "index",
    "title",
    "done",
    "priority",
    "due_date",
    "start_date",
    "end_date",
    "percent_done",
    "labels",
    "assignees",
    "updated",
]


class TableCase(unittest.TestCase):
    """共用浏览器与桩服务器；每条用例开新页面。"""

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
        # 只 shutdown 不 close 会留下未释放的 socket（同 test_caret_geometry.py）
        cls._server.server_close()

    def open_table(self, columns: list[str] | None = None):
        page = self._browser.new_page(viewport={"width": 1280, "height": 720})
        script = "localStorage.setItem('calton-token', JSON.stringify('e2e-token'));"
        if columns is not None:
            # 列显示是个人偏好、存 localStorage（见 columns.ts），直接预置即可
            script += f"localStorage.setItem('calton.table.columns', {json.dumps(json.dumps(columns))});"
        page.add_init_script(script)
        page.goto(f"{self._base}/projects/{TABLE_PROJECT_ID}/table")
        page.wait_for_selector(ROW, timeout=15_000)
        self.addCleanup(page.close)
        return page

    @staticmethod
    def box_metrics(page) -> dict:
        return page.evaluate(
            """(sel) => {
                const box = document.querySelector(sel);
                return {
                    clientW: box.clientWidth,
                    scrollW: box.scrollWidth,
                    clientH: box.clientHeight,
                    scrollH: box.scrollHeight,
                };
            }""",
            SCROLL_BOX,
        )

    @staticmethod
    def tops(page) -> dict:
        """表头与首行**相对视口**的位置。两个一起取，才能分辨"粘住"与"没滚"。"""
        return page.evaluate(
            """(sel) => {
                const box = document.querySelector(sel);
                return {
                    th: box.querySelector('thead th').getBoundingClientRect().top,
                    row: box.querySelector('[data-testid="task-table-row"]').getBoundingClientRect().top,
                    scrollTop: box.scrollTop,
                };
            }""",
            SCROLL_BOX,
        )


class TestTableLayoutPremise(TableCase):
    """★★ 前提：这个环境真的能测排版，而且表格真的溢出了它的盒子。

    三条都不成立的话，下面的断言会在"什么都没发生"的情况下变绿。
    """

    def test_layout_engine_is_present(self) -> None:
        """jsdom 里这个数是 0 —— 那正是这组不能写成单测的原因。"""
        width = self.open_table().evaluate(
            "(sel) => document.querySelector(sel).getBoundingClientRect().width", ROW
        )
        self.assertGreater(width, 0, "行矩形宽度为 0：这个环境没有排版引擎")

    def test_table_overflows_its_box_horizontally(self) -> None:
        """横向滚动的前提：内容真的比盒子宽。不然"滚了"与"没滚"同解。"""
        metrics = self.box_metrics(self.open_table(ALL_COLUMNS))
        self.assertGreater(
            metrics["scrollW"],
            metrics["clientW"],
            f"表格没有横向溢出（scrollW={metrics['scrollW']} clientW={metrics['clientW']}），"
            "这组用例什么也验不出来",
        )

    def test_table_overflows_its_box_vertically(self) -> None:
        """★ sticky 的前提：**盒子自己**必须是滚动容器。

        这一条就是那个 bug 的探针。`AppShell` 用 `min-h-screen` 时，
        这里的 clientH == scrollH（实测 4676 == 4676），盒子根本不滚，
        sticky 无从谈起 —— 而"表头没动"那条断言在那种情况下**照样绿**。
        """
        metrics = self.box_metrics(self.open_table())
        self.assertGreater(
            metrics["scrollH"],
            metrics["clientH"],
            f"表格的滚动容器不滚（scrollH={metrics['scrollH']} clientH={metrics['clientH']}）："
            "外壳没有把高度收住，sticky 表头不可能生效",
        )


class TestHorizontalScroll(TableCase):
    """★★★ 横向滚动发生在表格**自己的盒子**里，不是把整个页面推宽。"""

    def test_columns_scroll_horizontally_inside_the_box(self) -> None:
        page = self.open_table(ALL_COLUMNS)

        first_th_left_before = page.evaluate(
            "(sel) => document.querySelector(sel).querySelector('thead th').getBoundingClientRect().left",
            SCROLL_BOX,
        )
        page.evaluate("(sel) => { document.querySelector(sel).scrollLeft = 400; }", SCROLL_BOX)
        page.wait_for_timeout(200)

        after = page.evaluate(
            """(sel) => {
                const box = document.querySelector(sel);
                return {
                    scrollLeft: box.scrollLeft,
                    thLeft: box.querySelector('thead th').getBoundingClientRect().left,
                };
            }""",
            SCROLL_BOX,
        )

        # 两条都要：属性真的变了，**而且**列头真的跟着移动了。
        # 只断言 scrollLeft 的话，一个 overflow:visible 的普通 div 也能让你把它设成 400
        # （设得进去 ≠ 滚得动），于是这条用例对"横向滚动能不能用"零防护。
        self.assertEqual(after["scrollLeft"], 400, "scrollLeft 没有生效：盒子不是横向滚动容器")
        self.assertAlmostEqual(
            after["thLeft"] - first_th_left_before,
            -400,
            delta=2,
            msg=f"列头没有跟着横向滚动移动（{first_th_left_before} -> {after['thLeft']}）",
        )

    def test_page_itself_does_not_scroll_horizontally(self) -> None:
        """溢出被关起来了，没有漏成整页横向滚动条。

        ⚠️ **这一条不独立承重，是纵深防御 —— 不要因为它总是绿的就删掉，也不要拿它当证据。**

        变异实测（M3：把表格盒子的 `overflow-auto` 摘掉）：
        `test_columns_scroll_horizontally_inside_the_box` 红了，**而本条照样绿**。
        原因是这个性质被**两处**独立保证着：表格盒子的 `overflow-auto`，
        以及外壳 `AppShell` 的 `overflow-hidden`。任一处还在，页面就不会横向滚 ——
        即"终态可由多条路径抵达"（第 20 条），所以单摘一处它不会红。

        留着它的理由：它盯的是**用户可见的最终症状**（整页出现横向滚动条），
        那是两处防线同时失守时唯一会响的地方。**它的价值在将来，不在现在。**
        """
        page = self.open_table(ALL_COLUMNS)
        doc = page.evaluate(
            "() => ({ scrollW: document.documentElement.scrollWidth,"
            " clientW: document.documentElement.clientWidth })"
        )
        self.assertEqual(
            doc["scrollW"],
            doc["clientW"],
            f"整个文档出现了横向滚动（scrollW={doc['scrollW']} clientW={doc['clientW']}）："
            "表格的溢出漏到了盒子外面",
        )


class TestLongTitleMatchesUpstream(TableCase):
    """★★★ 长标题**照抄上游：不截断**，靠横向滚动兜着。

    ## 这不是"还没做"，是查过上游之后的定案

    先前这一项被登记成覆盖缺口（"截断没实现"）。裁决是**去查参照实现，不要我们发明** ——
    因为 truncate+tooltip / 两行省略 / 靠横滚是三个不同的产品选择，
    按"我们觉得哪个更好"来挑，那条"与上游一致"的守卫就失去意义。

    查的结果（`frontend/src/components/project/views/ProjectTable.vue`）：
      - 标题单元格是 `<td>` 里一个 RouterLink，**没有任何截断样式**
        （无 truncate / text-overflow / max-width）；
      - 整张表包在 `.has-horizontal-overflow` 里，而它就是 `overflow-x: auto`；
      - 该组件 scoped 样式里唯一一条 white-space 规则是 `th { white-space: nowrap }`。

    **也就是说上游同样靠横向滚动兜长标题。我们的行为已经与它一致。**

    ## 所以这里写的是**反向断言**（第 17 条）

    钉住"我们**有意**不截断"。将来有人好心加一个 `truncate`，这条会红，
    并在失败信息里告诉他为什么不能加 —— 否则那会是一次**看起来纯属改进**的偏离，
    而且没有任何东西会拦住它。

    ⛔ 这条红了**不要改断言**。要改的话得先走偏离登记，不是实现时顺手改。
    """

    def test_long_title_is_not_truncated_because_upstream_does_not_truncate(self) -> None:
        page = self.open_table()
        style = page.evaluate(
            """(sel) => {
                const td = document.querySelector(sel);
                const cs = getComputedStyle(td);
                return {
                    whiteSpace: cs.whiteSpace,
                    textOverflow: cs.textOverflow,
                    overflow: cs.overflow,
                    maxWidth: cs.maxWidth,
                    width: td.getBoundingClientRect().width,
                    scrollWidth: td.scrollWidth,
                };
            }""",
            '[data-testid="task-table-row"] [data-column="title"]',
        )

        # 判别式前提：这条标题真的长到会触发截断（若有截断的话）。
        # ⚠️ 桩里用的是**不可断行的 ASCII 长串**而不是长中文 —— 中文任意位置可断行，
        # 会自动折行、永远撑不宽单元格，那样"截断了"与"没截断"同解（第 45 条）。
        self.assertGreater(style["width"], 500, f"标题单元格没被撑宽（{style['width']}），验不出截断与否")

        self.assertEqual(style["whiteSpace"], "normal", "标题被改成不换行了 —— 上游没有这样做")
        self.assertNotEqual(style["textOverflow"], "ellipsis", "标题被加了省略号截断 —— 上游没有这样做")
        self.assertEqual(style["maxWidth"], "none", "标题被限了宽 —— 上游没有这样做")

    def test_long_title_widens_the_table_into_horizontal_scroll(self) -> None:
        """长标题的**后果**是横向滚动，这正是上游的兜法。

        上一条盯样式属性，这条盯它造成的实际结果 —— 样式换个写法达到同样效果时，
        上一条可能放过，这条不会。
        """
        page = self.open_table()
        metrics = self.box_metrics(page)
        self.assertGreater(
            metrics["scrollW"],
            metrics["clientW"],
            "长标题没有把表格撑出横向滚动 —— 说明某处把它截断或限宽了",
        )


class TestHeaderDoesNotWrap(TableCase):
    """★★ 列头不折行 —— 同样是照抄上游的 `th { white-space: nowrap }`。

    实测（加这条规则**之前**）：「Due Date」「Start Date」「End Date」三个列头
    各折成两行（列窄、标签是两个词），而上游是一行。
    """

    def test_header_labels_stay_on_one_line(self) -> None:
        page = self.open_table(ALL_COLUMNS)

        rows = page.evaluate(
            """(sel) => [...document.querySelectorAll(sel + ' thead th')].map((th) => {
                const host = th.querySelector('button') || th;
                const node = [...host.childNodes].find(
                    (n) => n.nodeType === 3 && n.textContent.trim(),
                );
                if (!node) return null;
                const range = document.createRange();
                range.selectNodeContents(node);
                return {
                    col: th.dataset.column,
                    text: node.textContent.trim(),
                    lines: range.getClientRects().length,
                };
            }).filter(Boolean)""",
            '[data-testid="task-table"]',
        )

        # 判别式：必须真有列头的文字**长到**在它那一列里放不下 —— 否则"没折行"由
        # "本来就短，怎么都放得下"满足，这条对 nowrap 有没有生效毫无分辨力。
        multiword = [r for r in rows if " " in r["text"]]
        self.assertTrue(multiword, f"没有任何多词列头，这条用例分辨不出 nowrap：{rows}")

        wrapped = [r for r in rows if r["lines"] > 1]
        self.assertEqual(wrapped, [], f"这些列头折行了，上游是 nowrap：{wrapped}")


class TestStickyHeader(TableCase):
    """★★★ 竖向滚动时列头粘在盒子顶部，行滚走。

    ⚠️ 断言必须是**两点**：表头没动 **且** 行确实移动了。
    只断言"表头没动"的话，**根本没滚**的情况完全同解 —— 而那正是修复前的真实状态。
    """

    def test_header_stays_put_while_rows_scroll_away(self) -> None:
        page = self.open_table()
        before = self.tops(page)
        self.assertEqual(before["scrollTop"], 0, "初始就不在顶部，后面的位移量没法解释")

        page.evaluate("(sel) => { document.querySelector(sel).scrollTop = 300; }", SCROLL_BOX)
        page.wait_for_timeout(200)
        after = self.tops(page)

        self.assertEqual(after["scrollTop"], 300, "盒子没有竖向滚动，这条用例的前提不成立")
        # ① 行确实被滚走了 —— 这是判别式，没有它"表头没动"可以由"什么都没发生"满足
        self.assertAlmostEqual(
            after["row"] - before["row"],
            -300,
            delta=2,
            msg=f"首行没有跟着滚动（{before['row']} -> {after['row']}）",
        )
        # ② 表头**原地不动** —— sticky 的定义
        self.assertAlmostEqual(
            after["th"],
            before["th"],
            delta=2,
            msg=f"表头跟着一起滚走了，sticky 没生效（{before['th']} -> {after['th']}）",
        )

    def test_header_is_not_covered_by_the_rows_scrolling_under_it(self) -> None:
        """★ 补一条"粘住之后仍然看得见"：粘住但被行盖住，等于没粘。

        `sticky` 生效但**画的顺序**不对时，列头会被滚上来的行压在下面 ——
        位置断言全绿（表头确实没动），而用户看到的是一堆没有列头的数据。

        ⚠️ 这里**不能**断言"表头在首行之上"。我第一版就是这么写的（`thTop <= rowTop`），
        实测 259 vs 15.5 直接红 —— 因为**首行正是那个已经滚到表头后面去的行**，
        它的 top 当然比表头小。那条断言编码的是一个错的心智模型："首行 = 第一个可见的行"。
        真正要验的性质是**遮挡关系**，那就直接用命中测试量遮挡关系。
        """
        page = self.open_table()
        page.evaluate("(sel) => { document.querySelector(sel).scrollTop = 300; }", SCROLL_BOX)
        page.wait_for_timeout(200)

        result = page.evaluate(
            """(sel) => {
                const box = document.querySelector(sel);
                const th = box.querySelector('thead th');
                const rect = th.getBoundingClientRect();
                // 在表头正中取一点，看命中的是不是表头自己（而不是压在上面的行）
                const hit = document.elementFromPoint(
                    rect.left + rect.width / 2,
                    rect.top + rect.height / 2,
                );
                // 有行**确实**滚到了表头覆盖的那条带子后面 —— 没有这个，遮挡断言无从谈起
                const rowsBehind = [...box.querySelectorAll('[data-testid="task-table-row"]')]
                    .filter((tr) => {
                        const r = tr.getBoundingClientRect();
                        return r.top < rect.bottom && r.bottom > rect.top;
                    }).length;
                // 不透明度要单独量：命中测试对"透明"是瞎的（见下面 M4 那段）
                const bg = getComputedStyle(th).backgroundColor;
                const theadBg = getComputedStyle(th.closest('thead')).backgroundColor;
                return {
                    hitIsHeader: !!(hit && hit.closest('thead')),
                    hitTag: hit ? hit.tagName : null,
                    rowsBehind,
                    bg,
                    theadBg,
                };
            }""",
            SCROLL_BOX,
        )

        # 判别式：必须真有行滚到表头那条带子后面，否则"没被盖住"由"根本没有行在那儿"满足
        self.assertGreater(
            result["rowsBehind"], 0, "没有任何行滚到表头后面，这条遮挡断言什么也验不出来"
        )
        self.assertTrue(
            result["hitIsHeader"],
            f"表头所在位置命中的是 {result['hitTag']}，不是表头本身 —— 它被滚上来的内容盖住了",
        )

        # ★ 不透明背景要**单独**断言。
        #
        # 变异实测（M4：把 thead 的 `bg-card` 摘掉）——上面那条命中断言**照样绿**：
        # `elementFromPoint` 返回的是绘制在最上层的元素，**与它透不透明无关**。
        # 于是"表头透明、底下的行透出来"这种失败，命中测试**结构上看不见**。
        # 而用户看到的是列头与数据糊在一起 —— 一个 sticky 表头最典型的坏法。
        transparent = {"rgba(0, 0, 0, 0)", "transparent"}
        self.assertFalse(
            result["bg"] in transparent and result["theadBg"] in transparent,
            f"表头背景是透明的（th={result['bg']} thead={result['theadBg']}）："
            "粘住了，但底下的行会透上来",
        )


if __name__ == "__main__":
    unittest.main()
