import { apiClient, type CaltonClient } from './client';
import { ContractViolationError } from './errors';
import type { Paginated } from './pagination';
import type { Task } from './tasks';

/**
 * 看板桶（终稿 §5.1 的"看板 (5)"端点组）。
 *
 * ## ⚠️ 取板面数据要用 tasks 端点，不是 buckets 端点
 *
 * 有两个地方能拿到桶，返回的东西**不一样**：
 *
 * 1. `GET /projects/{p}/views/{v}/buckets`（`Bucket.ReadAll`，kanban.go:114-155）
 *    只查 buckets 表，**不带 tasks，且 `count` 恒为 0** —— `Count` 是 `xorm:"-"` 字段，
 *    这个方法从头到尾没给它赋过值。拿它渲染板面会得到一排空桶，
 *    而且"limit 已满"永远判不出来。
 *
 * 2. `GET /projects/{p}/views/{v}/tasks`（**多态**，task_collection.go:176-182）
 *    view 是 kanban 且 `bucket_configuration_mode != none` 时，返回的是
 *    **`Bucket[]`（每个桶带着自己的 tasks）**，不是 `Task[]`。
 *    `count` 在这条路径上才被赋值（kanban.go:269）。
 *
 * 所以板面走 ②，桶的增删改走 `.../buckets` 那组写端点。
 *
 * ## ⚠️ count 是总数，tasks 是当前页
 *
 * `bucket.Count = total`（kanban.go:269）取的是**匹配的总数**，
 * 而 `tasks` 只是 per_page 截出来的一页。所以判断"是否已满"只能用 `count`，
 * 用 `tasks.length` 会在任务数超过一页时判错。
 */

export interface Bucket {
	id: number;
	title: string;
	project_view_id: number;
	/** 桶内任务总数（不是 `tasks.length`，见文件头）。 */
	count: number;
	/** 容量上限；**0 表示不限**，不是"容量为 0"。 */
	limit: number;
	position?: number;
	/** 当前页的任务；桶为空时后端省略该字段（`json:"tasks,omitempty"`）。 */
	tasks?: Task[] | null;
	created?: string;
	updated?: string;
}

/** limit 为 0 表示不限容量 —— 判满之前必须先过这一关。 */
export function isBucketFull(bucket: Pick<Bucket, 'count' | 'limit'>): boolean {
	if (!bucket.limit || bucket.limit <= 0) return false;
	return bucket.count >= bucket.limit;
}

/**
 * 多态端点的形状判别：Bucket 有 `project_view_id`，Task 没有。
 *
 * 判错的后果不是报错而是空板面 —— view 的 `bucket_configuration_mode` 是 `none` 时，
 * 后端返回的是扁平的 `Task[]`，当成 Bucket[] 渲染会得到一排没有标题的空列。
 * 与其静默出错，不如在这里如实说清楚是配置问题。
 */
function assertBucketShape(items: unknown[], viewId: number): asserts items is Bucket[] {
	for (const item of items) {
		if (typeof item !== 'object' || item === null || !('project_view_id' in item)) {
			throw new ContractViolationError(
				`视图 ${viewId} 的任务端点返回的是扁平任务列表，不是看板桶。` +
					`看板视图只有在 bucket_configuration_mode 不是 "none" 时才返回桶结构 —— ` +
					`请检查该视图的配置。`,
			);
		}
	}
}

/**
 * 板面数据：桶 + 每桶的任务。
 *
 * 注意分页头此时描述的是**桶的条数**（`resultCount = len(buckets)`），
 * 不是任务条数；而 `per_page` 限制的是**每个桶内返回多少任务**。
 * 板面不做翻页，取一页足够大的即可。
 */
export async function listBucketsWithTasks(
	projectId: number,
	viewId: number,
	/** F11a：筛选条件原样透传（空串不发这个键）。参数对象在前、client 在后，与 `listViewTasks` 一致。 */
	params: { filter?: string } = {},
	client: CaltonClient = apiClient,
): Promise<Paginated<Bucket>> {
	const result = await client.requestList<unknown>(
		'GET',
		`/projects/${projectId}/views/${viewId}/tasks`,
		// 不发 sort_by：kanban 分支会把 sortby 整个覆盖成 position asc（kanban.go:209-216），
		// 发了也是白发，还会让人误以为板面支持自定义排序
		{
			query: {
				per_page: BOARD_TASKS_PER_BUCKET,
				...(params.filter ? { filter: params.filter } : {}),
			},
		},
	);

	assertBucketShape(result.items, viewId);
	return result as Paginated<Bucket>;
}

/** 每个桶最多取这么多任务。上游 per_page 上限是 50，超了会被静默截断。 */
export const BOARD_TASKS_PER_BUCKET = 50;

export interface BucketWritePayload {
	title: string;
	limit?: number;
	position?: number;
}

/** ⚠️ v1 里 PUT 才是新建。 */
export function createBucket(
	projectId: number,
	viewId: number,
	payload: BucketWritePayload,
	client: CaltonClient = apiClient,
): Promise<Bucket> {
	return client.put<Bucket>(`/projects/${projectId}/views/${viewId}/buckets`, payload);
}

/**
 * ⚠️ v1 里 POST 是全量替换更新。不过 `Bucket.Update` 只写
 * title / limit / position 三列（kanban.go:348-357），其余字段回传也不会生效。
 */
export function updateBucket(
	projectId: number,
	viewId: number,
	bucketId: number,
	payload: BucketWritePayload,
	client: CaltonClient = apiClient,
): Promise<Bucket> {
	return client.post<Bucket>(`/projects/${projectId}/views/${viewId}/buckets/${bucketId}`, payload);
}

/**
 * 删桶**不删任务**：桶里的任务会被移到该视图的默认桶（kanban.go:414-420）。
 * UI 上的确认文案必须说清楚这一点，否则用户会以为任务跟着一起没了而不敢删。
 *
 * 删最后一个桶会被后端拒绝：412 + code 10003（error.go:1877-1887）。
 */
export function deleteBucket(
	projectId: number,
	viewId: number,
	bucketId: number,
	client: CaltonClient = apiClient,
): Promise<void> {
	return client.delete<void>(`/projects/${projectId}/views/${viewId}/buckets/${bucketId}`);
}

/** 删最后一个桶时后端返回的错误码。前端预判禁用，这个码是兜底。 */
export const ERR_CANNOT_REMOVE_LAST_BUCKET = 10003;
/** 往已满的桶里放任务时的错误码（412）。拖拽落到满列时会撞上。 */
export const ERR_BUCKET_LIMIT_EXCEEDED = 10004;

/**
 * 把任务移进某个桶。
 *
 * ⚠️ **这一个调用会连带改动别的东西**，别拿请求体去推断结果：
 * - 移进 done 列会把任务标记为完成并写 `done_at`；移出则取消完成
 *   （kanban_task_bucket.go:137-159）
 * - ★ **重复任务**被移进 done 列时，服务端会把它改送到该视图的**默认列**，
 *   而不是你指定的 done 列（同文件 145-155）—— 落点和用户拖到的地方不一样
 * - done 状态一旦变化，任务还会被同步进**同项目其他视图**的 done 列（193-215）
 *
 * 所以成功之后仍然必须重取板面。
 *
 * ★ **响应体自相矛盾**（coder-b 实测，源码读不出来）：把重复任务移进 done 列时，
 * 顶层 `bucket_id` 是服务端修正后的**真实落点**（默认列），而嵌套的 `bucket.id`
 * 原样回显**你请求的那个列**。同一个响应里两个字段给出不同答案。
 * **只认顶层 `bucket_id`。** 读嵌套那个会以为移动成功了。
 */
export function moveTaskToBucket(
	projectId: number,
	viewId: number,
	bucketId: number,
	taskId: number,
	client: CaltonClient = apiClient,
): Promise<MoveTaskResponse> {
	return client.post<MoveTaskResponse>(
		`/projects/${projectId}/views/${viewId}/buckets/${bucketId}/tasks`,
		{
			task_id: taskId,
			bucket_id: bucketId,
			project_view_id: viewId,
		},
	);
}

/**
 * 移动任务的响应。
 *
 * ⚠️ `bucket_id`（顶层）与 `bucket.id`（嵌套）在重复任务场景下**不一致**：
 * 前者是真实落点，后者是请求回显。只用前者。
 */
export interface MoveTaskResponse {
	task_id?: number;
	/** ★ 服务端修正后的真实落点。重复任务移进 done 列时这里是默认列。 */
	bucket_id?: number;
	/** ⚠️ 请求回显，不是真实落点。留着只为说明它存在且不可信。 */
	bucket?: { id?: number };
}

/**
 * 设置任务在某视图内的 position。
 *
 * ⚠️ position 小于 `MinPositionSpacing`(0.01) 时，服务端会**重算整个视图的所有
 * position**（task_position.go:191-202），存下来的值和你发的不一样。
 * 同样意味着：成功之后本地的 position 不可信，要重取。
 */
export function setTaskPosition(
	taskId: number,
	viewId: number,
	position: number,
	client: CaltonClient = apiClient,
): Promise<unknown> {
	return client.post(`/tasks/${taskId}/position`, {
		project_view_id: viewId,
		position,
	});
}
