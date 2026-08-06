import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import {
	goPathOf,
	matchUnpaginatedEndpoint,
	sampleUrlFor,
	UNPAGINATED_LIST_ENDPOINTS,
} from './unpaginated-endpoints';

/**
 * 豁免名单是一条**削弱契约校验的通道**，所以它需要比普通常量更硬的看守：
 * 每一条都必须能在 Go 的路由表里指认出"这个端点确实不走 WebHandler"。
 *
 * 这里直接读仓库里的 `pkg/routes/routes.go`（它是对拍基准，原地不动），
 * 把名单和真相绑在一起。有人为了绕开一次 ContractViolationError
 * 往名单里塞一个真·分页端点时，"不得是 ReadAllWeb" 那条会红。
 */
// vitest 里 import.meta.url 不是 file: 协议，只能从 cwd（= web-react/）往上走一级到仓库根。
const ROUTES_GO = resolve(process.cwd(), '..', 'pkg/routes/routes.go');

describe('豁免名单与 Go 路由表对账', () => {
	it('能读到对拍基准的路由表（读不到则后面的对账全是假绿）', () => {
		expect(existsSync(ROUTES_GO)).toBe(true);
	});

	const source = existsSync(ROUTES_GO) ? readFileSync(ROUTES_GO, 'utf8') : '';
	const lines = source.split('\n').map((line) => line.trim());

	it.each(UNPAGINATED_LIST_ENDPOINTS.map((entry) => [entry.name, entry] as const))(
		'%s 的注册行在 routes.go 里存在',
		(_name, entry) => {
			expect(lines).toContain(entry.goRegistration);
		},
	);

	it.each(UNPAGINATED_LIST_ENDPOINTS.map((entry) => [entry.name, entry] as const))(
		'%s 确实不走 WebHandler（所以确实不发分页头）',
		(_name, entry) => {
			expect(entry.goRegistration).not.toContain('ReadAllWeb');
		},
	);

	/**
	 * ★★ 把 `pattern` 和 `goRegistration` 绑死在同一个端点上。
	 *
	 * 这条是本文件的承重墙。上面两条各自都承重，但它们只能证明
	 * "这条注册行存在、且不是 ReadAllWeb"，**证明不了这条注册行就是这个 pattern
	 * 对应的那个端点**。缺了本条，名单可以被走私：
	 * 给真·分页端点（如 `/projects/{p}/views`，routes.go:924，确实是 ReadAllWeb、
	 * 确实发分页头）配上 `a.GET("/routes", …)` 这条合法的非-ReadAllWeb 注册行，
	 * 上面两条双双为真、测试全绿，而该端点从此缺分页头也被静默接受 ——
	 * 正是本模块存在的理由被完整绕过。reviewer 实跑复现过（56→58 passed，零红）。
	 */
	it.each(UNPAGINATED_LIST_ENDPOINTS.map((entry) => [entry.name, entry] as const))(
		'★★ %s 的 pattern 必须匹配它自己那条注册行还原出的路径',
		(_name, entry) => {
			const goPath = goPathOf(entry);
			expect(goPath).not.toBeNull();
			// :param 段换成具体数字后，pattern 必须认得它
			expect(entry.pattern.test(sampleUrlFor(goPath!))).toBe(true);
		},
	);

	/** group 前缀不能是随手编的：它必须真的在 routes.go 里被建出来。 */
	it.each(
		[...new Set(UNPAGINATED_LIST_ENDPOINTS.map((entry) => entry.goGroupPrefix))]
			.filter(Boolean)
			.map((prefix) => [prefix] as const),
	)('group 前缀 %s 在 routes.go 里确有其事', (prefix) => {
		expect(source).toMatch(new RegExp(`\\w+ := \\w+\\.Group\\("${prefix}"\\)`));
	});

	/**
	 * 反向的牙齿：真·分页端点必须**不**被名单命中。
	 * 有了上面的绑定断言之后，这里不必再穷举 —— 但留着便宜，
	 * 且它读起来直接说明了"什么东西不该进名单"。
	 * `/projects/12/views` 与 `/notifications` 是走私实证里用过的两个，特意列上。
	 */
	it.each([
		'/projects',
		'/labels',
		'/tasks',
		'/projects/12/views/3/tasks',
		'/teams',
		'/projects/12/views',
		'/notifications',
	])('%s 是分页端点，不得被豁免', (path) => {
		expect(matchUnpaginatedEndpoint(path)).toBeNull();
	});

	it('控制样本：/projects 与 /labels 在 Go 里确实是 ReadAllWeb（证明上一条的对照有意义）', () => {
		expect(lines).toContain('a.GET("/projects", projectHandler.ReadAllWeb)');
		expect(lines).toContain('a.GET("/labels", labelHandler.ReadAllWeb)');
	});
});

describe('matchUnpaginatedEndpoint', () => {
	it.each([
		['/users', 'GET /users'],
		['/projects/12/projectusers', 'GET /projects/{project}/projectusers'],
		['/routes', 'GET /routes'],
		['/webhooks/events', 'GET /webhooks/events'],
		['/user/timezones', 'GET /user/timezones'],
		['/user/settings/webhooks', 'GET /user/settings/webhooks'],
		['/user/settings/webhooks/events', 'GET /user/settings/webhooks/events'],
		['/user/settings/token/caldav', 'GET /user/settings/token/caldav'],
	])('%s 命中 %s', (path, name) => {
		expect(matchUnpaginatedEndpoint(path)?.name).toBe(name);
	});

	/**
	 * ★ 两个 webhook 事件端点长得像但不是一回事：
	 * `/webhooks/events` 是项目级的可用事件表，`/user/settings/webhooks/events`
	 * 是用户定向的。把它们当成一个会让其中一个漏出名单。
	 */
	it('★ 两个 webhook events 端点各自独立命中，不互相顶替', () => {
		expect(matchUnpaginatedEndpoint('/webhooks/events')?.name).toBe('GET /webhooks/events');
		expect(matchUnpaginatedEndpoint('/user/settings/webhooks/events')?.name).toBe(
			'GET /user/settings/webhooks/events',
		);
	});

	it('带 query 时仍然命中（名单不该因为调用方手写 query 而被绕过）', () => {
		expect(matchUnpaginatedEndpoint('/users?s=ann')?.name).toBe('GET /users');
	});

	it('前缀相同但不是同一个端点的不命中', () => {
		expect(matchUnpaginatedEndpoint('/users/12/settings')).toBeNull();
		expect(matchUnpaginatedEndpoint('/projects/12/users')).toBeNull();
		expect(matchUnpaginatedEndpoint('/webhooks')).toBeNull();
	});

	/** 伪项目 id 是负数（T12），`/projects/-2/projectusers` 也得认。 */
	it('伪项目 id（负数）也命中 projectusers', () => {
		expect(matchUnpaginatedEndpoint('/projects/-2/projectusers')?.name).toBe(
			'GET /projects/{project}/projectusers',
		);
	});
});
