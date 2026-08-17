import { ZERO_TIME } from '@/lib/datetime';
import { apiClient, type CaltonClient } from './client';
import type { Paginated } from './pagination';

/**
 * TaskCollection 端点（终稿 §5.1 的"任务 (10)"组）。
 *
 * v1 的任务列表有三个入口，走的是**同一个** `taskCollectionHandler.ReadAllWeb`：
 *   GET /tasks                              全局
 *   GET /projects/{project}/tasks           项目内（不带 view 语义）
 *   GET /projects/{project}/views/{view}/tasks   项目视图内
 *
 * 项目视图页用**第三个**。区别不是"更具体"这种审美问题，而是：
 * `position` 是按 view 存的 —— 走非 view 入口拿到的 position 恒为 0
 * （契约里 models.Task.position 的说明就写着这条）。List 视图当下按 position 排，
 * Kanban（F07b）更是完全依赖它，两边必须走同一个入口才不会打架。
 */

/** 只声明本项目用得到的字段；models.Task 的全集见 generated.ts。 */
export interface Task {
	id: number;
	title: string;
	description?: string;
	done?: boolean;
	/** 形如 "PRJ-3"，没有 identifier 的项目会是空串。 */
	identifier?: string;
	index?: number;
	project_id?: number;
	/** ⚠️ 零值是 "0001-01-01T00:00:00Z" 不是 null，一律过 parseApiTime。 */
	due_date?: string;
	start_date?: string;
	end_date?: string;
	done_at?: string;
	/** 0 = 未设置，1..5 = low..DO NOW。 */
	priority?: number;
	percent_done?: number;
	hex_color?: string;
	is_favorite?: boolean;
	position?: number;
	repeat_after?: number;
	repeat_mode?: number;
	bucket_id?: number;
	cover_image_attachment_id?: number;
	/**
	 * ⚠️ 不带 `expand` 请求时这几个关联字段是 **null 不是 []**
	 * （`labels` / `reminders` / `related_tasks` / `attachments` / `reactions`），
	 * **只有 `assignees` 是 `[]`**。所以读的时候一律 `?? []`，
	 * 直接 `.map()` 会在没有标签的任务上炸。
	 */
	labels?: { id: number; title: string; hex_color?: string }[] | null;
	/** 唯一一个空集为 `[]` 而非 null 的关联字段。 */
	assignees?: { id: number; username?: string; name?: string }[];
	reminders?: unknown[] | null;
	/**
	 * 关联任务，按 relation kind 分组：子任务在 `subtask`、父任务在 `parenttask`
	 *（还有 related/blocking/... 等 kind，这里只用到子/父）。列表接口就会带出来，
	 * 不用逐个任务再拉。不带 `expand` 时同其它关联字段一样是 **null 不是 {}**。
	 */
	related_tasks?: Record<string, Task[]> | null;
	created?: string;
	updated?: string;
}

export interface ListTasksParams {
	page?: number;
	per_page?: number;
	/** 可重复，与 order_by **按下标配对**（F06 会用到）。 */
	sort_by?: string[];
	order_by?: ('asc' | 'desc')[];
	filter?: string;
	filter_timezone?: string;
	s?: string;
}

/**
 * 上游 per_page 的默认值与上限都是 50（`read_all.go:81-90`、`config.go:359`）：
 * 超上限会被静默截断，所以取 50 就是"一页能拿到的最多条数"。
 */
export const TASKS_PER_PAGE = 50;

/**
 * `POST /tasks/{id}` **写入的全部列**（`updateSingleTask` 的 `colsToUpdate`，tasks.go:1283-1298）。
 *
 * ## 为什么必须逐列列全
 *
 * v1 的 POST 是**真·全量替换**：`Task.Update` 调 `updateSingleTask(s, a, nil)`，
 * `fields` 为 nil ⇒ `len(fields) > 0` 不成立 ⇒ `fieldSet` 为空 ⇒
 * **没有任何一列会回落到旧值**，15 列全部按请求体写入。
 *
 * 所以只发 `{done: true}` 不是"只改 done"，而是把标题清空、优先级归零、
 * **`project_id` 写成 0** —— 任务会从它所属的项目里消失。
 * 这就是 AC-6 说的"必须回传完整对象"，不是风格偏好。
 *
 * 这张表由 `tasks.test.ts` 直接对账 Go 源码：少一列 = 那一列被静默清空，
 * 且**不会报错**，是最难回溯的一类 bug。
 */
export const WRITABLE_TASK_COLUMNS = [
	'title',
	'description',
	'done',
	'due_date',
	'repeat_after',
	'priority',
	'start_date',
	'end_date',
	'hex_color',
	'percent_done',
	'project_id',
	'bucket_id',
	'repeat_mode',
	'cover_image_attachment_id',
] as const;

export type WritableTaskColumn = (typeof WRITABLE_TASK_COLUMNS)[number];

/** 可编辑字段的补丁。只列 F08a 负责的那几个，其余由后续任务扩。 */
export type TaskPatch = Partial<Pick<Task, WritableTaskColumn & keyof Task>>;

/**
 * 读-改-写：把补丁盖在**服务端返回的完整任务**上，产出全量替换的请求体。
 *
 * 关键是 `base` 必须是**服务端刚给的那个对象**，不能是页面上拼的局部状态 ——
 * 少一个字段就等于把那个字段清掉。这里逐列取值而不是 `{...base, ...patch}`，
 * 是为了让"漏列"变成可断言的事实：产出的对象键集合恒等于 WRITABLE_TASK_COLUMNS。
 */
export function buildTaskUpdatePayload(base: Task, patch: TaskPatch = {}): Record<string, unknown> {
	const merged = { ...base, ...patch } as Record<string, unknown>;
	const payload: Record<string, unknown> = {};

	for (const column of WRITABLE_TASK_COLUMNS) {
		payload[column] = merged[column] ?? TASK_COLUMN_FALLBACKS[column];
	}

	// id 不在可写列里，但 URL 之外后端也用它定位；带上无害且与上游客户端一致
	payload.id = base.id;
	return payload;
}

/**
 * 服务端没返回某列时的兜底值。
 *
 * ⚠️ 时间列的零值是 `"0001-01-01T00:00:00Z"` 而**不是 null** —— 发 null 会 412。
 * 这些兜底只在响应缺字段时才用得上（正常响应是齐的），但缺了就静默清列，
 * 所以宁可显式写出来。
 */
const TASK_COLUMN_FALLBACKS: Record<WritableTaskColumn, unknown> = {
	title: '',
	description: '',
	done: false,
	due_date: ZERO_TIME,
	repeat_after: 0,
	priority: 0,
	start_date: ZERO_TIME,
	end_date: ZERO_TIME,
	hex_color: '',
	percent_done: 0,
	project_id: 0,
	bucket_id: 0,
	repeat_mode: 0,
	cover_image_attachment_id: 0,
};

export function getTask(taskId: number, client: CaltonClient = apiClient) {
	return client.requestOne<Task>('GET', `/tasks/${taskId}`);
}

/** ⚠️ POST 是全量替换，body 必须由 `buildTaskUpdatePayload` 产出。 */
export function updateTask(
	taskId: number,
	payload: Record<string, unknown>,
	client: CaltonClient = apiClient,
): Promise<Task> {
	return client.post<Task>(`/tasks/${taskId}`, payload);
}

/**
 * 全局任务集合（跨项目）。F12 的今日/本周用它。
 *
 * ⚠️ **路径是 `/tasks` 不是 `/tasks/all`。** `/tasks/all` 是本 fork **没有注册**的
 * Calton-only 别名，请求会被 `GET /tasks/:projecttask` 吞掉（"all" 解析成任务 id 失败），
 * 已认证时返回 **400 / code 2004**（`tasks.yaml` 实测并已登记对拍豁免）。
 * 上游前端历史上用的是 `/tasks/all`，照抄会得到一个看起来毫不相干的 400。
 *
 * ⚠️ 这个入口拿到的 `position` 恒为 0（position 按 view 存），所以不要按 position 排。
 */
export function listTasks(
	params: ListTasksParams = {},
	client: CaltonClient = apiClient,
): Promise<Paginated<Task>> {
	return client.requestList<Task>('GET', '/tasks', {
		query: { per_page: TASKS_PER_PAGE, ...params },
	});
}

/**
 * 项目内任务（**不带 view 语义**）。F12 的收藏用它，因为收藏是**伪项目 -1**。
 *
 * ⚠️ 为什么收藏不能用 `GET /tasks?filter=is_favorite = true`：
 * **`is_favorite` 不在可筛选字段白名单里**（`FILTERABLE_TASK_FIELDS` =
 * 可排序字段 ∪ {assignees, labels, reminders}，两边都没有它），
 * 这么写会得到 **400 / 4016 "The task field 'is_favorite' is invalid."**。
 * 收藏只能走伪项目入口。
 */
export function listProjectTasks(
	projectId: number,
	params: ListTasksParams = {},
	client: CaltonClient = apiClient,
): Promise<Paginated<Task>> {
	return client.requestList<Task>('GET', `/projects/${projectId}/tasks`, {
		query: { per_page: TASKS_PER_PAGE, ...params },
	});
}

export function listViewTasks(
	projectId: number,
	viewId: number,
	params: ListTasksParams = {},
	client: CaltonClient = apiClient,
): Promise<Paginated<Task>> {
	return client.requestList<Task>('GET', `/projects/${projectId}/views/${viewId}/tasks`, {
		query: { per_page: TASKS_PER_PAGE, ...params },
	});
}
