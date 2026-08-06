import { apiClient, type CaltonClient } from './client';
import type { Paginated } from './pagination';

/**
 * 项目视图（终稿 §5.1 的"视图 (5)"端点组）。
 *
 * 实测事实（tester）：**新建项目会自动带出 4 个 view**（list / gantt / table / kanban），
 * 一次性返回四个 id。所以视图容器**不需要处理"项目还没有视图"的空态** ——
 * 真出现空列表说明数据异常，应当如实报错而不是静默渲染一个空壳。
 */

/** 契约里 view_kind 是字符串枚举，不是数字。 */
export const VIEW_KINDS = ['list', 'gantt', 'table', 'kanban'] as const;
export type ViewKind = (typeof VIEW_KINDS)[number];

export function isViewKind(value: string): value is ViewKind {
	return (VIEW_KINDS as readonly string[]).includes(value);
}

export interface ProjectView {
	id: number;
	project_id: number;
	title: string;
	view_kind: ViewKind;
	position?: number;
	bucket_configuration_mode?: 'none' | 'manual' | 'filter';
	default_bucket_id?: number;
	done_bucket_id?: number;
	filter?: unknown;
}

export function listProjectViews(
	projectId: number,
	client: CaltonClient = apiClient,
): Promise<Paginated<ProjectView>> {
	return client.requestList<ProjectView>('GET', `/projects/${projectId}/views`);
}
