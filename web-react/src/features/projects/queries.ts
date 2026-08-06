import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { listProjects, type ListProjectsParams, type Project } from '@/api/projects';
import type { CaltonError } from '@/api/errors';
import type { Paginated } from '@/api/pagination';
import { buildProjectTree, type ProjectTree } from './tree';

export const projectKeys = {
	all: ['projects'] as const,
	list: (params: ListProjectsParams) => ['projects', 'list', params] as const,
};

/**
 * 项目列表。
 *
 * 侧栏/项目树要的是**全量**项目，上游 per_page 上限是 50，所以这里显式取 50 一页；
 * 项目数超过 50 的情况留到 F04b 之后按需补翻页（届时改成 useInfiniteQuery）。
 */
export function useProjects(params: ListProjectsParams = {}) {
	return useQuery<Paginated<Project>, CaltonError>({
		queryKey: projectKeys.list(params),
		queryFn: () => listProjects({ per_page: 50, ...params }),
	});
}

export function useProjectTree(params: ListProjectsParams = {}): {
	tree: ProjectTree;
	query: ReturnType<typeof useProjects>;
} {
	const query = useProjects(params);
	const items = query.data?.items;

	const tree = useMemo(() => buildProjectTree(items ?? []), [items]);

	return { tree, query };
}
