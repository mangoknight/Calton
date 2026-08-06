import { apiClient, type CaltonClient } from './client';

/**
 * saved filter（F11b）。
 *
 * ## ⚠️ 没有列表端点
 *
 * v1 只有 `GET/POST/DELETE /filters/{id}` 与 `PUT /filters`，
 * **没有 `GET /filters`**。侧栏里的清单来自 `GET /projects` —— saved filter
 * 以**负 ID 伪项目**的形式混在项目列表里返回（`SavedFilter.ToProject()`）。
 * 所以"列出我的过滤器"是从项目列表里筛负 ID，见 `features/filters/pseudo-project.ts`。
 *
 * ## ⚠️ 伪项目**不参与项目列表的分页计数**
 *
 * `getAllRawProjects` 先按 page/perPage 取真实项目，**之后**才把 saved filter
 * 追加进结果（project.go:292-299），而 `resultCount` / `totalItems` 来自前一步。
 * 于是 `items.length` 可能**大于** `resultCount`，且伪项目会出现在**每一页**上。
 * 前端因此不能用 `resultCount` 去校验 items 长度，也不能靠翻页去凑齐过滤器。
 */

import type { ListTasksParams } from './tasks';

export interface SavedFilterOwner {
	id: number;
	username?: string;
	name?: string;
}

export interface SavedFilter {
	id: number;
	title: string;
	description?: string;
	/** 包裹的查询条件。`filter` 是 DSL 字符串，与 F11a 的输入同源。 */
	filters?: { filter?: string } & Partial<ListTasksParams>;
	is_favorite?: boolean;
	owner?: SavedFilterOwner | null;
	created?: string;
	updated?: string;
}

/**
 * 写请求体。
 *
 * ⚠️ `title` 与 `filters` 都是后端必填（`valid:"required"`，saved_filters.go:37-39），
 * 空标题会 412。`filters` 少了会被当成"没有条件"，那不是一个有意义的过滤器。
 */
export interface SavedFilterWritePayload {
	title: string;
	description?: string;
	filters: { filter: string };
}

export function getSavedFilter(filterId: number, client: CaltonClient = apiClient) {
	return client.get<SavedFilter>(`/filters/${filterId}`);
}

/** ⚠️ v1 里 PUT 才是新建。 */
export function createSavedFilter(
	payload: SavedFilterWritePayload,
	client: CaltonClient = apiClient,
) {
	return client.put<SavedFilter>('/filters', payload);
}

/** ⚠️ v1 里 POST 是更新。 */
export function updateSavedFilter(
	filterId: number,
	payload: SavedFilterWritePayload,
	client: CaltonClient = apiClient,
) {
	return client.post<SavedFilter>(`/filters/${filterId}`, payload);
}

export function deleteSavedFilter(filterId: number, client: CaltonClient = apiClient) {
	return client.delete(`/filters/${filterId}`);
}
