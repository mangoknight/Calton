import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
	createComment,
	deleteComment,
	listComments,
	updateComment,
	type TaskComment,
} from '@/api/comments';
import type { CaltonError } from '@/api/errors';
import type { Paginated } from '@/api/pagination';

export const commentKeys = {
	list: (taskId: number) => ['comments', taskId] as const,
};

export function useComments(taskId: number) {
	return useQuery<Paginated<TaskComment>, CaltonError>({
		queryKey: commentKeys.list(taskId),
		queryFn: () => listComments(taskId),
	});
}

/**
 * 三个写操作都失效重取评论列表。
 *
 * 不做局部乐观拼接的理由：新建评论的 `id` / `created` / `author` 都由服务端生成，
 * 本地拼一条出来必然缺字段，而"作者是谁"恰好决定了删除按钮显不显示 ——
 * 拼错就会出现一条自己删不掉的评论，直到刷新。
 */
function useCommentInvalidation(taskId: number) {
	const queryClient = useQueryClient();
	return () => queryClient.invalidateQueries({ queryKey: commentKeys.list(taskId) });
}

export function useCreateComment(taskId: number) {
	const invalidate = useCommentInvalidation(taskId);
	return useMutation<TaskComment, CaltonError, string>({
		mutationFn: (comment) => createComment(taskId, comment),
		onSuccess: invalidate,
	});
}

export function useUpdateComment(taskId: number) {
	const invalidate = useCommentInvalidation(taskId);
	return useMutation<TaskComment, CaltonError, { id: number; comment: string }>({
		mutationFn: ({ id, comment }) => updateComment(taskId, id, comment),
		onSuccess: invalidate,
	});
}

export function useDeleteComment(taskId: number) {
	const invalidate = useCommentInvalidation(taskId);
	return useMutation<unknown, CaltonError, number>({
		// ⚠️ 带 taskId —— 单条评论端点的 taskID 参与校验
		mutationFn: (commentId) => deleteComment(taskId, commentId),
		onSuccess: invalidate,
	});
}
