import { apiClient, type CaltonClient } from './client';
import type { Paginated } from './pagination';

/**
 * 项目端点。字段 snake_case 直吃。
 *
 * ⚠️ **`parent_project_id` 的读取口径尚未实测确定**（tester 正在拿 Go 参考服务验）。
 * Go 模型里它是 `*int64`（可空指针，json tag 无 omitempty），所以线上**三种形态都可能出现**：
 * 显式 `0`、`null`、键缺失。类型按最宽的写，判顶层一律用 `!parent_project_id`
 * ——它对 0 / null / undefined 同时成立，无论实测结果是哪种都安全。
 * 千万别写 `=== 0` 或 `=== null`。
 */

export interface Project {
	id: number;
	title: string;
	description?: string;
	identifier?: string;
	hex_color?: string;
	/** 可能是 0 / null / 键缺失，三种都表示"顶层"。见文件头注释。 */
	parent_project_id?: number | null;
	/** 项目所有者。删除会跨所有权边界级联，UI 需要它来警示"要删掉别人的东西"。 */
	owner?: { id: number; username?: string; name?: string };
	is_archived?: boolean;
	is_favorite?: boolean;
	position?: number;
	max_permission?: number;
	created?: string;
	updated?: string;
}

export interface ListProjectsParams {
	page?: number;
	per_page?: number;
	/** 搜索词。 */
	s?: string;
	is_archived?: boolean;
}

export function listProjects(
	params: ListProjectsParams = {},
	client: CaltonClient = apiClient,
): Promise<Paginated<Project>> {
	return client.requestList<Project>('GET', '/projects', { query: { ...params } });
}

export function getProject(id: number, client: CaltonClient = apiClient) {
	return client.requestOne<Project>('GET', `/projects/${id}`);
}
