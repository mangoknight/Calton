import { useMutation, useQueryClient } from '@tanstack/react-query';

import {
	moveTaskToBucket,
	setTaskPosition,
	type Bucket,
	type MoveTaskResponse,
} from '@/api/buckets';
import type { CaltonError } from '@/api/errors';
import type { Paginated } from '@/api/pagination';
import { applyTaskMove, type TaskMove } from './board-move';
import { bucketKeys } from './bucket-queries';

/**
 * 拖拽落下后的写入（F07b）。
 *
 * ## 为什么是两个请求，且必须并发
 *
 * v1 没有"把任务移到某列的某个位置"这一个接口，只有分开的两个：
 *   POST /projects/{p}/views/{v}/buckets/{b}/tasks  —— 改所属列
 *   POST /tasks/{id}/position                       —— 改列内位置
 * 串行发会让用户先看到卡片跳到目标列的末尾、再跳到正确位置。并发发没有这个中间态。
 *
 * ## 失败时为什么要"两者一致回滚"
 *
 * `Promise.all` 在第一个 reject 时就 reject，但**不会取消另一个请求** ——
 * 它多半已经发出去甚至已经成功。所以失败后的服务端状态可能是"改了列没改位置"
 * 这种半截状态。UI 先整体回滚到拖拽前（用户看到的是"这次拖拽没生效"，是个自洽的状态），
 * 再靠 `onSettled` 的重取跟服务端对齐。**回滚只保证 UI 自洽，重取才保证正确。**
 *
 * ## 为什么成功也要重取
 *
 * 这个写入不是"一次调用只改一处"：
 * - 移进/移出 done 列会连带改任务的 done 与 done_at；
 * - ★ 重复任务移进 done 列会被服务端改送到**默认列**，落点和用户拖到的地方不同；
 * - done 变化还会把任务同步进同项目其他视图的 done 列；
 * - position 低于 0.01 时服务端重算整个视图的 position。
 * 乐观状态在以上任一情况下都是错的，所以 `onSettled` 无条件重取。
 *
 * ## 为什么还要在 onSuccess 里再纠一次
 *
 * 光靠 `onSettled` 重取，重复任务会出现一次可见的闪烁：卡片先停在用户拖到的 done 列
 * （乐观状态，错的），等重取回来才跳到默认列。用户看到的是"移动成功了又自己跳走"，
 * 像个 bug。响应里顶层 `bucket_id` 已经告诉了我们真实落点，所以在重取落地之前
 * 先按它纠正一次，把闪烁窗口压到最小。
 *
 * ⚠️ 只认**顶层 `bucket_id`**：同一个响应里嵌套的 `bucket.id` 回显的是请求值，
 * 读它等于什么都没纠（coder-b 实测）。
 */
export function useMoveTask(projectId: number, viewId: number, filter = '') {
	const queryClient = useQueryClient();
	// ⚠️ 必须带上 filter：board 的 query key 含 filter，
	// 少了它乐观更新会写到另一个缓存条目上，筛选状态下拖拽会看起来没反应。
	const boardKey = bucketKeys.board(projectId, viewId, filter);

	return useMutation<
		MoveTaskResponse,
		CaltonError,
		TaskMove,
		{ previous: Paginated<Bucket> | undefined }
	>({
		mutationFn: async (move) => {
			const [moved] = await Promise.all([
				moveTaskToBucket(projectId, viewId, move.toBucketId, move.taskId),
				setTaskPosition(move.taskId, viewId, move.position),
			]);
			return moved;
		},

		onMutate: async (move) => {
			// 取消在途的板面请求，否则它可能在乐观更新之后落地，把界面覆盖回旧数据
			await queryClient.cancelQueries({ queryKey: boardKey });

			const previous = queryClient.getQueryData<Paginated<Bucket>>(boardKey);
			if (previous) {
				queryClient.setQueryData(boardKey, applyTaskMove(previous, move));
			}
			return { previous };
		},

		onSuccess: (moved, move) => {
			// 顶层 bucket_id 才是真实落点；与请求不一致说明服务端改送了（典型：重复任务）
			const landed = moved?.bucket_id;
			if (landed === undefined || landed === move.toBucketId) return;

			const current = queryClient.getQueryData<Paginated<Bucket>>(boardKey);
			if (!current) return;

			queryClient.setQueryData(
				boardKey,
				applyTaskMove(current, { ...move, fromBucketId: move.toBucketId, toBucketId: landed }),
			);
		},

		onError: (_error, _move, context) => {
			// 整块换回拖拽前的快照：列的归属与位置是同一份数据，回滚天然是一致的
			if (context?.previous) {
				queryClient.setQueryData(boardKey, context.previous);
			}
		},

		onSettled: () => queryClient.invalidateQueries({ queryKey: boardKey }),
	});
}
