import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { assignUser, unassignUser } from '@/api/assignees';
import type { CaltonError } from '@/api/errors';
import { buildTaskUpdatePayload, listTasks, updateTask, type Task, type TaskPatch } from '@/api/tasks';

/**
 * 全局按人看板的数据层。
 *
 * ## 为什么取全量、在客户端过滤
 *
 * 按 **assignee** 分列，还要能按**项目**过滤 —— 而 `project` **不在** filter DSL 的
 * 可筛选字段白名单里（`FILTERABLE_TASK_FIELDS`，见 `api/tasks.ts`），
 * 用 `filter=project_id = 3` 会 400/4016。所以项目过滤只能在拿到任务后本地做。
 * 既然项目要本地过滤，人也一并本地过滤，逻辑集中、少一处 DSL 边界。
 *
 * ## 分页
 *
 * `GET /tasks` 每页至多 50（上游 `config.go:359` 的上限）。全局看板要看到全部，
 * 所以按 `x-pagination-total-pages` 逐页取。`MAX_PAGES` 是护栏：任务真多到
 * 1000 条以上时先截断并在页面上说明，而不是把浏览器拖死。
 */
export const MAX_PAGES = 20;

export const boardKeys = {
	allTasks: ['board', 'all-tasks'] as const,
};

export interface AllTasksResult {
	tasks: Task[];
	/** 命中了 MAX_PAGES 护栏、后面还有没取回来的页 —— 页面据此提示"结果被截断"。 */
	truncated: boolean;
}

async function fetchAllTasks(): Promise<AllTasksResult> {
	const first = await listTasks({ page: 1 });
	const tasks = [...first.items];
	const totalPages = Math.min(first.totalPages, MAX_PAGES);

	for (let page = 2; page <= totalPages; page += 1) {
		const next = await listTasks({ page });
		tasks.push(...next.items);
	}

	return { tasks, truncated: first.totalPages > MAX_PAGES };
}

export function useAllTasks() {
	return useQuery<AllTasksResult, CaltonError>({
		queryKey: boardKeys.allTasks,
		queryFn: fetchAllTasks,
	});
}

export interface ReassignArgs {
	taskId: number;
	/** 拖出的那一列对应的人；从「未分配」拖出时为 null。 */
	fromUserId: number | null;
	/** 拖入的那一列对应的人；拖进「未分配」时为 null。 */
	toUserId: number | null;
}

/**
 * 把一个任务从某人的列挪到另一个人的列 —— 即改 assignee。
 *
 * 语义（多 assignee 时任务在每个 assignee 的列里都出现，所以拖的是**这一次分派**）：
 * - A 列 → B 列：删 A、加 B（这条分派移动到 B）。
 * - 未分配 → B：加 B。
 * - A 列 → 未分配：删 A。
 *
 * ⚠️ 先加后删：如果先删再加、加那步失败，任务会变成「谁都没指派」而不是留在原地。
 * 先加成功了再删，最坏情况是两个人都在（可见、可再拖），不会凭空消失。
 * 同人拖回自己不发请求。
 */
export function useReassignTask() {
	const queryClient = useQueryClient();
	return useMutation<void, CaltonError, ReassignArgs>({
		mutationFn: async ({ taskId, fromUserId, toUserId }) => {
			if (fromUserId === toUserId) return;
			if (toUserId !== null) await assignUser(taskId, toUserId);
			if (fromUserId !== null) await unassignUser(taskId, fromUserId);
		},
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: boardKeys.allTasks });
		},
	});
}

/** 按状态分列时的三列。task 没有独立状态字段，由 done + percent_done 推导。 */
export type BoardStatus = 'todo' | 'doing' | 'done';

/**
 * 一个任务落在哪个状态列：
 * - `done` → 已完成
 * - 未完成但有进度（percent_done > 0） → 进行中
 * - 其余 → 待办
 */
export function statusOf(task: Task): BoardStatus {
	if (task.done) return 'done';
	if ((task.percent_done ?? 0) > 0) return 'doing';
	return 'todo';
}

/**
 * 拖动改状态。
 *
 * ⚠️ POST /tasks/{id} 是**全量替换**（见 `api/tasks.ts`），所以必须用
 * `buildTaskUpdatePayload(完整task, patch)` —— `task` 得是服务端返回的完整对象
 * （`GET /tasks` 给的就是），只发 `{done:true}` 会把别的列清空。
 *
 * - → 已完成：`done = true`
 * - → 待办：`done = false, percent_done = 0`
 * - → 进行中：`done = false`，进度设成一个非零值（已在 1–99 之间则保留，否则给 50）——
 *   "进行中"没有天然的百分比，拖进来只能给个约定值，这一列的拖拽语义天生是模糊的。
 */
export function useSetTaskStatus() {
	const queryClient = useQueryClient();
	return useMutation<void, CaltonError, { task: Task; status: BoardStatus }>({
		mutationFn: async ({ task, status }) => {
			if (statusOf(task) === status) return;
			const patch: TaskPatch =
				status === 'done'
					? { done: true }
					: status === 'todo'
						? { done: false, percent_done: 0 }
						: {
								done: false,
								percent_done:
									task.percent_done && task.percent_done > 0 && task.percent_done < 100
										? task.percent_done
										: 50,
							};
			await updateTask(task.id, buildTaskUpdatePayload(task, patch));
		},
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: boardKeys.allTasks });
		},
	});
}
