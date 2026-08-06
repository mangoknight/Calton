/**
 * 路由动态段的取值校验。
 *
 * `/projects/:projectId/:view` 这类路由会把任何字符串塞进参数里 ——
 * `/projects/new/list` 会让 projectId 变成 "new"。不校验就会拿 NaN 去打接口。
 */

/** 只接受正整数形式的路由 id，其余（包括 "new"、"1.5"、"0"、负数）一律返回 null。 */
export function parseRouteId(raw: string | undefined): number | null {
	if (!raw || !/^\d+$/.test(raw)) return null;
	const id = Number(raw);
	return id > 0 ? id : null;
}
