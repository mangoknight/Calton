import { keepPreviousData, useQuery } from '@tanstack/react-query';

import type { CaltonError } from '@/api/errors';
import type { Paginated } from '@/api/pagination';
import { listProjectTasks, listViewTasks, type ListTasksParams, type Task } from '@/api/tasks';
import { buildTaskTree, type TaskTree } from './subtask-tree';

export const taskKeys = {
	all: ['tasks'] as const,
	byView: (projectId: number, viewId: number, params: ListTasksParams) =>
		['tasks', 'view', projectId, viewId, params] as const,
	projectTree: (projectId: number) => ['tasks', 'project-tree', projectId] as const,
};

export function useViewTasks(projectId: number, viewId: number, params: ListTasksParams = {}) {
	return useQuery<Paginated<Task>, CaltonError>({
		queryKey: taskKeys.byView(projectId, viewId, params),
		queryFn: () => listViewTasks(projectId, viewId, params),
		// 翻页时保留上一页数据：否则每次翻页列表先塌成空态再长回来，
		// 会和"这个项目真没有任务"的空态在视觉上撞车。
		placeholderData: keepPreviousData,
	});
}

/**
 * 一个项目的**全部任务**（用于项目页展开子任务树）。
 *
 * `GET /projects/{id}/tasks` 每页至多 50，子任务树要正确嵌套就得把父、子都拉到，
 * 所以按 `x-pagination-total-pages` 逐页取；`MAX_PROJECT_TASK_PAGES` 是护栏，
 * 命中即截断（截断的话个别子任务可能落在后面页拿不到，退化成顶层平铺，不会炸）。
 * 列表接口已带 `related_tasks`，边信息现成，不用逐个任务再拉。
 *
 * `enabled` 让它**懒加载**：只有用户点开某个项目的任务时才发请求。
 */
export const MAX_PROJECT_TASK_PAGES = 20;

export interface ProjectTaskTreeResult {
	tree: TaskTree;
	total: number;
	truncated: boolean;
}

async function fetchProjectTaskTree(projectId: number): Promise<ProjectTaskTreeResult> {
	const first = await listProjectTasks(projectId, { page: 1 });
	const tasks = [...first.items];
	const totalPages = Math.min(first.totalPages, MAX_PROJECT_TASK_PAGES);
	for (let page = 2; page <= totalPages; page += 1) {
		const next = await listProjectTasks(projectId, { page });
		tasks.push(...next.items);
	}
	return {
		tree: buildTaskTree(tasks),
		total: tasks.length,
		truncated: first.totalPages > MAX_PROJECT_TASK_PAGES,
	};
}

export function useProjectTaskTree(projectId: number, enabled: boolean) {
	return useQuery<ProjectTaskTreeResult, CaltonError>({
		queryKey: taskKeys.projectTree(projectId),
		queryFn: () => fetchProjectTaskTree(projectId),
		enabled,
	});
}
