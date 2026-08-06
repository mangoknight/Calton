import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';

import type { CaltonError } from '@/api/errors';
import type { Project } from '@/api/projects';
import {
	createSavedFilter,
	deleteSavedFilter,
	getSavedFilter,
	updateSavedFilter,
	type SavedFilter,
	type SavedFilterWritePayload,
} from '@/api/saved-filters';
import { useProjects } from '@/features/projects/queries';
import { projectKeys } from '@/features/projects/queries';
import { savedFilterIdFromProjectId } from './pseudo-project';

export const savedFilterKeys = {
	all: ['saved-filters'] as const,
	detail: (filterId: number) => ['saved-filters', filterId] as const,
};

/** 侧栏用的一条过滤器（由伪项目投影而来）。 */
export interface SavedFilterEntry {
	/** 真实的 saved filter id，用于 `/filters/{id}` 路由与写端点。 */
	filterId: number;
	/** 伪项目 id，用于任务查询路径。 */
	projectId: number;
	title: string;
}

/**
 * 侧栏里的过滤器清单。
 *
 * ⚠️ 数据源是 **`GET /projects`**，不是某个 `/filters` 列表端点（那个不存在）。
 * saved filter 以负 ID 伪项目的形式混在项目列表里返回，这里把它们筛出来并换算回
 * filter id。真实项目由 `buildProjectTree` 那边负责，两边**同源不同筛**。
 */
export function useSavedFilters() {
	const query = useProjects();

	const filters = useMemo<SavedFilterEntry[]>(() => {
		const projects: Project[] = query.data?.items ?? [];
		return projects
			.map((project) => {
				// 换算函数自带"-1 是收藏夹"的判据，这里不要再写裸算术
				const filterId = savedFilterIdFromProjectId(project.id);
				return filterId === null ? null : { filterId, projectId: project.id, title: project.title };
			})
			.filter((entry): entry is SavedFilterEntry => entry !== null);
	}, [query.data]);

	return { filters, query };
}

export function useSavedFilter(filterId: number) {
	return useQuery<SavedFilter, CaltonError>({
		queryKey: savedFilterKeys.detail(filterId),
		queryFn: () => getSavedFilter(filterId),
	});
}

/**
 * 增删改都要**同时失效项目列表** —— 侧栏的过滤器清单是从 `GET /projects` 派生的，
 * 只失效 saved-filter 自己的 key，侧栏不会更新。
 * 这是"没有列表端点"这件事在缓存层的直接后果，容易漏。
 */
function useSavedFilterInvalidation() {
	const queryClient = useQueryClient();
	return () => {
		queryClient.invalidateQueries({ queryKey: savedFilterKeys.all });
		queryClient.invalidateQueries({ queryKey: projectKeys.all });
	};
}

export function useCreateSavedFilter() {
	const invalidate = useSavedFilterInvalidation();
	return useMutation<SavedFilter, CaltonError, SavedFilterWritePayload>({
		mutationFn: (payload) => createSavedFilter(payload),
		onSuccess: invalidate,
	});
}

export function useUpdateSavedFilter(filterId: number) {
	const invalidate = useSavedFilterInvalidation();
	return useMutation<SavedFilter, CaltonError, SavedFilterWritePayload>({
		mutationFn: (payload) => updateSavedFilter(filterId, payload),
		onSuccess: invalidate,
	});
}

export function useDeleteSavedFilter() {
	const invalidate = useSavedFilterInvalidation();
	return useMutation<unknown, CaltonError, number>({
		mutationFn: (filterId) => deleteSavedFilter(filterId),
		onSuccess: invalidate,
	});
}
