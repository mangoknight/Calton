/**
 * saved filter ↔ 伪项目 ID 的换算（F11b）。
 *
 * ## 为什么会有这层换算
 *
 * v1 **没有 `GET /filters` 列表端点**。saved filter 是通过
 * `GET /projects` 以**负 ID 伪项目**的形式返回的（`SavedFilter.ToProject()`，
 * saved_filters.go:105-116），任务查询也走 `/projects/{负数}/...` 这条路径。
 * 所以前端必须在"给人看的 filter id"和"给接口用的 project id"之间来回换。
 *
 * ## 换算式（saved_filters.go:72-89）
 *
 * ```
 * projectID = -filterID - 1     filter 1 → -2, filter 2 → -3, …
 * filterID  = -projectID - 1
 * ```
 *
 * ## ⚠️ `-1` 是**收藏夹**，不是 saved filter
 *
 * 判据是 `projectID < -1`（**严格小于**，task_collection.go:268）。
 * 写成 `<= -1` 会把收藏夹当成 saved filter，去查一个 id 为 0 的过滤器 ——
 * 后端把 filterID 0 视为无效。这就是当初 T12 卡上标的"差一个等号就把收藏夹
 * 当过滤器"，两侧都容易写错，所以这里只暴露带判据的函数，不暴露裸算术。
 */

/** 收藏夹伪项目的 ID。它不是 saved filter。 */
export const FAVORITES_PROJECT_ID = -1;

/** 这个（可能为负的）项目 ID 是不是 saved filter 的投影。 */
export function isSavedFilterProjectId(projectId: number): boolean {
	// ⚠️ 严格小于 -1：-1 是收藏夹
	return Number.isInteger(projectId) && projectId < FAVORITES_PROJECT_ID;
}

export function isFavoritesProjectId(projectId: number): boolean {
	return projectId === FAVORITES_PROJECT_ID;
}

/**
 * 伪项目 ID → saved filter ID。不是 saved filter 时返回 null，
 * **不要**返回 0 或负数让调用方自己判 —— 那正是收藏夹被当成过滤器的入口。
 */
export function savedFilterIdFromProjectId(projectId: number): number | null {
	if (!isSavedFilterProjectId(projectId)) return null;
	return -projectId - 1;
}

/**
 * saved filter ID → 伪项目 ID。filter id 必须是正整数
 * （后端 filterID 0 无效，见文件头）。
 */
export function projectIdFromSavedFilterId(filterId: number): number | null {
	if (!Number.isInteger(filterId) || filterId < 1) return null;
	return -filterId - 1;
}

/**
 * 路由段里的 saved filter id。
 *
 * ⚠️ 这里**不复用 `parseRouteId`**，也不去放宽它。
 *
 * `parseRouteId` 只收正整数，是 `/projects/:projectId/:view` 的守卫 ——
 * 放宽它去容纳负数伪项目，等于把 `/projects/new/list` 那道防线一起拆了。
 * 正确做法是**URL 里放正的 filter id**（`/filters/2`，可读、与 API 的 id 一致），
 * 只在调接口时才换成伪项目 ID。换算发生在边界上，而不是让负数在路由里流通。
 */
export function parseFilterRouteId(raw: string | undefined): number | null {
	if (!raw || !/^\d+$/.test(raw)) return null;
	const id = Number(raw);
	return id >= 1 ? id : null;
}
