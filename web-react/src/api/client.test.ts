import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { buildQuery, CaltonClient } from './client';
import { ContractViolationError, CaltonError } from './errors';
import { createTokenStore } from './token-store';
import { server } from '@/test/msw';

const BASE = 'http://api.test/api/v1';

function makeClient(overrides: { onUnauthenticated?: (e: CaltonError) => void } = {}) {
	const tokens = createTokenStore(null);
	const client = new CaltonClient({ baseUrl: BASE, tokenStore: tokens, ...overrides });
	return { client, tokens };
}

/** 带全套分页头的列表响应。 */
function listResponse(items: unknown[], resultCount = items.length, totalPages = 1) {
	return HttpResponse.json(items, {
		headers: {
			'x-pagination-result-count': String(resultCount),
			'x-pagination-total-pages': String(totalPages),
			'access-control-expose-headers': 'x-pagination-result-count, x-pagination-total-pages',
		},
	});
}

/** 断言"必须抛错"并拿到类型化的错误 —— 请求意外成功时立刻红，而不是拿到 undefined 继续比。 */
async function captureError<E = CaltonError>(promise: Promise<unknown>): Promise<E> {
	try {
		await promise;
	} catch (error) {
		return error as E;
	}
	throw new Error('期望请求抛错，但它成功了');
}

describe('buildQuery', () => {
	it('数组序列化成重复参数并保持顺序（sort_by/order_by 按下标成对）', () => {
		const qs = buildQuery({ sort_by: ['due_date', 'title'], order_by: ['asc', 'desc'] });
		expect(qs).toBe('?sort_by=due_date&sort_by=title&order_by=asc&order_by=desc');
	});

	it('丢掉 null/undefined，但保留 0 与 false', () => {
		expect(buildQuery({ a: null, b: undefined, page: 0, done: false })).toBe('?page=0&done=false');
	});

	it('无参数时不产生问号', () => {
		expect(buildQuery(undefined)).toBe('');
		expect(buildQuery({})).toBe('');
	});
});

describe('Bearer 注入', () => {
	it('有 token 时带上，anonymous 请求不带', async () => {
		const seen: (string | null)[] = [];
		server.use(
			http.get(`${BASE}/user`, ({ request }) => {
				seen.push(request.headers.get('authorization'));
				return HttpResponse.json({ id: 1 });
			}),
		);

		const { client, tokens } = makeClient();
		tokens.set('jwt-1');
		await client.get('/user');
		await client.get('/user', { anonymous: true });

		expect(seen).toEqual(['Bearer jwt-1', null]);
	});
});

describe('fetch 的取用时机', () => {
	it('每次请求现取 globalThis.fetch，不在构造时抓死', async () => {
		// 回归用例：单例 client 在模块加载时就建好了，那时 MSW（开发期 mock、单测拦截）
		// 还没打补丁。构造时 bind 一次的话，之后所有请求都绕过拦截，
		// 表现是相对路径 URL 直接抛 "Failed to parse URL from /api/v1/..."。
		const client = new CaltonClient({ baseUrl: BASE });

		const stub = vi.fn(async () => new Response('{"ok":true}', { status: 200 }));
		vi.stubGlobal('fetch', stub);

		await expect(client.get('/late-bound')).resolves.toEqual({ ok: true });
		expect(stub).toHaveBeenCalledTimes(1);

		vi.unstubAllGlobals();
	});
});

describe('分页头解析', () => {
	it('解析成 {items, resultCount, totalPages}', async () => {
		server.use(http.get(`${BASE}/tasks`, () => listResponse([{ id: 1 }, { id: 2 }], 2, 7)));

		const { client } = makeClient();
		await expect(client.requestList('GET', '/tasks')).resolves.toEqual({
			items: [{ id: 1 }, { id: 2 }],
			resultCount: 2,
			totalPages: 7,
		});
	});

	it('空结果是 [] 且 total_pages 为 0', async () => {
		server.use(http.get(`${BASE}/tasks`, () => listResponse([], 0, 0)));

		const { client } = makeClient();
		await expect(client.requestList('GET', '/tasks')).resolves.toEqual({
			items: [],
			resultCount: 0,
			totalPages: 0,
		});
	});

	it.each([
		['x-pagination-result-count', { 'x-pagination-total-pages': '3' }],
		['x-pagination-total-pages', { 'x-pagination-result-count': '3' }],
	])('缺 %s 时抛 ContractViolationError，而不是静默 NaN', async (missing, headers) => {
		server.use(http.get(`${BASE}/tasks`, () => HttpResponse.json([], { headers })));

		const { client } = makeClient();
		const error = await captureError<ContractViolationError>(client.requestList('GET', '/tasks'));

		expect(error).toBeInstanceOf(ContractViolationError);
		expect(error.message).toContain(missing);
		// 报错要指向真正的成因，否则排查会往后端逻辑上跑偏
		expect(error.message).toContain('Access-Control-Expose-Headers');
	});

	it('分页头不是数字时同样报错', async () => {
		server.use(
			http.get(`${BASE}/tasks`, () =>
				HttpResponse.json([], {
					headers: { 'x-pagination-result-count': 'many', 'x-pagination-total-pages': '1' },
				}),
			),
		);

		const { client } = makeClient();
		await expect(client.requestList('GET', '/tasks')).rejects.toBeInstanceOf(
			ContractViolationError,
		);
	});
});

/**
 * 豁免名单（名单本身与 Go 路由表的对账见 `unpaginated-endpoints.test.ts`）。
 *
 * 起因：tester 实测 `GET /users` 走自定义 handler，**一个分页头都不发，空结果还返回
 * `null` 不是 `[]`**。F08c 的指派选择器只要调一次 `requestList` 就抛
 * ContractViolationError——而且报的是一个看起来像后端问题的"契约违规"。
 * 这个洞不能在后端补（补了与 Go 分叉、对拍立刻红），只能前端认下来。
 */
describe('无分页头端点豁免', () => {
	/** Go: `c.JSON(http.StatusOK, users)`——没有任何分页头。 */
	function bareArrayResponse(items: unknown[] | null) {
		return HttpResponse.json(items);
	}

	it('★ GET /users 不发分页头也不抛错，条数由 items 推出来', async () => {
		server.use(http.get(`${BASE}/users`, () => bareArrayResponse([{ id: 1 }, { id: 2 }])));

		const { client } = makeClient();
		await expect(client.requestList('GET', '/users')).resolves.toEqual({
			items: [{ id: 1 }, { id: 2 }],
			resultCount: 2,
			totalPages: 1,
		});
	});

	/**
	 * ★ 空搜索时 Go 的 ListUsers 是个裸 `return`，nil slice 序列化成 `null`。
	 * 这不是"网关改写"，是这个端点真实的后端契约。
	 */
	it('★ GET /users 空结果返回 null，归一成 [] 且 totalPages 为 0', async () => {
		server.use(http.get(`${BASE}/users`, () => bareArrayResponse(null)));

		const { client } = makeClient();
		await expect(client.requestList('GET', '/users')).resolves.toEqual({
			items: [],
			resultCount: 0,
			totalPages: 0,
		});
	});

	it('/projects/{project}/projectusers 同样豁免', async () => {
		server.use(http.get(`${BASE}/projects/7/projectusers`, () => bareArrayResponse([{ id: 3 }])));

		const { client } = makeClient();
		await expect(client.requestList('GET', '/projects/7/projectusers')).resolves.toEqual({
			items: [{ id: 3 }],
			resultCount: 1,
			totalPages: 1,
		});
	});

	/**
	 * 豁免是按端点给的，不是全局关掉校验。同一个 client 上的分页端点必须照旧报错，
	 * 否则"漏了 Access-Control-Expose-Headers"这类真问题会被一起放过。
	 */
	it('★ 豁免不外溢：同一 client 上分页端点缺头照旧抛 ContractViolationError', async () => {
		server.use(
			http.get(`${BASE}/users`, () => bareArrayResponse([])),
			http.get(`${BASE}/tasks`, () => bareArrayResponse([])),
		);

		const { client } = makeClient();
		await expect(client.requestList('GET', '/users')).resolves.toMatchObject({ items: [] });
		await expect(client.requestList('GET', '/tasks')).rejects.toBeInstanceOf(
			ContractViolationError,
		);
	});

	/**
	 * 豁免端点万一真发了分页头，说明我们这边的后端和 Go 分叉了——那是对拍台（AC-1）
	 * 的事，不是前端该救的。前端按名单一律用推算值，行为可预期。
	 */
	it('豁免端点即使发了分页头也走推算值（分叉由对拍台去报，不在这里静默兼容）', async () => {
		server.use(http.get(`${BASE}/users`, () => listResponse([{ id: 1 }], 99, 42)));

		const { client } = makeClient();
		await expect(client.requestList('GET', '/users')).resolves.toEqual({
			items: [{ id: 1 }],
			resultCount: 1,
			totalPages: 1,
		});
	});
});

describe('x-max-permission', () => {
	it('ReadOne 带上时解析出来，缺失时为 null', async () => {
		server.use(
			http.get(`${BASE}/projects/1`, () =>
				HttpResponse.json({ id: 1 }, { headers: { 'x-max-permission': '2' } }),
			),
			http.get(`${BASE}/projects/2`, () => HttpResponse.json({ id: 2 })),
		);

		const { client } = makeClient();
		await expect(client.requestOne('GET', '/projects/1')).resolves.toEqual({
			data: { id: 1 },
			maxPermission: 2,
		});
		await expect(client.requestOne('GET', '/projects/2')).resolves.toEqual({
			data: { id: 2 },
			maxPermission: null,
		});
	});

	it('Read=0 不被当成缺失', async () => {
		server.use(
			http.get(`${BASE}/projects/3`, () =>
				HttpResponse.json({ id: 3 }, { headers: { 'x-max-permission': '0' } }),
			),
		);

		const { client } = makeClient();
		const result = await client.requestOne('GET', '/projects/3');
		expect(result.maxPermission).toBe(0);
	});
});

describe('错误体 → CaltonError', () => {
	it('常规 {code,message}', async () => {
		server.use(
			http.get(`${BASE}/projects/9`, () =>
				HttpResponse.json({ code: 3001, message: 'This project does not exist.' }, { status: 404 }),
			),
		);

		const { client } = makeClient();
		const error = await captureError(client.get('/projects/9'));

		expect(error).toBeInstanceOf(CaltonError);
		expect(error.status).toBe(404);
		expect(error.code).toBe(3001);
		expect(error.message).toBe('This project does not exist.');
	});

	it('带 i18n_params 时保留，供 F13 插值', async () => {
		server.use(
			http.get(`${BASE}/x`, () =>
				HttpResponse.json(
					{
						code: 2003,
						message: "The timezone 'Foo/Bar' is invalid",
						i18n_params: { timezone: 'Foo/Bar' },
					},
					{ status: 400 },
				),
			),
		);

		const { client } = makeClient();
		const error = await captureError(client.get('/x'));
		expect(error.i18nParams).toEqual({ timezone: 'Foo/Bar' });
	});

	it('ValidationHTTPError 的 invalid_fields 保留', async () => {
		server.use(
			http.get(`${BASE}/x`, () =>
				HttpResponse.json(
					{ code: 2002, message: 'Invalid Data', invalid_fields: ['expand'] },
					{ status: 412 },
				),
			),
		);

		const { client } = makeClient();
		const error = await captureError(client.get('/x'));
		expect(error.invalidFields).toEqual(['expand']);
	});

	it('只有 message 没有 code 时，code 保持 undefined —— 不给它补一个', async () => {
		server.use(
			http.get(`${BASE}/x`, () =>
				HttpResponse.json({ message: 'some echo error' }, { status: 400 }),
			),
		);

		const { client } = makeClient();
		const error = await captureError(client.get('/x'));
		expect(error.code).toBeUndefined();
		expect(error.message).toBe('some echo error');
	});

	it('非 JSON 响应体也能拿到可读 message', async () => {
		server.use(
			http.get(
				`${BASE}/x`,
				() => new HttpResponse('<html>502 Bad Gateway</html>', { status: 502 }),
			),
		);

		const { client } = makeClient();
		const error = await captureError(client.get('/x'));
		expect(error).toBeInstanceOf(CaltonError);
		expect(error.status).toBe(502);
		expect(error.message).toContain('502 Bad Gateway');
	});
});

describe('401 刷新', () => {
	beforeEach(() => vi.restoreAllMocks());

	it('刷新成功后重试原请求，调用方无感', async () => {
		let refreshCalls = 0;
		let userCalls = 0;
		server.use(
			http.post(`${BASE}/user/token/refresh`, () => {
				refreshCalls += 1;
				return HttpResponse.json({ token: 'jwt-new' });
			}),
			http.get(`${BASE}/user`, ({ request }) => {
				userCalls += 1;
				return request.headers.get('authorization') === 'Bearer jwt-new'
					? HttpResponse.json({ id: 1, username: 'me' })
					: HttpResponse.json({ code: 11, message: 'invalid token' }, { status: 401 });
			}),
		);

		const { client, tokens } = makeClient();
		tokens.set('jwt-old');

		await expect(client.get('/user')).resolves.toEqual({ id: 1, username: 'me' });
		expect(refreshCalls).toBe(1);
		expect(userCalls).toBe(2);
		expect(tokens.get()).toBe('jwt-new');
	});

	it('★ 重试后仍 401 → 直接登出，不再刷新第二次（防无限循环）', async () => {
		let refreshCalls = 0;
		let userCalls = 0;
		server.use(
			http.post(`${BASE}/user/token/refresh`, () => {
				refreshCalls += 1;
				return HttpResponse.json({ token: 'jwt-new' });
			}),
			http.get(`${BASE}/user`, () => {
				userCalls += 1;
				return HttpResponse.json({ code: 11, message: 'invalid token' }, { status: 401 });
			}),
		);

		const onUnauthenticated = vi.fn();
		const { client, tokens } = makeClient({ onUnauthenticated });
		tokens.set('jwt-old');

		const error = await captureError(client.get('/user'));

		expect(error).toBeInstanceOf(CaltonError);
		expect(error.status).toBe(401);
		expect(error.code).toBe(11);
		expect(refreshCalls).toBe(1); // 关键断言：只刷新过一次
		expect(userCalls).toBe(2); // 原请求 + 一次重试，没有第三次
		expect(tokens.get()).toBeNull();
		expect(onUnauthenticated).toHaveBeenCalledTimes(1);
	});

	it('刷新本身 401 时登出，且不会递归刷新', async () => {
		let refreshCalls = 0;
		server.use(
			http.post(`${BASE}/user/token/refresh`, () => {
				refreshCalls += 1;
				return HttpResponse.json({ code: 11, message: 'invalid token' }, { status: 401 });
			}),
			http.get(`${BASE}/user`, () =>
				HttpResponse.json({ code: 11, message: 'invalid token' }, { status: 401 }),
			),
		);

		const onUnauthenticated = vi.fn();
		const { client, tokens } = makeClient({ onUnauthenticated });
		tokens.set('jwt-old');

		const error = await captureError(client.get('/user'));

		expect(error.status).toBe(401);
		expect(refreshCalls).toBe(1);
		expect(tokens.get()).toBeNull();
		expect(onUnauthenticated).toHaveBeenCalledTimes(1);
	});

	it('刷新返回 200 但没有 token 字段，也算失败', async () => {
		server.use(
			http.post(`${BASE}/user/token/refresh`, () => HttpResponse.json({})),
			http.get(`${BASE}/user`, () =>
				HttpResponse.json({ code: 11, message: 'invalid token' }, { status: 401 }),
			),
		);

		const { client, tokens } = makeClient();
		tokens.set('jwt-old');

		await expect(client.get('/user')).rejects.toBeInstanceOf(CaltonError);
		expect(tokens.get()).toBeNull();
	});

	it('并发 401 只触发一次刷新（单飞）', async () => {
		let refreshCalls = 0;
		server.use(
			http.post(`${BASE}/user/token/refresh`, async () => {
				refreshCalls += 1;
				await new Promise((r) => setTimeout(r, 10));
				return HttpResponse.json({ token: 'jwt-new' });
			}),
			http.get(`${BASE}/tasks`, ({ request }) =>
				request.headers.get('authorization') === 'Bearer jwt-new'
					? listResponse([{ id: 1 }])
					: HttpResponse.json({ code: 11, message: 'invalid token' }, { status: 401 }),
			),
		);

		const { client, tokens } = makeClient();
		tokens.set('jwt-old');

		const results = await Promise.all([
			client.requestList('GET', '/tasks'),
			client.requestList('GET', '/tasks'),
			client.requestList('GET', '/tasks'),
		]);

		expect(refreshCalls).toBe(1);
		results.forEach((r) => expect(r.items).toEqual([{ id: 1 }]));
	});

	it('没有 token 时的 401 不触发刷新（未登录访问受限端点）', async () => {
		let refreshCalls = 0;
		server.use(
			http.post(`${BASE}/user/token/refresh`, () => {
				refreshCalls += 1;
				return HttpResponse.json({ token: 'jwt-new' });
			}),
			http.get(`${BASE}/user`, () =>
				HttpResponse.json({ code: 11, message: 'invalid token' }, { status: 401 }),
			),
		);

		const { client } = makeClient();
		await expect(client.get('/user')).rejects.toBeInstanceOf(CaltonError);
		expect(refreshCalls).toBe(0);
	});

	it('登录请求（anonymous）的 401 是密码错，不刷新不登出', async () => {
		let refreshCalls = 0;
		server.use(
			http.post(`${BASE}/user/token/refresh`, () => {
				refreshCalls += 1;
				return HttpResponse.json({ token: 'jwt-new' });
			}),
			http.post(`${BASE}/login`, () =>
				HttpResponse.json({ code: 1011, message: 'wrong password' }, { status: 401 }),
			),
		);

		const onUnauthenticated = vi.fn();
		const { client, tokens } = makeClient({ onUnauthenticated });
		tokens.set('jwt-old');

		await expect(
			client.post('/login', { username: 'a', password: 'b' }, { anonymous: true }),
		).rejects.toBeInstanceOf(CaltonError);

		expect(refreshCalls).toBe(0);
		expect(onUnauthenticated).not.toHaveBeenCalled();
		expect(tokens.get()).toBe('jwt-old');
	});
});

describe('请求体与响应体', () => {
	it('带 body 时发 JSON 并设置 Content-Type', async () => {
		let received: unknown;
		let contentType: string | null = null;
		server.use(
			http.put(`${BASE}/projects`, async ({ request }) => {
				contentType = request.headers.get('content-type');
				received = await request.json();
				return HttpResponse.json({ id: 5 }, { status: 201 });
			}),
		);

		const { client } = makeClient();
		await expect(client.put('/projects', { title: '新项目' })).resolves.toEqual({ id: 5 });
		expect(received).toEqual({ title: '新项目' });
		expect(contentType).toContain('application/json');
	});

	it('204 空响应解析成 undefined 而不是抛 JSON 解析错', async () => {
		server.use(http.delete(`${BASE}/projects/1`, () => new HttpResponse(null, { status: 204 })));

		const { client } = makeClient();
		await expect(client.delete('/projects/1')).resolves.toBeUndefined();
	});
});
