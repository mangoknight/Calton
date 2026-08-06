import { http, HttpResponse } from 'msw';

/**
 * 默认 mock：让"已登录"的页面测试不必每个都自己 mock 一遍 GET /user。
 * 用例需要别的行为就 server.use(...) 覆盖。
 * 路径带通配前缀，同时匹配相对路径与任意 origin。
 */

export const currentUserFixture = {
	id: 1,
	username: 'tester',
	name: '测试用户',
	email: 'tester@example.com',
};

export const defaultHandlers = [
	http.get('*/api/v1/user', () => HttpResponse.json(currentUserFixture)),
	http.post('*/api/v1/user/logout', () => new HttpResponse(null, { status: 204 })),
	/**
	 * 空标签列表。存在的理由与上面的 GET /user 一样：`/labels` 是导航项之一，
	 * 任何走到这条路由的测试（如 AppShell 的导航用例）都会真的发这个请求，
	 * 不给默认 handler 就会在无关用例里刷 MSW 未处理请求的报错。
	 *
	 * ⚠️ 必须带分页头 —— `GET /labels` 走通用 ReadAll，是**发**分页头的端点，
	 * 不在 `unpaginated-endpoints.ts` 豁免名单里，缺头会抛 ContractViolationError。
	 * 关心标签数据的用例一律用 `server.use(...)` 覆盖它。
	 */
	http.get('*/api/v1/labels', () =>
		HttpResponse.json([], {
			headers: { 'x-pagination-result-count': '0', 'x-pagination-total-pages': '0' },
		}),
	),
];
