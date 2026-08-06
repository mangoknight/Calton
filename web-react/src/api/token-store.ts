/**
 * JWT 存放处。
 *
 * access token 存 localStorage（exp 只有 600s，靠 refresh 续）；
 * refresh token 是 httpOnly cookie（calton_refresh_token，path 限定
 * /api/v1/user/token/refresh），前端读不到也不该读 —— 刷新请求带
 * credentials: 'include' 即可。
 */

const STORAGE_KEY = 'calton-token';

export interface TokenStore {
	get(): string | null;
	set(token: string | null): void;
	subscribe(listener: (token: string | null) => void): () => void;
}

export function createTokenStore(storage: Storage | null = safeLocalStorage()): TokenStore {
	let cached: string | null = storage?.getItem(STORAGE_KEY) ?? null;
	const listeners = new Set<(token: string | null) => void>();

	return {
		get: () => cached,
		set(token) {
			cached = token;
			if (token === null) storage?.removeItem(STORAGE_KEY);
			else storage?.setItem(STORAGE_KEY, token);
			listeners.forEach((l) => l(token));
		},
		subscribe(listener) {
			listeners.add(listener);
			return () => listeners.delete(listener);
		},
	};
}

/** Safari 隐私模式 / 无 DOM 环境下 localStorage 会抛，退化成纯内存。 */
function safeLocalStorage(): Storage | null {
	try {
		return typeof localStorage === 'undefined' ? null : localStorage;
	} catch {
		return null;
	}
}

export const tokenStore = createTokenStore();
