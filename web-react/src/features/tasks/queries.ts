import { keepPreviousData, useQuery } from '@tanstack/react-query';

import type { CaltonError } from '@/api/errors';
import type { Paginated } from '@/api/pagination';
import { listViewTasks, type ListTasksParams, type Task } from '@/api/tasks';

export const taskKeys = {
	all: ['tasks'] as const,
	byView: (projectId: number, viewId: number, params: ListTasksParams) =>
		['tasks', 'view', projectId, viewId, params] as const,
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
