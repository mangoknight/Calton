import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { CaltonError } from '@/api/errors';
import {
	createLabel,
	deleteLabel,
	listLabels,
	updateLabel,
	type Label,
	type LabelWritePayload,
} from '@/api/labels';
import type { Paginated } from '@/api/pagination';

export const labelKeys = {
	all: ['labels'] as const,
	list: () => ['labels', 'list'] as const,
};

/**
 * 标签列表 —— 当前用户的可见集合，**含别人建的**。
 *
 * 这里刻意不做任何按创建者的过滤：能看见的都要列出来，
 * 哪些能改由 `canManageLabel` 在渲染按钮时单独判（见 `permissions.ts`）。
 */
export function useLabels() {
	return useQuery<Paginated<Label>, CaltonError>({
		queryKey: labelKeys.list(),
		queryFn: () => listLabels(),
	});
}

/** ⚠️ PUT 才是新建。后端**不校验标题**，前端也不要补，见 `api/labels.ts` 文件头。 */
export function useCreateLabel() {
	const queryClient = useQueryClient();

	return useMutation<Label, CaltonError, LabelWritePayload>({
		mutationFn: (payload) => createLabel(payload),
		onSuccess: () => queryClient.invalidateQueries({ queryKey: labelKeys.all }),
	});
}

/**
 * ⚠️ POST 是全量替换：`payload` 的三列会原样落库，没在里面的列被清成空串。
 * 类型上三列必填就是为了让"漏列"编译不过。
 */
export function useUpdateLabel() {
	const queryClient = useQueryClient();

	return useMutation<Label, CaltonError, { id: number; payload: LabelWritePayload }>({
		mutationFn: ({ id, payload }) => updateLabel(id, payload),
		onSuccess: () => queryClient.invalidateQueries({ queryKey: labelKeys.all }),
	});
}

export function useDeleteLabel() {
	const queryClient = useQueryClient();

	return useMutation<unknown, CaltonError, number>({
		mutationFn: (id) => deleteLabel(id),
		onSuccess: () => queryClient.invalidateQueries({ queryKey: labelKeys.all }),
	});
}
