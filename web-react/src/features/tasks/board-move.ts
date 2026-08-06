import type { Bucket } from '@/api/buckets';
import type { Paginated } from '@/api/pagination';
import { positionForInsert } from '@/lib/task-position';

/**
 * 看板拖拽的纯逻辑（F07b）：落点解析与本地乐观改板。
 *
 * 拆成纯函数是为了能不经过 dnd-kit 的指针事件就测到 —— jsdom 里模拟拖拽既脆又慢，
 * 而真正容易出错的是"落在哪、position 算多少、卡片搬到哪个数组"，与手势无关。
 * 手势本身由 F14 的 Playwright 冒烟覆盖。
 */

/** dnd-kit 的 id 必须全局唯一，任务和桶都用数字 id，会撞车，所以加前缀。 */
export const TASK_DRAG_PREFIX = 'task:';
export const BUCKET_DROP_PREFIX = 'bucket:';

export function taskDragId(taskId: number): string {
	return `${TASK_DRAG_PREFIX}${taskId}`;
}

export function bucketDropId(bucketId: number): string {
	return `${BUCKET_DROP_PREFIX}${bucketId}`;
}

function parsePrefixed(id: string | number, prefix: string): number | null {
	const raw = String(id);
	if (!raw.startsWith(prefix)) return null;
	const value = Number(raw.slice(prefix.length));
	return Number.isInteger(value) ? value : null;
}

export interface TaskMove {
	taskId: number;
	fromBucketId: number;
	toBucketId: number;
	position: number;
}

function findTaskBucket(buckets: readonly Bucket[], taskId: number): Bucket | undefined {
	return buckets.find((bucket) => (bucket.tasks ?? []).some((task) => task.id === taskId));
}

/**
 * 把一次落下解析成"要发什么请求"。返回 null 表示这次拖拽不需要发任何请求。
 *
 * `over` 有两种：落在另一张卡上（`task:N`）或落在列的空白处（`bucket:N`）。
 * 落在卡上时插到那张卡**之前**，这与拖拽时的视觉预期一致。
 */
export function resolveDrop(
	buckets: readonly Bucket[],
	activeId: string | number,
	overId: string | number | null | undefined,
): TaskMove | null {
	if (overId === null || overId === undefined) return null;

	const taskId = parsePrefixed(activeId, TASK_DRAG_PREFIX);
	if (taskId === null) return null;

	const from = findTaskBucket(buckets, taskId);
	if (!from) return null;

	const overTaskId = parsePrefixed(overId, TASK_DRAG_PREFIX);
	const overBucketId = parsePrefixed(overId, BUCKET_DROP_PREFIX);

	let target: Bucket | undefined;

	if (overTaskId !== null) {
		// 落回自己身上：没动
		if (overTaskId === taskId) return null;
		target = findTaskBucket(buckets, overTaskId);
	} else if (overBucketId !== null) {
		target = buckets.find((bucket) => bucket.id === overBucketId);
	}
	if (!target) return null;

	/**
	 * ⚠️ 下标必须在**剔除被拖任务之后**的数组里算。
	 * 在原数组里算下标、再交给会自行剔除的 positionForInsert，两边坐标系差一位 ——
	 * 同列拖动时就会插错位置。（这条是被测试抓出来的。）
	 */
	const targetTasks = (target.tasks ?? []).filter((task) => task.id !== taskId);
	const targetIndex =
		overTaskId !== null
			? targetTasks.findIndex((task) => task.id === overTaskId)
			: // 落在列的空白处 = 放到末尾
				targetTasks.length;

	const position = positionForInsert(targetTasks, taskId, targetIndex);

	// 同列且落点没变化时不必打接口。注意这里比的是算出来的 position，
	// 而不是 index —— index 相同但列不同仍然是一次真实的移动。
	const current = (from.tasks ?? []).find((task) => task.id === taskId);
	if (from.id === target.id && current?.position === position) return null;

	return { taskId, fromBucketId: from.id, toBucketId: target.id, position };
}

/**
 * 本地乐观改板：把卡片从原列搬到目标列，写上新 position 并按 position 重排。
 *
 * ⚠️ 这只是**给眼睛看的中间态**。服务端在同一次调用里可能还做了别的事
 * （改 done、把重复任务改送到默认列、position 太小时重算整个视图），
 * 所以无论成功失败都必须重取，不能拿这个结果当最终状态。见 `useMoveTask`。
 */
export function applyTaskMove(board: Paginated<Bucket>, move: TaskMove): Paginated<Bucket> {
	const moving = findTaskBucket(board.items, move.taskId)?.tasks?.find(
		(task) => task.id === move.taskId,
	);
	if (!moving) return board;

	const items = board.items.map((bucket) => {
		const tasks = bucket.tasks ?? [];

		if (bucket.id === move.fromBucketId && bucket.id === move.toBucketId) {
			// 同列内重排：数量不变，只改 position 再排序
			return {
				...bucket,
				tasks: sortByPosition(
					tasks.map((task) =>
						task.id === move.taskId ? { ...task, position: move.position } : task,
					),
				),
			};
		}

		if (bucket.id === move.fromBucketId) {
			return {
				...bucket,
				tasks: tasks.filter((task) => task.id !== move.taskId),
				// count 是总数，搬走一张就减一
				count: Math.max(0, bucket.count - 1),
			};
		}

		if (bucket.id === move.toBucketId) {
			return {
				...bucket,
				tasks: sortByPosition([...tasks, { ...moving, position: move.position }]),
				count: bucket.count + 1,
			};
		}

		return bucket;
	});

	return { ...board, items };
}

function sortByPosition<T extends { position?: number }>(tasks: T[]): T[] {
	return [...tasks].sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
}
