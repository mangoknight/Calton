import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
	assignUser,
	listProjectUsers,
	listTaskAssignees,
	searchUsers,
	unassignUser,
	type AssignableUser,
} from '@/api/assignees';
import type { CaltonError } from '@/api/errors';
import {
	addLabelToTask,
	listLabels,
	listTaskLabels,
	removeLabelFromTask,
	type Label,
} from '@/api/labels';
import type { Paginated } from '@/api/pagination';
import { taskDetailKeys } from './detail-queries';
import { taskKeys } from './queries';

export const relationKeys = {
	allLabels: (search: string) => ['labels', 'all', search] as const,
	taskLabels: (taskId: number) => ['labels', 'task', taskId] as const,
	taskAssignees: (taskId: number) => ['assignees', 'task', taskId] as const,
	userSearch: (search: string) => ['users', 'search', search] as const,
};

/**
 * 可选标签的全集。
 *
 * ⚠️ **不要在返回值上再加权限过滤** —— `GET /labels` 返回什么就是能用什么，
 * 包括别人建的标签（挂到自己任务上实测 201）。加过滤会把共享标签从选择器里滤掉，
 * 是"能选、一点就 403"的镜像错误。详见 `api/labels.ts` 文件头。
 */
export function useAllLabels(search = '') {
	return useQuery<Paginated<Label>, CaltonError>({
		queryKey: relationKeys.allLabels(search),
		// F10 合并后 listLabels 收的是参数对象（支持 page/per_page/s），不再是裸搜索词
		queryFn: () => listLabels(search ? { s: search } : {}),
		staleTime: 60_000,
	});
}

export function useTaskLabels(taskId: number) {
	return useQuery<Paginated<Label>, CaltonError>({
		queryKey: relationKeys.taskLabels(taskId),
		queryFn: () => listTaskLabels(taskId),
	});
}

export function useTaskAssignees(taskId: number) {
	return useQuery<Paginated<AssignableUser>, CaltonError>({
		queryKey: relationKeys.taskAssignees(taskId),
		queryFn: () => listTaskAssignees(taskId),
	});
}

/**
 * 用户搜索。
 *
 * `enabled` 卡在"有搜索词"上不是性能优化：`GET /users` 空搜索时后端返回 `null`
 * （裸 return），拿它当"全部用户"会得到一个空列表，看起来像"没有人可指派"。
 */
export function useUserSearch(search: string) {
	return useQuery<Paginated<AssignableUser>, CaltonError>({
		queryKey: relationKeys.userSearch(search),
		queryFn: () => searchUsers(search),
		enabled: search.trim().length > 0,
	});
}

/**
 * 项目成员，作为任务指派的候选来源。
 *
 * ⚠️ 为什么不用全局 `GET /users` 搜索：那个端点受**可发现性**限制 —— 只有精确
 * 用户名不受限，子串匹配要 `discoverable_by_name=1`（默认 0），所以打"don"搜不到
 * "dongxp"，用户得记住完整用户名才能指派（这是忠实对齐 Go 的行为，见后端
 * `user_service.search_users`）。而 `projectusers` 列出的是项目全部成员、不受该限制，
 * 正是任务指派该看的人群 —— 点一下就能选，不用记全名。
 */
export function useProjectMembers(projectId: number | undefined) {
	return useQuery<Paginated<AssignableUser>, CaltonError>({
		queryKey: ['project-members', projectId] as const,
		queryFn: () => listProjectUsers(projectId!),
		enabled: typeof projectId === 'number' && projectId > 0,
	});
}

/**
 * 标签与指派的写操作一律**失效重取**，不做局部乐观拼接。
 *
 * 这几个关联在任务对象上也有副本（`task.labels` / `task.assignees`），
 * 列表/表格/看板里还各有一份。只改一处会让其它几处显示旧值，
 * 而它们恰好都在同一个页面上可见。
 */
function useRelationInvalidation(taskId: number) {
	const queryClient = useQueryClient();
	return () => {
		queryClient.invalidateQueries({ queryKey: relationKeys.taskLabels(taskId) });
		queryClient.invalidateQueries({ queryKey: relationKeys.taskAssignees(taskId) });
		queryClient.invalidateQueries({ queryKey: taskDetailKeys.detail(taskId) });
		queryClient.invalidateQueries({ queryKey: taskKeys.all });
	};
}

export function useAddLabel(taskId: number) {
	const invalidate = useRelationInvalidation(taskId);
	return useMutation<unknown, CaltonError, number>({
		mutationFn: (labelId) => addLabelToTask(taskId, labelId),
		onSuccess: invalidate,
	});
}

export function useRemoveLabel(taskId: number) {
	const invalidate = useRelationInvalidation(taskId);
	return useMutation<unknown, CaltonError, number>({
		mutationFn: (labelId) => removeLabelFromTask(taskId, labelId),
		onSuccess: invalidate,
	});
}

export function useAssignUser(taskId: number) {
	const invalidate = useRelationInvalidation(taskId);
	// ⚠️ 收数字 user id，不是用户名 —— 见 api/assignees.ts 文件头
	return useMutation<unknown, CaltonError, number>({
		mutationFn: (userId) => assignUser(taskId, userId),
		onSuccess: invalidate,
	});
}

export function useUnassignUser(taskId: number) {
	const invalidate = useRelationInvalidation(taskId);
	return useMutation<unknown, CaltonError, number>({
		mutationFn: (userId) => unassignUser(taskId, userId),
		onSuccess: invalidate,
	});
}
