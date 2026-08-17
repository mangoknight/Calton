import { useMutation, useQueryClient } from '@tanstack/react-query';

import type { CaltonError } from '@/api/errors';
import { createTask, type Task } from '@/api/tasks';
import { boardKeys } from '@/features/board/queries';
import { bucketKeys } from './bucket-queries';
import { taskKeys } from './queries';

/**
 * 新建任务的 mutation（F-quickadd）。
 *
 * ⚠️ 新建走 `createTask`（PUT），不是 updateTask（POST 全量替换）。
 *
 * ## 失效范围为什么这么宽
 *
 * 一条新任务会同时出现在三个不相干的读入口里，谁也别想靠请求体去猜新状态：
 * - `taskKeys.all`（`['tasks']`）覆盖 List 视图的 `byView` 和项目页的 `projectTree`；
 * - `boardKeys.allTasks`：全局按人看板取的是 `GET /tasks` 全量，新任务也在其中；
 * - `bucketKeys.all`：kanban 板面按桶取，新任务落进默认桶，得让板面重取才看得到。
 * 失效多了顶多多发几个请求，失效漏了就是"建了却不显示"的鬼故事。
 */
export function useCreateTask(projectId: number) {
	const queryClient = useQueryClient();

	return useMutation<Task, CaltonError, { title: string }>({
		mutationFn: (payload) => createTask(projectId, payload),
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: taskKeys.all });
			void queryClient.invalidateQueries({ queryKey: boardKeys.allTasks });
			void queryClient.invalidateQueries({ queryKey: bucketKeys.all });
		},
	});
}
