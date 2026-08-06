import { apiClient, type CaltonClient } from './client';
import type { Paginated } from './pagination';

/**
 * 指派（F08c）。
 *
 * ## ⚠️ 写端点收的是**数字 user_id**，不是用户名
 *
 * 这里有一个极易搞混的地方，两个"assignees"说的不是一回事：
 *
 * - **写**（本文件）：`PUT /tasks/{id}/assignees` 的 body 是 `{user_id: 901}`，
 *   `DELETE /tasks/{id}/assignees/{userID}` 的路径段也是数字 id
 *   （`TaskAssginee.UserID` 的 json tag / param，task_assignees.go:35）。
 * - **过滤**（F11a / F12 的 filter DSL）：`filter=assignees = alice` 收的是
 *   **用户名** —— `task_search.go:59-64` 里该字段是 `FilterableField: "username"`，
 *   并 JOIN 了 users 表。传数字 id 会**返回 200 空集且不报错**。
 *
 * 两边搞反的表现都是"没报错但没效果"：写端点传用户名会被当成 0，
 * 过滤传 id 会静默查不到。**选择器属于"写"这一侧，一律用数字 id。**
 */

export interface AssignableUser {
	id: number;
	username?: string;
	name?: string;
}

/**
 * 搜索可指派的用户。
 *
 * ⚠️ 两件事：
 * 1. 这个端点**不发分页头**（自定义 handler），靠 client 的豁免名单放行；
 *    见 `unpaginated-endpoints.ts`。这正是 f19a38d 那条名单要保护的调用点。
 * 2. **搜索词为空时后端返回 `null`**（`ListUsers` 里是个裸 return），
 *    不是"返回所有用户"。所以选择器必须先让用户打字，空串查不出人来。
 */
export function searchUsers(
	search: string,
	client: CaltonClient = apiClient,
): Promise<Paginated<AssignableUser>> {
	return client.requestList<AssignableUser>('GET', '/users', { query: { s: search } });
}

/**
 * 项目内成员。比全局搜索更贴指派场景（只能指派有项目访问权的人）。
 * 同样在豁免名单里（自定义 handler，不发分页头）。
 */
export function listProjectUsers(
	projectId: number,
	search?: string,
	client: CaltonClient = apiClient,
): Promise<Paginated<AssignableUser>> {
	return client.requestList<AssignableUser>('GET', `/projects/${projectId}/projectusers`, {
		query: search ? { s: search } : {},
	});
}

export function listTaskAssignees(
	taskId: number,
	client: CaltonClient = apiClient,
): Promise<Paginated<AssignableUser>> {
	return client.requestList<AssignableUser>('GET', `/tasks/${taskId}/assignees`, {
		query: { per_page: 50 },
	});
}

/** ⚠️ PUT 才是新建；body 的键是 `user_id`，值是**数字 id**。 */
export function assignUser(
	taskId: number,
	userId: number,
	client: CaltonClient = apiClient,
): Promise<unknown> {
	return client.put(`/tasks/${taskId}/assignees`, { user_id: userId });
}

/** 路径段是数字 user id，不是用户名。 */
export function unassignUser(
	taskId: number,
	userId: number,
	client: CaltonClient = apiClient,
): Promise<unknown> {
	return client.delete(`/tasks/${taskId}/assignees/${userId}`);
}
