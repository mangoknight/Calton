# F14 — 前端 E2E（生产构建 + 真浏览器）

## 跑法

```bash
cd web-react
npm run build      # F14 打的是 dist/ 生产构建，不是 dev server
npm run test:e2e   # python3 -m unittest discover -s e2e -t e2e -v
```

**依赖**：`playwright`（Python 绑定）与 chromium。本机已装；
没装的话 `pip install playwright && playwright install chromium`。
**没有新增任何 npm 依赖** —— 浏览器与驱动都在 Node 依赖树之外，
主 chunk 预算不受影响。

## 这个套件覆盖什么（以及**不**覆盖什么）

只覆盖一件事：**只有真浏览器才验得了的行为**。

单测跑 jsdom，而 jsdom **没有排版引擎**。`src/test/setup.ts` 补了三个缺失的
几何 API 让 ProseMirror 不再抛错，但**补出来的几何量全是零**，于是：

> 光标落点、点击命中、滚动到选区 —— 在 jsdom 里**测了也是假绿**。

### `test_caret_geometry.py` —— 坐标（5 条）

2 条前提断言（这个环境真的有排版、文档真的高于视口）+ 点击命中 + 滚动到选区 + 一条判别式
（证明"滚动到选区"验的是把光标带进视口，而不只是"有东西滚了"）。

### `test_paste_sanitization.py` —— 从外部粘贴 HTML（5 条）

jsdom 里 `ClipboardEvent` / `DataTransfer` / `navigator.clipboard` **全是 undefined**，
`new DataTransfer()` 直接抛 —— **连构造一次粘贴都做不到**。

断言盯的是**最终会被存下去的那份 HTML**（桩服务器记录写请求体），不只是屏幕上看着对不对：
存进去的才是下次读回来的。实测钉住的行为：`<script>` 不留、`onclick`/`style` 被剥、
`<b>` 规范化成 `<strong>`、**schema 不支持的块（表格）降级但不丢字**。

最后一条是这组的重点：**静默丢内容**才是粘贴清洗最真实的风险。

### ⛔ 不要往这里加的东西

**响应体/请求体的契约用例。** 那些归对拍语料与 **T36**，而 T36 用的是
**真实 MCP 客户端**，不是我们模仿的形状。在这里再造一份"我们以为的形状"，
一旦与实物有偏差，会得到一个绿的 F14 和一个红的 T36，然后浪费时间去调和。

接口在这里是**桩**（`stub_server.py`），只负责让页面渲染出来，不承担契约职责。

### `test_kanban_drag.py` —— 看板拖拽（4 条）

dnd-kit `PointerSensor` + `activationConstraint: { distance: 6 }`，要真实指针事件；
jsdom 里 `PointerEvent` / `setPointerCapture` **全是 undefined**。

**这块此前是零覆盖**：`useMoveTask.test.tsx` 用 `renderHook` 直接调 hook、
`board-move.test.ts` 测纯函数，两者都**不经过手势**。看覆盖率会以为拖拽有覆盖。

一次跨列拖拽同时钉三样：移动请求（task_id/bucket_id 对不对）、位置请求（上游是两次写）、
**界面真的变了**（桩的板面有状态，重拉会反映结果）。

### `test_table_layout.py` —— 表格排版 / A3（10 条）

3 条前提断言（这个环境真的有排版、表格真的横向溢出、**盒子自己真的是滚动容器**），
外加横向滚动 2 条、sticky 表头 2 条、长标题照抄上游 2 条、列头不折行 1 条。

jsdom 里 `getBoundingClientRect()` 与 `scrollWidth/scrollHeight` **全是 0**，
`position: sticky` 更是根本不存在 —— **"有没有溢出""列头有没有粘住"没有可读的量**。

**这组一写就抓到一个真 bug**：`AppShell` 用 `min-h-screen`，外壳跟着内容长高，
于是整条 `flex-1 min-h-0 overflow-auto` 链上**没有任何一层真的是滚动容器**，
在滚的是 `<html>`。而 `sticky` 只相对自己那个 overflow 祖先粘 ——
祖先不滚，它就不粘。实测滚动文档 600px，表头与首行**各移动 -600px**。

标记 `thead.sticky top-0` 一直都在，**看代码完全看不出问题**，789 条单测也全绿。
修法是 `AppShell` 改 `h-screen`（那里有文件头注释钉住理由）。

⚠️ 这个修改把滚动的那一个从 `<html>` 换成了 `<main>`，
所以 `test_caret_geometry.py` 里两条原本读 `document.scrollingElement.scrollTop` 的
用例改读 `[data-testid="app-main"]`。**被测行为一个字没变**，变的是测量点。

## ★ 长标题：**查过上游之后定案为"不截断"**，已从缺口转为反向断言

A3 原本列的第三项是"长标题截断换行"。实测我们这边这个行为不存在，
一度登记为覆盖缺口。**裁决是：去查参照实现，不要我们发明** ——
truncate+tooltip / 两行省略 / 靠横滚是三个不同的**产品选择**，
按"我们觉得哪个更好"来挑，那条"与上游一致"的守卫就失去意义。

查的结果（`frontend/src/components/project/views/ProjectTable.vue`）：

- 标题单元格是 `<td>` 里一个 RouterLink，**没有任何截断样式**（无 truncate / text-overflow / max-width）
- 整张表包在 `.has-horizontal-overflow` 里，该类就是 `overflow-y: hidden; overflow-x: auto`
- 该组件 scoped 样式里唯一一条 white-space 规则是 **`th { white-space: nowrap }`**

**上游同样靠横向滚动兜长标题，我们的行为本来就与它一致。**
所以这里写的是**反向断言**（第 17 条）钉住"我们**有意**不截断"：
将来有人好心加一个 `truncate`，用例会红并在失败信息里说明为什么不能加。
**否则那会是一次看起来纯属改进的偏离，且没有任何东西拦得住。**

⛔ 这几条红了**不要改断言**——要改得先走偏离登记，不是实现时顺手改。

**顺带修了一处真的分歧**：上游 `th` 是 `nowrap`，我们没有，实测
「Due Date」「Start Date」「End Date」三个列头各折成两行。已补 `whitespace-nowrap`。

⚠️ 记一条**取值判别力**（第 45 条）：长标题必须用**不可断行的 ASCII 长串**，
不能用长中文。中文可以在任意字符间换行，一段长中文会自动折行、永远不会把单元格撑宽 ——
于是"做了截断"与"没做截断"**同解**，用什么数据都验不出来。

## ⚠️ 已登记的覆盖缺口：6px 激活阈值没有守着

`activationConstraint.distance = 6` 的作用是"点开任务详情的点击别被当成拖拽吞掉"。
**这个取值目前没有任何用例守着**，实测过两条路都不行：

- 把阈值改成 0 重新构建 → 「点一下不产生写请求」那条**照样绿**
  （Playwright 的 down+up 之间没有移动，dnd-kit 阈值为 0 也不会激活）
- 同样条件下点卡片仍然正常跳 `/tasks/1`，阈值保护的那个行为也看不出差别
- 小距离横向移动同样测不出：**同列内落点解析出来还是同一个桶，本来就不发请求**

要真正守住它，得能观测"拖拽有没有被激活"本身（比如 dnd-kit 的 overlay/aria 状态），
不是观测它的下游后果。**登记在此，不假装它被覆盖了。**

## 已知边界

- 把光标移到文档末尾的快捷键**平台相关**：macOS 实测必须 `Meta+ArrowDown`
  （`Control+End` 什么也不做）。非 darwin 分支取 `Control+End`，**未实测**。
- 只跑 chromium。跨浏览器差异（尤其 Safari 的 `caretRangeFromPoint`）未覆盖。
