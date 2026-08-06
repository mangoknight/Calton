import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { CaltonError } from '@/api/errors';
import {
	buildTaskUpdatePayload,
	getTask,
	updateTask,
	type Task,
	type TaskPatch,
} from '@/api/tasks';
import { bucketKeys } from './bucket-queries';
import { taskKeys } from './queries';

export const taskDetailKeys = {
	detail: (taskId: number) => ['tasks', 'detail', taskId] as const,
};

export function useTask(taskId: number) {
	return useQuery<{ data: Task; maxPermission: number | null }, CaltonError>({
		queryKey: taskDetailKeys.detail(taskId),
		queryFn: () => getTask(taskId),
	});
}

/**
 * 任务字段更新（F08a）。
 *
 * ## 读-改-写是硬性的
 *
 * `POST /tasks/{id}` 是**全量替换**：`Task.Update` 以 nil fields 调
 * `updateSingleTask`，15 个可写列全部按请求体写入、**没有一列回落旧值**
 * （tasks.go:1251-1253、1300-1302）。所以补丁必须盖在服务端刚返回的完整对象上，
 * 由 `buildTaskUpdatePayload` 产出。只发改动字段会把其余列清成零值，
 * 其中 `project_id` 归零会让任务从项目里消失，而接口返回 200。
 *
 * ## 乐观更新 + 失效重取
 *
 * 乐观改本地让勾选立刻有反馈；成功后仍然重取，因为改 `done` 会连带
 * 服务端改 `done_at`、并把任务挪进/挪出各看板视图的 done 列
 * （tasks.go 里 `t.Done != ot.Done` 那一段），这些都不是前端算得出来的。
 */
export function useUpdateTask(taskId: number) {
	const queryClient = useQueryClient();
	const key = taskDetailKeys.detail(taskId);

	return useMutation<
		Task,
		CaltonError,
		TaskPatch,
		{ previous: { data: Task; maxPermission: number | null } | undefined }
	>({
		mutationFn: (patch) => {
			const current = queryClient.getQueryData<{ data: Task }>(key)?.data;
			if (!current) {
				// 没有完整对象就发不了全量替换 —— 宁可显式失败，也不要发一个残缺 body
				// 把其余字段清空。这是 AC-6 最容易被绕过的地方。
				throw new Error('任务尚未加载完成，无法保存：全量替换需要完整对象');
			}
			return updateTask(taskId, buildTaskUpdatePayload(current, patch));
		},

		onMutate: async (patch) => {
			await queryClient.cancelQueries({ queryKey: key });
			const previous = queryClient.getQueryData<{ data: Task; maxPermission: number | null }>(key);
			if (previous) {
				queryClient.setQueryData(key, { ...previous, data: { ...previous.data, ...patch } });
			}
			return { previous };
		},

		onError: (_error, _patch, context) => {
			if (context?.previous) queryClient.setQueryData(key, context.previous);
		},

		onSettled: () => {
			queryClient.invalidateQueries({ queryKey: key });
			// 列表/表格/看板里都有这条任务的副本，改完得让它们也重取
			queryClient.invalidateQueries({ queryKey: taskKeys.all });
			queryClient.invalidateQueries({ queryKey: bucketKeys.all });
		},
	});
}
