import { useMutation, useQueryClient } from '@tanstack/react-query';

import type { CaltonError } from '@/api/errors';
import { buildTaskUpdatePayload, updateTask, type Task } from '@/api/tasks';
import { boardKeys } from '@/features/board/queries';
import { bucketKeys } from './bucket-queries';
import { taskKeys } from './queries';

/**
 * 在列表里就地勾选完成/重开。
 *
 * ⚠️ `POST /tasks/{id}` 是**全量替换**（见 `api/tasks.ts`），所以：
 * - `task` 必须是**服务端返回的完整对象**（视图任务端点给的就是），经
 *   `buildTaskUpdatePayload` 产出全列请求体，绝不能只发 `{done}`；
 * - 必须**回传 assignees**：它是被 acted-on 的字段，省略 = 清空指派
 *   （与 `useUpdateTask` / `useSetTaskStatus` 同一条规矩）。
 *
 * 成功后失效任务、看板、桶三类缓存：改 done 会让后端把任务挪进/挪出完成桶。
 */
export function useToggleTaskDone() {
	const queryClient = useQueryClient();

	return useMutation<Task, CaltonError, { task: Task; done: boolean }>({
		mutationFn: ({ task, done }) => {
			const payload = buildTaskUpdatePayload(task, { done });
			payload.assignees = (task.assignees ?? []).map((a) => ({
				id: a.id,
				username: a.username ?? '',
				name: a.name ?? '',
			}));
			return updateTask(task.id, payload);
		},
		onSettled: () => {
			void queryClient.invalidateQueries({ queryKey: taskKeys.all });
			void queryClient.invalidateQueries({ queryKey: boardKeys.allTasks });
			void queryClient.invalidateQueries({ queryKey: bucketKeys.all });
		},
	});
}
