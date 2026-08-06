/**
 * API Token 端点（终稿 §5.1 的"令牌"组）。
 *
 * ⚠️ PUT 创建接口的响应体**包含 `token` 字段（明文）**，列表接口不包含。
 * 明文只在创建时出现一次，之后只能看到 id/title/permissions/expires_at/created。
 *
 * ⚠️ 三条路径全部需要 JWT 认证（/tokens 本身在 JWT_ONLY_PATHS 里），
 * 所以不能匿名调用。创建/删除/列表都走 `apiClient` 自动注入的 Bearer header。
 */

import { apiClient, type CaltonClient } from './client';
import type { Paginated } from './pagination';

/** API 权限映射：group → action[]。如 {"tasks": ["read_all", "create"]} */
export type TokenPermissions = Record<string, string[]>;

/** 列表中的 Token（无明文）。 */
export interface APIToken {
	id: number;
	title: string;
	permissions: TokenPermissions;
	expires_at: string;
	created: string;
	owner_id: number;
}

/** 创建 Token 的请求体。三个字段全部必填。 */
export interface CreateTokenPayload {
	title: string;
	permissions: TokenPermissions;
	expires_at: string;
}

/** 创建成功返回（包含明文 token）。 */
export interface CreatedToken extends APIToken {
	token: string;
}

/**
 * 获取当前用户的所有 API Token。
 *
 * 支持分页（page / per_page）和搜索（s，按 title 模糊匹配）。
 * 走通用 ReadAll 路径，发分页头。
 */
export function getTokens(
	params: { page?: number; per_page?: number; s?: string } = {},
	client: CaltonClient = apiClient,
): Promise<Paginated<APIToken>> {
	return client.requestList<APIToken>('GET', '/tokens', { query: params });
}

/**
 * 创建新的 API Token。
 *
 * ⚠️ 返回体包含 `token` 字段（明文），仅此一次。存储后不可恢复。
 * ⚠️ v1 里 **PUT 才是新建**，不是 POST。
 */
export function createToken(
	payload: CreateTokenPayload,
	client: CaltonClient = apiClient,
): Promise<CreatedToken> {
	return client.put<CreatedToken>('/tokens', payload);
}

/**
 * 删除指定的 API Token。
 *
 * ⚠️ 删除不存在的 token 返回 403/{"code":0,"message":"Forbidden"} 而不是 404。
 * 并且不能删除别人的 token。
 */
export function deleteToken(
	tokenId: number,
	client: CaltonClient = apiClient,
): Promise<{ message: string }> {
	return client.delete<{ message: string }>(`/tokens/${tokenId}`);
}
