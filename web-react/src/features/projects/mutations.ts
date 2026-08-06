import { useMutation, useQueryClient } from '@tanstack/react-query';

import { apiClient } from '@/api/client';
import type { CaltonError } from '@/api/errors';
import type { Project } from '@/api/projects';
import { projectKeys } from './queries';

/**
 * 项目增删改。
 *
 * ⚠️ v1 的动词是反的（终稿 §1.1）：
 *   **PUT /projects 是新建**，**POST /projects/{id} 是更新且为全量替换**。
 * 全量替换意味着更新时必须回传完整对象 —— 只发改动的字段会把其余字段清空（AC-6）。
 */

export interface CreateProjectPayload {
	title: string;
	description?: string;
	hex_color?: string;
	parent_project_id?: number | null;
}

export function useCreateProject() {
	const queryClient = useQueryClient();

	return useMutation<Project, CaltonError, CreateProjectPayload>({
		mutationFn: (payload) => apiClient.put<Project>('/projects', payload),
		onSuccess: () => queryClient.invalidateQueries({ queryKey: projectKeys.all }),
	});
}

export function useUpdateProject() {
	const queryClient = useQueryClient();

	return useMutation<Project, CaltonError, Project>({
		// 全量替换：调用方负责把完整对象传进来
		mutationFn: (project) => apiClient.post<Project>(`/projects/${project.id}`, project),
		onSuccess: () => queryClient.invalidateQueries({ queryKey: projectKeys.all }),
	});
}

export function useDeleteProject() {
	const queryClient = useQueryClient();

	return useMutation<unknown, CaltonError, number>({
		mutationFn: (id) => apiClient.delete(`/projects/${id}`),
		onSuccess: () => queryClient.invalidateQueries({ queryKey: projectKeys.all }),
	});
}
