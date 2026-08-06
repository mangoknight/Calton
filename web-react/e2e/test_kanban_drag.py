"""F14 / A1：**看板拖拽**。

## 为什么这必须是 E2E

`KanbanView.tsx` 用的是 dnd-kit 的 `PointerSensor`，
`activationConstraint: { distance: 6 }` —— **要有真实指针事件、并且移动超过 6px 才激活**。

实测 jsdom：`PointerEvent` / `DragEvent` / `setPointerCapture` **全是 undefined**。
不是"没写用例"，是**结构上写不了**。

## 这块此前是**零覆盖**，不是"覆盖得少"

`useMoveTask.test.tsx` 用 `renderHook` 直接调 hook，测的是
**"给定一次移动，请求怎么发"**；`board-move.test.ts` 测的是纯函数的落点计算。
两者都是真测试，但**都不经过手势**：传感器有没有接上、6px 阈值对不对、
拖到某一列时落点算的是不是那一列 —— 这一整段没有任何东西守着。

**"有测试"与"被覆盖"的差别就在这里**：看覆盖率会以为拖拽有覆盖。

## 断言什么

拖一次跨列，同时钉三样：

1. **移动请求**打到目标桶，请求体带对 `task_id` / `bucket_id`
2. **位置请求**跟着发（上游是两次写：先进桶，再定位）
3. **界面真的变了** —— 桩服务器的板面是有状态的，重新拉取会反映移动结果

只断言 1、2 的话，"请求发对了但界面没更新"会漏过去；
只断言 3 的话，乐观更新也能让它变绿。
"""

from __future__ import annotations

import unittest

from playwright.sync_api import sync_playwright

import stub_server
from stub_server import KANBAN_VIEW_ID, PROJECT_ID, serve

COLUMN = '[data-testid="bucket-column"]'
CARD = '[data-testid="task-card"]'

# dnd-kit 的激活阈值（`KanbanView.tsx` 里写死 6px）。
# 用例里要跨过它，所以这个数字是**被测配置的一部分**，不是随手取的。
ACTIVATION_DISTANCE = 6


class KanbanDragCase(unittest.TestCase):
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
        stub_server.reset_board()
        stub_server.RECEIVED_WRITES.clear()

    def open_board(self):
        page = self._browser.new_page(viewport={"width": 1280, "height": 900})
        page.add_init_script("localStorage.setItem('calton-token', JSON.stringify('e2e-token'))")
        page.goto(f"{self._base}/projects/{PROJECT_ID}/kanban")
        page.wait_for_selector(CARD, timeout=15_000)
        self.addCleanup(page.close)
        return page

    @staticmethod
    def columns(page) -> list[list[str]]:
        """每一列当前的任务标题。"""
        return [
            [card.inner_text().strip() for card in column.query_selector_all(CARD)]
            for column in page.query_selector_all(COLUMN)
        ]

    def drag_card_to_column(self, page, card_index: int, column_index: int) -> None:
        """按住卡片，越过激活阈值，拖到目标列底部再松手。

        ⚠️ 中间那几步 `mouse.move` 不能省：dnd-kit 要**在 pointerdown 之后
        真的移动超过 6px** 才激活。一步直接移到终点时浏览器只产生一次移动事件，
        传感器有可能拿不到中间过程 —— 第一版就是分几步走才稳定复现的。
        """
        card = page.query_selector_all(CARD)[card_index].bounding_box()
        target = page.query_selector_all(COLUMN)[column_index].bounding_box()

        start_x = card["x"] + card["width"] / 2
        start_y = card["y"] + card["height"] / 2

        page.mouse.move(start_x, start_y)
        page.mouse.down()
        for offset in (ACTIVATION_DISTANCE // 2, ACTIVATION_DISTANCE + 4, 40):
            page.mouse.move(start_x + offset, start_y + offset)
            page.wait_for_timeout(60)

        page.mouse.move(
            target["x"] + target["width"] / 2,
            target["y"] + target["height"] - 40,
            steps=10,
        )
        page.wait_for_timeout(150)
        page.mouse.up()
        page.wait_for_timeout(700)

    @staticmethod
    def writes_to(path_fragment: str) -> list[dict]:
        return [w for w in stub_server.RECEIVED_WRITES if path_fragment in w["path"]]


class TestKanbanDrag(KanbanDragCase):
    def test_board_starts_with_two_non_empty_columns(self) -> None:
        """★ 前提：起始布局是判别式的。

        两列都非空才分得出"拖到右列"与"拖到任何空白处"；
        左列有两张才验得出"挑对了那一张"。这条钉住前提，
        免得将来有人改了桩数据、让下面的用例悄悄失去分辨力。
        """
        page = self.open_board()
        columns = self.columns(page)

        self.assertEqual(len(columns), 2)
        self.assertEqual(columns[0], ["任务 1", "任务 2"])
        self.assertEqual(columns[1], ["任务 3"])

    def test_drag_across_columns_sends_move_and_position(self) -> None:
        """★★★ 跨列拖拽 → 两次写请求，且请求体指向被拖的那张卡与目标桶。"""
        page = self.open_board()
        self.drag_card_to_column(page, card_index=0, column_index=1)

        moves = self.writes_to(f"/views/{KANBAN_VIEW_ID}/buckets/")
        self.assertTrue(moves, "拖拽之后没有发出移动请求（传感器可能根本没激活）")

        move_body = moves[-1]["body"]
        self.assertEqual(move_body["task_id"], 1, "移动的不是被拖的那张卡")
        self.assertEqual(move_body["bucket_id"], 102, "没有落到目标列")
        self.assertEqual(move_body["project_view_id"], KANBAN_VIEW_ID)

        # 上游是两次写：先进桶，再定位。少了第二次，跨列后的排序会是错的。
        positions = self.writes_to("/position")
        self.assertTrue(positions, "缺少定位请求")
        self.assertEqual(positions[-1]["path"], "/api/v1/tasks/1/position")

    def test_drag_across_columns_updates_the_board(self) -> None:
        """★★★ 界面**真的变了** —— 桩的板面有状态，重拉后反映移动结果。

        与上一条分开：只断言请求的话，"请求对了但界面没更新"会漏过去。
        """
        page = self.open_board()
        self.drag_card_to_column(page, card_index=0, column_index=1)

        columns = self.columns(page)
        self.assertEqual(columns[0], ["任务 2"], "源列没有把这张卡摘掉")
        self.assertIn("任务 1", columns[1], "目标列没有收到这张卡")

    def test_click_without_moving_does_not_trigger_a_move(self) -> None:
        """★ 按下并松开、但不移动，不产生任何写请求。

        ## ⚠️ 这条**不能**用来担保 6px 阈值，我试过了

        写这条时我在注释里声称它守着 `activationConstraint.distance = 6`
        （"把阈值改成 0 就会有用例红"）。**实测推翻了这个说法**：
        把 6 改成 0 之后重新构建，

        - 这条用例照样绿（点击不移动 ⇒ 两种配置下都不触发拖拽）；
        - 点卡片仍然正常跳到 `/tasks/1`（阈值保护的"点击别被吞掉"也看不出差别）。

        原因是 Playwright 的 `click`/`mouse.down+up` 之间**没有任何移动**，
        而 dnd-kit 即使阈值为 0 也要有一次移动事件才会激活。
        纯横向小距离移动同样测不出来：**同列内的落点解析出来还是同一个桶，本来就不发请求**。

        所以：**6px 这个取值目前没有任何用例守着**，已登记为已知缺口（见 README）。
        这条留着的价值是它自己那句话——**点一下不该产生写请求**，
        那是真实的回归风险（第 3 条：守不住的东西要如实标注，不要含糊过去）。
        """
        page = self.open_board()
        card = page.query_selector_all(CARD)[0].bounding_box()

        page.mouse.move(card["x"] + card["width"] / 2, card["y"] + card["height"] / 2)
        page.mouse.down()
        page.mouse.up()
        page.wait_for_timeout(400)

        self.assertEqual(
            stub_server.RECEIVED_WRITES, [], "只是点了一下，却发出了写请求"
        )


if __name__ == "__main__":
    unittest.main()
