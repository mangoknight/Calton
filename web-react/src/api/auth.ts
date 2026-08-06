import { apiClient, type CaltonClient } from './client';

/**
 * 认证端点（终稿 §2.6）。
 *
 * 注意 POST /login 的响应体**只有 {"token": "..."}**，没有用户信息 ——
 * 用户资料要另打 GET /user。别指望登录接口回用户对象。
 */

export interface LoginPayload {
	username: string;
	password: string;
	/** 开了 TOTP 的账号必填；没开时不要传空串，后端会当成错误的验证码。 */
	totp_passcode?: string;
	long_token?: boolean;
}

export interface RegisterPayload {
	username: string;
	email: string;
	password: string;
	language?: string;
}

export interface AuthToken {
	token: string;
}

/** GET /user 的返回。字段是 snake_case，前端直吃。 */
export interface CurrentUser {
	id: number;
	username: string;
	name?: string;
	email?: string;
	is_admin?: boolean;
	created?: string;
	updated?: string;
}

export async function login(payload: LoginPayload, client: CaltonClient = apiClient) {
	const result = await client.post<AuthToken>('/login', payload, { anonymous: true });
	client.tokens.set(result.token);
	return result;
}

export function register(payload: RegisterPayload, client: CaltonClient = apiClient) {
	// 注册不会自动登录 —— 上游返回的是 user 对象而不是 token，注册后仍要走一次 login
	return client.post<CurrentUser>('/register', payload, { anonymous: true });
}

export function getCurrentUser(client: CaltonClient = apiClient) {
	return client.get<CurrentUser>('/user');
}

export async function logout(client: CaltonClient = apiClient) {
	try {
		await client.post('/user/logout');
	} finally {
		// 后端登出失败也要清本地 token，否则用户卡在"看着像登录着但什么都打不开"
		client.tokens.set(null);
	}
}
