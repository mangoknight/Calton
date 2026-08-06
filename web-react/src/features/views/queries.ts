import { useQuery } from '@tanstack/react-query';

import type { CaltonError } from '@/api/errors';
import type { Paginated } from '@/api/pagination';
import { listProjectViews, type ProjectView } from '@/api/views';

export const viewKeys = {
	all: ['views'] as const,
	byProject: (projectId: number) => ['views', 'project', projectId] as const,
};

export function useProjectViews(projectId: number | undefined) {
	return useQuery<Paginated<ProjectView>, CaltonError>({
		queryKey: viewKeys.byProject(projectId ?? -1),
		queryFn: () => listProjectViews(projectId as number),
		enabled: projectId !== undefined,
		// 视图集合几乎不变，多缓存一会儿；切换视图时不该重新拉一遍
		staleTime: 5 * 60_000,
	});
}
