/**
 * 分页页码放在 URL query（`?page=2`）而不是组件 state：
 * 刷新/前进后退/分享链接都能停在同一页。代价是它和路由参数一样是任意字符串，
 * 必须校验后再用 —— 拿 NaN 去打接口，后端会回 400，UI 上表现成"翻页报错"。
 */

/**
 * 非法/缺失一律降级到第 1 页，不抛错 —— 用户手改 URL 不该看到崩溃页。
 *
 * ⚠️ 上界不是洁癖：`/^\d+$/` 放行任意长的数字串，而 22 位以上的数字经
 * `Number()` 再 `String()` 会变成科学计数法（25 个 9 → `"1e+25"`），
 * 拼进 query 后 Go 的 `Atoi` 直接 invalid syntax → 400。
 * 那正是本模块声称要防的结局，所以超出安全整数范围的一律按非法处理。
 */
export function parsePageParam(raw: string | null | undefined): number {
	if (!raw || !/^\d+$/.test(raw)) return 1;
	const page = Number(raw);
	if (!Number.isSafeInteger(page)) return 1;
	return page >= 1 ? page : 1;
}
