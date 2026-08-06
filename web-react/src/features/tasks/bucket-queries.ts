import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
	createBucket,
	deleteBucket,
	listBucketsWithTasks,
	updateBucket,
	type Bucket,
	type BucketWritePayload,
} from '@/api/buckets';
import type { CaltonError } from '@/api/errors';
import type { Paginated } from '@/api/pagination';

export const bucketKeys = {
	all: ['buckets'] as const,
	board: (projectId: number, viewId: number, filter = '') =>
		['buckets', 'board', projectId, viewId, filter] as const,
};

export function useBoard(projectId: number, viewId: number, filter = '') {
	return useQuery<Paginated<Bucket>, CaltonError>({
		queryKey: bucketKeys.board(projectId, viewId, filter),
		queryFn: () => listBucketsWithTasks(projectId, viewId, { filter }),
	});
}

interface BucketMutationContext {
	projectId: number;
	viewId: number;
}

/**
 * 桶的增删改一律**失效重取整块板面**，不做局部乐观更新。
 *
 * 理由不是省事：后端一次调用会连带改动别的东西 ——
 * 删桶会把桶里的任务搬到默认桶，还可能把视图上的 `default_bucket_id` /
 * `done_bucket_id` 清零（kanban.go:388-420）；done bucket 的双向联动在 T28 那侧
 * 还带着 reentrancy guard。也就是说**不能假设一次写只产生一处状态变更**，
 * 前端照着请求体去猜新状态必然会猜漏。整块重取是这里唯一能保证一致的做法。
 */
function useInvalidateBoard() {
	const queryClient = useQueryClient();
	return ({ projectId, viewId }: BucketMutationContext) =>
		queryClient.invalidateQueries({ queryKey: bucketKeys.board(projectId, viewId) });
}

export function useCreateBucket(context: BucketMutationContext) {
	const invalidate = useInvalidateBoard();

	return useMutation<Bucket, CaltonError, BucketWritePayload>({
		mutationFn: (payload) => createBucket(context.projectId, context.viewId, payload),
		onSuccess: () => invalidate(context),
	});
}

export function useUpdateBucket(context: BucketMutationContext) {
	const invalidate = useInvalidateBoard();

	return useMutation<Bucket, CaltonError, { id: number } & BucketWritePayload>({
		mutationFn: ({ id, ...payload }) =>
			updateBucket(context.projectId, context.viewId, id, payload),
		onSuccess: () => invalidate(context),
	});
}

export function useDeleteBucket(context: BucketMutationContext) {
	const invalidate = useInvalidateBoard();

	return useMutation<void, CaltonError, number>({
		mutationFn: (id) => deleteBucket(context.projectId, context.viewId, id),
		onSuccess: () => invalidate(context),
	});
}
