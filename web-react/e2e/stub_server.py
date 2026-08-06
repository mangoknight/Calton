"""F14 E2E 的静态站点 + 桩接口服务器。

## 为什么接口是**桩**，而不是打真实后端

F14 要覆盖的是**只有真浏览器才能验的东西**（光标落点、点击命中、滚动到选区）——
这些行为与后端返回什么字段毫无关系。

契约保真度归对拍语料与 T36 管，**T36 用的是真实 MCP 客户端而不是我们模仿的形状**。
在这里再造一份"我们以为的响应体"去打真后端，等于用我们的想象复制一个已经有实物的东西：
一旦想象与实物有偏差，会得到一个绿的 F14 和一个红的 T36，然后浪费时间去调和。

所以这里的桩**只需要让页面渲染出来**，不承担任何契约职责。
⛔ 不要往这个文件里加"验证请求体形状"之类的断言 —— 那是对拍台的活。
"""

from __future__ import annotations

import json
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "dist"

# 描述里放**很长的内容**：滚动到选区那条用例需要文档高于视口，
# 否则"滚了"和"没滚"同解（scrollTop 恒为 0），那条断言什么也验不了。
_PARAGRAPHS = "".join(f"<p>第 {i} 段：这是一段用来把文档撑高的正文内容。</p>" for i in range(1, 41))

TASK = {
    "id": 1,
    "title": "F14 光标定位用任务",
    # 首段用于"点击命中"：三个 x 位置必须落到不同的光标偏移量上。
    # ⚠️ 判别条件是**文字够长**（当前 43 字符 / 约 393px），**不是**字符互不相同 ——
    # 后者是我最初写下的理由，实测把首段换成 43 个连续 `A` 后偏移量仍是 2/22/41，
    # 该理由已被推翻（详见 test_caret_geometry.py 里那段反例注释，第 44 条）。
    "description": f"<p id='probe'>AAAAAAAAAA BBBBBBBBBB CCCCCCCCCC DDDDDDDDDD</p>{_PARAGRAPHS}",
    "done": False,
    "priority": 0,
    "project_id": 1,
    "identifier": "T-1",
    "due_date": "0001-01-01T00:00:00Z",
}

USER = {"id": 1, "username": "tester", "name": "Tester"}

# ---- 看板（A1 拖拽用）----
PROJECT_ID = 12
KANBAN_VIEW_ID = 4


def _board_task(task_id: int, position: float) -> dict:
    return {"id": task_id, "title": f"任务 {task_id}", "position": position, "project_id": PROJECT_ID}


# 板面的**可变**状态。移动任务的写请求会真的改它，于是重新拉板面时能看到结果 ——
# 否则重新拉到的还是原样，"拖过去了"在界面上看不出来，用例只能断言请求、断言不了结果。
# ⚠️ 每条用例前调 `reset_board()`。
BOARD: list[dict] = []


def reset_board() -> None:
    BOARD[:] = make_buckets()


def move_task_in_board(task_id: int, bucket_id: int) -> None:
    """把任务挪到目标桶（桩的最小实现：摘掉再追加，并同步 count）。"""
    moved = None
    for bucket in BOARD:
        for task in list(bucket["tasks"]):
            if task["id"] == task_id:
                moved = task
                bucket["tasks"].remove(task)
                bucket["count"] = len(bucket["tasks"])
    if moved is None:
        return
    for bucket in BOARD:
        if bucket["id"] == bucket_id:
            bucket["tasks"].append(moved)
            bucket["count"] = len(bucket["tasks"])


def make_buckets() -> list[dict]:
    """两列：左列两个任务、右列一个。

    ⚠️ 判别式布局：**两列都必须非空**。右列为空的话，"拖到右列"与"拖到任何空白处"
    在结果上难以区分；而左列只有一个任务时，也验不出"从多任务列里挑对了那一个"。
    """
    return [
        {
            "id": 101,
            "title": "待办",
            "project_view_id": KANBAN_VIEW_ID,
            "count": 2,
            "limit": 0,
            "tasks": [_board_task(1, 1.0), _board_task(2, 2.0)],
        },
        {
            "id": 102,
            "title": "进行中",
            "project_view_id": KANBAN_VIEW_ID,
            "count": 1,
            "limit": 0,
            "tasks": [_board_task(3, 1.0)],
        },
    ]

# ---- 表格（A3 排版用）----
#
# 单独一个 project，不与看板共用：A3 要把**所有列**打开来撑宽表格，
# 而看板那组依赖的是它自己那份桶数据，两边共用一个 id 会互相影响。
TABLE_PROJECT_ID = 13
TABLE_VIEW_ID = 5

# 长标题用**不可断行的 ASCII 长串**，不是长中文。
# ⚠️ 这是判别式取值（第 45 条）：中文可以在任意字符间换行，一段长中文会自动折行、
# 永远不会把单元格撑宽 —— 于是"有没有做截断/换行处理"两种实现**同解**，什么也验不出来。
# 只有不可断行的长串才能把"放任它撑宽"与"截断或强制换行"区分开。
LONG_TITLE = "A" * 200

# 行数要够多，让表格高于滚动容器 —— 否则 sticky 表头"粘住"与"根本没滚"同解。
TABLE_TASK_COUNT = 60


def _table_task(task_id: int) -> dict:
    return {
        "id": task_id,
        # 第 1 行是那条长标题，其余是普通标题
        "title": LONG_TITLE if task_id == 1 else f"表格任务 {task_id}",
        "done": task_id % 2 == 0,
        "priority": task_id % 6,
        "project_id": TABLE_PROJECT_ID,
        "identifier": f"TBL-{task_id}",
        "index": task_id,
        "percent_done": 0.25,
        "due_date": "2026-08-20T00:00:00Z",
        "start_date": "2026-08-01T00:00:00Z",
        "end_date": "2026-08-30T00:00:00Z",
        "updated": "2026-08-01T00:00:00Z",
        "labels": [],
        "assignees": [],
    }


TABLE_TASKS = [_table_task(i) for i in range(1, TABLE_TASK_COUNT + 1)]

# 收到的写请求体，按发生顺序。用例读它来断言"**真的会被存下去的是什么**"——
# 粘贴清洗错了会静默丢内容，而丢没丢只有看落库的那份才知道。
# ⚠️ 服务器跑在同进程的线程里，所以用例直接读这个列表即可；每条用例前自行清空。
RECEIVED_WRITES: list[dict] = []

# 端点 -> (响应体, 额外响应头)
ROUTES: dict[str, tuple[object, dict[str, str]]] = {
    "/api/v1/user": (USER, {}),
    "/api/v1/tasks/1": (TASK, {}),
    "/api/v1/tasks/1/comments": ([], {"x-pagination-result-count": "0", "x-pagination-total-pages": "0"}),
    "/api/v1/labels": ([], {"x-pagination-result-count": "0", "x-pagination-total-pages": "0"}),
    "/api/v1/projects": ([], {"x-pagination-result-count": "0", "x-pagination-total-pages": "0"}),
    "/api/v1/projects/1/projectusers": ([], {}),
    f"/api/v1/projects/{PROJECT_ID}/views": (
        [
            {"id": KANBAN_VIEW_ID, "project_id": PROJECT_ID, "title": "Kanban", "view_kind": "kanban"},
        ],
        {"x-pagination-result-count": "1", "x-pagination-total-pages": "1"},
    ),
    # ⚠️ 板面数据走的是 **tasks** 端点，不是 buckets 端点：view 是 kanban 时它多态返回
    # `Bucket[]`（每个桶带着自己的 tasks），而 `/buckets` 只给空桶且 count 恒 0。
    # 这处是照抄 `api/buckets.ts` 文件头记录的实测结论 —— 第一版我按名字猜成 /buckets，
    # 页面直接 404。
    f"/api/v1/projects/{PROJECT_ID}/views/{KANBAN_VIEW_ID}/tasks": (
        None,  # 动态：每次请求现算，见 do_GET
        {"x-pagination-result-count": "2", "x-pagination-total-pages": "1"},
    ),
    # ---- 表格（A3）----
    f"/api/v1/projects/{TABLE_PROJECT_ID}/views": (
        [
            {
                "id": TABLE_VIEW_ID,
                "project_id": TABLE_PROJECT_ID,
                "title": "Table",
                "view_kind": "table",
            },
        ],
        {"x-pagination-result-count": "1", "x-pagination-total-pages": "1"},
    ),
    f"/api/v1/projects/{TABLE_PROJECT_ID}/views/{TABLE_VIEW_ID}/tasks": (
        TABLE_TASKS,
        {
            "x-pagination-result-count": str(TABLE_TASK_COUNT),
            "x-pagination-total-pages": "1",
        },
    ),
}


class Handler(SimpleHTTPRequestHandler):
    """静态资源走 dist/，`/api/v1/*` 走桩，其余路径回 index.html（SPA 路由）。"""

    def _send_json(self, payload: object, headers: dict[str, str]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 的命名)
        path = self.path.split("?", 1)[0]

        if path in ROUTES:
            payload, headers = ROUTES[path]
            # None 表示这条要现算（看板每次都给一份新的桶，避免用例之间共享可变对象）
            if payload is None:
                if not BOARD:
                    reset_board()
                self._send_json(BOARD, headers)
                return
            self._send_json(payload, headers)
            return

        if path.startswith("/api/"):
            # 桩没覆盖到的接口一律 404 —— 静默返回空对象会让"页面少发了一个请求"
            # 和"接口没实现"看起来一样
            # ⚠️ 状态行只能是 latin-1，这里不能写中文（第一版写了，服务器线程直接抛）
            self.send_error(404, f"not stubbed: {path}")
            return

        # SPA：非静态资源的路径交给前端路由
        if "." not in Path(path).name:
            self.path = "/index.html"

        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        """写操作一律回显任务对象 —— 编辑器失焦保存会打这里，不回的话页面会报错。"""
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b"{}")
            RECEIVED_WRITES.append({"path": self.path, "body": body})
            # 看板移动：让桩的板面真的变，这样重新拉取时界面能反映出来
            if self.path.endswith("/tasks") and "buckets" in self.path and isinstance(body, dict):
                if "task_id" in body and "bucket_id" in body:
                    move_task_in_board(int(body["task_id"]), int(body["bucket_id"]))
        except json.JSONDecodeError:
            RECEIVED_WRITES.append({"path": self.path, "body": None, "raw": raw.decode(errors="replace")})
        self._send_json(TASK, {})

    def log_message(self, *args: object) -> None:
        """默认会往 stderr 打每条请求，E2E 输出里全是噪声。"""


def serve(port: int = 0) -> tuple[HTTPServer, int]:
    """起服务器，返回 (server, 实际端口)。

    port=0 让内核分配空闲端口 —— 写死端口会在并发跑时撞车，
    而那种失败长得像"页面加载失败"，排查很贵（对拍台刚踩过端口耗尽）。
    """
    if not DIST.exists():
        raise SystemExit(f"没有 {DIST}，先跑 `npm run build`（F14 打的是**生产构建**）")

    server = HTTPServer(("127.0.0.1", port), partial(Handler, directory=str(DIST)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_port
