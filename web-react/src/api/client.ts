import { toCaltonError, CaltonError } from './errors';
import {
	derivePagination,
	parseMaxPermission,
	parsePagination,
	type Paginated,
} from './pagination';
import { tokenStore as defaultTokenStore, type TokenStore } from './token-store';
import { matchUnpaginatedEndpoint } from './unpaginated-endpoints';

/**
 * v1 语义翻译层（终稿 §4）。所有请求都从这里走，页面不要直接 fetch。
 *
 * 它负责：Bearer 注入、401 刷新一次后重试、分页头解析、错误体 → CaltonError。
 * 它不负责：字段名转换 —— API 是 snake_case，前端直吃，不做 camelCase 转换层。
 */

export const DEFAULT_BASE_URL = '/api/v1';
export const REFRESH_PATH = '/user/token/refresh';

export type QueryValue = string | number | boolean | null | undefined;
export type Query = Record<string, QueryValue | QueryValue[]>;

export interface RequestOptions {
	query?: Query;
	body?: unknown;
	headers?: Record<string, string>;
	signal?: AbortSignal;
	/** 该请求不带 Bearer（登录/注册用）。 */
	anonymous?: boolean;
}

export interface ClientOptions {
	baseUrl?: string;
	tokenStore?: TokenStore;
	fetchImpl?: typeof fetch;
	/** 刷新失败或二次 401 时回调，用来跳登录页。 */
	onUnauthenticated?: (error: CaltonError) => void;
}

/** 数组值序列化成重复参数并保持顺序 —— sort_by/order_by 是按下标成对的，顺序错了排序就错了。 */
export function buildQuery(query: Query | undefined): string {
	if (!query) return '';
	const params = new URLSearchParams();
	for (const [key, value] of Object.entries(query)) {
		const values = Array.isArray(value) ? value : [value];
		for (const v of values) {
			if (v === null || v === undefined) continue;
			params.append(key, String(v));
		}
	}
	const qs = params.toString();
	return qs ? `?${qs}` : '';
}

export class CaltonClient {
	private readonly baseUrl: string;
	/** 不在构造时抓 globalThis.fetch —— 单例在模块加载时就建好了，那时 MSW 还没打补丁，
	 *  抓早了就会一直用没被拦截的原生 fetch。每次请求现取。 */
	private readonly fetchImpl?: typeof fetch;
	private onUnauthenticated?: (error: CaltonError) => void;
	readonly tokens: TokenStore;

	/** 并发请求同时 401 时只刷新一次，其余等这一次的结果。 */
	private refreshInFlight: Promise<string | null> | null = null;

	constructor(options: ClientOptions = {}) {
		this.baseUrl = options.baseUrl ?? DEFAULT_BASE_URL;
		this.tokens = options.tokenStore ?? defaultTokenStore;
		this.fetchImpl = options.fetchImpl;
		this.onUnauthenticated = options.onUnauthenticated;
	}

	/** 单例 client 建好时还没有 router，跳登录页的动作只能事后注入。 */
	setOnUnauthenticated(handler: (error: CaltonError) => void) {
		this.onUnauthenticated = handler;
	}

	/** 返回解析后的 body。 */
	async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
		const { data } = await this.requestRaw<T>(method, path, options);
		return data;
	}

	/** 需要读响应头（分页 / x-max-permission）时用这个。 */
	async requestRaw<T>(
		method: string,
		path: string,
		options: RequestOptions = {},
	): Promise<{ data: T; response: Response }> {
		const response = await this.send(method, path, options, true);

		if (!response.ok) {
			throw await this.toError(response);
		}

		return { data: await parseBody<T>(response), response };
	}

	/**
	 * 列表端点：把分页头解析成 {items, resultCount, totalPages}。
	 *
	 * ⚠️ **使用边界**：只有走 ReadAllWeb 的端点才发 `x-pagination-*` 头。
	 * v1 里有一小撮列表端点是自定义 handler，直接 `c.JSON(200, items)`，一个头都不发；
	 * 它们记在 `unpaginated-endpoints.ts` 的豁免名单里，本方法对名单内的路径改用推算值。
	 * 名单外的端点缺头照旧抛 ContractViolationError —— 那条防线不能一起放掉。
	 *
	 * 往名单里加东西前先读那个文件的说明：豁免是削弱契约校验，不是绕开报错的快捷方式。
	 */
	async requestList<T>(
		method: string,
		path: string,
		options: RequestOptions = {},
	): Promise<Paginated<T>> {
		const { data, response } = await this.requestRaw<T[] | null>(method, path, options);
		// data 可能是 null：Go 的自定义 handler 返回 nil slice 时序列化成 `null` 而不是 `[]`
		// （如 GET /users 空搜索时 ListUsers 是个裸 return）。这是这些端点真实的后端契约，
		// 不是中间层改写，所以归一化放在这里而不是当异常报。
		const items = data ?? [];
		const exempt = matchUnpaginatedEndpoint(path);
		return {
			items,
			...(exempt ? derivePagination(items.length) : parsePagination(response.headers)),
		};
	}

	/** ReadOne 端点：连带取出 x-max-permission，UI 用它决定按钮可见性。 */
	async requestOne<T>(
		method: string,
		path: string,
		options: RequestOptions = {},
	): Promise<{ data: T; maxPermission: number | null }> {
		const { data, response } = await this.requestRaw<T>(method, path, options);
		return { data, maxPermission: parseMaxPermission(response.headers) };
	}

	get<T>(path: string, options?: RequestOptions) {
		return this.request<T>('GET', path, options);
	}

	/** ⚠️ v1 里 POST 是**全量替换**更新，不是新建 —— 必须回传完整对象（AC-6）。 */
	post<T>(path: string, body?: unknown, options?: RequestOptions) {
		return this.request<T>('POST', path, { ...options, body });
	}

	/** ⚠️ v1 里 PUT 才是新建。动词与 REST 惯例是反的（终稿 §1.1）。 */
	put<T>(path: string, body?: unknown, options?: RequestOptions) {
		return this.request<T>('PUT', path, { ...options, body });
	}

	delete<T>(path: string, options?: RequestOptions) {
		return this.request<T>('DELETE', path, options);
	}

	private async send(
		method: string,
		path: string,
		options: RequestOptions,
		allowRefresh: boolean,
	): Promise<Response> {
		const response = await this.rawFetch(method, path, options);

		if (response.status !== 401 || !allowRefresh) return response;

		// 匿名请求（登录/注册）的 401 是"密码错了"，刷新没有意义
		if (options.anonymous || this.tokens.get() === null) return response;

		const refreshed = await this.refreshOnce();
		if (refreshed === null) {
			// clone：body 只能读一次，调用方还要拿它构造抛出去的错误
			this.logout(await this.toError(response.clone()));
			return response;
		}

		// 重试一次；这次的 401 直接登出，不再刷新 —— 否则就是无限刷新循环
		const retried = await this.rawFetch(method, path, options);
		if (retried.status === 401) {
			this.logout(await this.toError(retried.clone()));
		}
		return retried;
	}

	private async rawFetch(method: string, path: string, options: RequestOptions) {
		const token = options.anonymous ? null : this.tokens.get();
		const hasBody = options.body !== undefined;

		const doFetch = this.fetchImpl ?? globalThis.fetch;

		return doFetch.call(globalThis, `${this.baseUrl}${path}${buildQuery(options.query)}`, {
			method,
			headers: {
				Accept: 'application/json',
				...(hasBody ? { 'Content-Type': 'application/json' } : {}),
				...(token ? { Authorization: `Bearer ${token}` } : {}),
				...options.headers,
			},
			body: hasBody ? JSON.stringify(options.body) : undefined,
			signal: options.signal,
			credentials: 'include',
		});
	}

	/** 单飞：并发的 401 共用同一次刷新。成功返回新 token，失败返回 null。 */
	private refreshOnce(): Promise<string | null> {
		this.refreshInFlight ??= this.doRefresh().finally(() => {
			this.refreshInFlight = null;
		});
		return this.refreshInFlight;
	}

	private async doRefresh(): Promise<string | null> {
		try {
			// allowRefresh=false：刷新请求自己 401 时绝不能再触发刷新
			const response = await this.send('POST', REFRESH_PATH, {}, false);
			if (!response.ok) return null;

			const body = await parseBody<{ token?: string }>(response);
			const token = body?.token;
			if (typeof token !== 'string' || token === '') return null;

			this.tokens.set(token);
			return token;
		} catch {
			// 网络错误也算刷新失败，交给调用方按原始 401 处理
			return null;
		}
	}

	private logout(error: CaltonError) {
		this.tokens.set(null);
		this.onUnauthenticated?.(error);
	}

	private async toError(response: Response) {
		return toCaltonError(response);
	}
}

async function parseBody<T>(response: Response): Promise<T> {
	if (response.status === 204) return undefined as T;
	const text = await response.text();
	if (!text) return undefined as T;
	return JSON.parse(text) as T;
}

export const apiClient = new CaltonClient();
