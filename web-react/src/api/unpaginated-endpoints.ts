/**
 * 已知**不发分页头**的列表端点豁免名单。
 *
 * ## 为什么需要这个名单
 *
 * v1 的绝大多数列表端点走 `handler.WebHandler.ReadAllWeb`，由它统一发
 * `x-pagination-result-count` / `x-pagination-total-pages`。`pagination.ts` 因此
 * 把"缺头"当契约违规抛出——那通常意味着后端漏了 `Access-Control-Expose-Headers`，
 * 静默算成 NaN 会一路漏到分页控件上，表现成"翻页没反应"。这条防线要留着。
 *
 * 但 v1 里有一小撮列表端点**根本不经 WebHandler**：它们是自定义 handler，
 * 直接 `c.JSON(http.StatusOK, items)` 返回数组，一个分页头都不发。
 * 对它们调 `requestList()` 会抛 `ContractViolationError`——报的是一个
 * 看起来像后端 bug 的错误，而实际上后端完全正确。
 *
 * ## 为什么不在后端补上这些头
 *
 * 本项目的 Python 后端要与 Go 版逐字节对拍（AC-1：status + 归一化 body + 分页头
 * 三者一致）。给这些端点补发分页头 = 与 Go 分叉 = 对拍立刻红。
 * **这个洞只能在前端认下来。**
 *
 * ## 名单怎么维护
 *
 * 每条都记着它在 `pkg/routes/routes.go` 里的注册字面量，
 * `unpaginated-endpoints.test.ts` 拿它回查 Go 源码，断言：
 * ① 该注册行确实存在；② 它确实不是 `*.ReadAllWeb`；
 * ③ **`pattern` 必须能匹配这条注册行还原出来的路径**。
 *
 * 第 ③ 条不是补充，是把前两条**绑到同一个端点上**。只有 ①② 的时候名单是可以走私的：
 * 给真·分页端点配一条不相干但合法的非-ReadAllWeb 注册行（比如拿 `/routes` 那行
 * 去掩护 `/projects/{p}/views`），①② 双双为真，测试全绿，而那个真·分页端点
 * 从此缺分页头也不报错 —— 本模块要防的"静默 NaN → 翻页没反应"被完整放了回来。
 * 这是 reviewer 用真实走私实证过的，不是假想。
 */

export interface UnpaginatedEndpoint {
	/** 匹配 `client` 收到的 path（不含 baseUrl，也不含 query）。 */
	readonly pattern: RegExp;
	/** `pkg/routes/routes.go` 里的注册字面量，是名单的取证依据。 */
	readonly goRegistration: string;
	/**
	 * 该注册行所在 Echo group 的前缀，没有则为空串。
	 *
	 * 为什么要单列：`u.GET("s", …)` 挂在 `u := a.Group("/user")` 下，
	 * 拼起来才是 `/users` —— 光看注册行里的 `"s"` 根本认不出它是哪个端点。
	 * 测试会连这个前缀本身也回查一遍，防止有人随手编一个。
	 */
	readonly goGroupPrefix: string;
	/** 说明性名字，报错与测试里用。 */
	readonly name: string;
}

export const UNPAGINATED_LIST_ENDPOINTS: readonly UnpaginatedEndpoint[] = [
	{
		name: 'GET /users',
		pattern: /^\/users$/,
		goRegistration: 'u.GET("s", apiv1.UserList)',
		goGroupPrefix: '/user',
	},
	{
		name: 'GET /projects/{project}/projectusers',
		pattern: /^\/projects\/-?\d+\/projectusers$/,
		goRegistration: 'a.GET("/projects/:project/projectusers", apiv1.ListUsersForProject)',
		goGroupPrefix: '',
	},
	{
		name: 'GET /routes',
		pattern: /^\/routes$/,
		goRegistration: 'a.GET("/routes", models.GetAvailableAPIRoutesForToken)',
		goGroupPrefix: '',
	},
	{
		// ⚠️ 项目级的 webhook 事件表，与下面那条用户定向的是**两个不同端点**
		name: 'GET /webhooks/events',
		pattern: /^\/webhooks\/events$/,
		goRegistration: 'a.GET("/webhooks/events", apiv1.GetAvailableWebhookEvents)',
		goGroupPrefix: '',
	},
	{
		name: 'GET /user/timezones',
		pattern: /^\/user\/timezones$/,
		goRegistration: 'u.GET("/timezones", apiv1.GetAvailableTimezones)',
		goGroupPrefix: '/user',
	},
	{
		name: 'GET /user/settings/webhooks',
		pattern: /^\/user\/settings\/webhooks$/,
		goRegistration: 'u.GET("/settings/webhooks", apiv1.GetUserWebhooks)',
		goGroupPrefix: '/user',
	},
	{
		// ⚠️ 用户定向的事件表，与上面 `/webhooks/events` 不是同一个端点
		name: 'GET /user/settings/webhooks/events',
		pattern: /^\/user\/settings\/webhooks\/events$/,
		goRegistration: 'u.GET("/settings/webhooks/events", apiv1.GetUserDirectedWebhookEvents)',
		goGroupPrefix: '/user',
	},
	{
		name: 'GET /user/settings/token/caldav',
		pattern: /^\/user\/settings\/token\/caldav$/,
		goRegistration: 'u.GET("/settings/token/caldav", apiv1.GetCaldavTokens)',
		goGroupPrefix: '/user',
	},
];

/**
 * 从注册行里还原出这个端点的 v1 路径。
 *
 * Echo 的 group 是**直接拼接**的（`a.Group("/user")` + `"s"` = `/users`），
 * 不插斜杠 —— `/users` 这条正是靠这个才成立。
 */
export function goPathOf(entry: UnpaginatedEndpoint): string | null {
	const match = entry.goRegistration.match(/^\w+\.GET\("([^"]*)"/);
	if (!match) return null;
	return entry.goGroupPrefix + match[1];
}

/** 把 Go 的 `:param` 段换成一个具体值，用来检验 pattern 是否真的覆盖这个端点。 */
export function sampleUrlFor(goPath: string, paramValue = '12'): string {
	return goPath.replace(/:[A-Za-z_]\w*/g, paramValue);
}

/**
 * 命中豁免名单则返回该条目，否则 null。
 *
 * 传进来的 path 正常不带 query（`client` 的 query 走 `options.query`），
 * 但调用方手写 `'/users?s=x'` 也不该把名单绕过去，所以这里先截掉。
 */
export function matchUnpaginatedEndpoint(path: string): UnpaginatedEndpoint | null {
	const pathname = path.split(/[?#]/, 1)[0] ?? path;
	return UNPAGINATED_LIST_ENDPOINTS.find((entry) => entry.pattern.test(pathname)) ?? null;
}
