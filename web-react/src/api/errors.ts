/**
 * v1 错误体 → CaltonError（终稿 §1.7）。
 *
 * 三种形状都要吃下：
 *   {code, message, i18n_params?}          常规
 *   {code, message, details}               HTTPErrorWithDetails
 *   {code, message, invalid_fields}        ValidationHTTPError
 * 外加一个必须照抄的不一致：中央 handler 对纯字符串 echo 错误只输出 {"message":"..."}，
 * **没有 code** —— 不要"好心"给它补一个。
 */

export interface CaltonErrorBody {
	code?: number;
	message?: string;
	i18n_params?: Record<string, unknown>;
	details?: unknown;
	invalid_fields?: string[];
}

export class CaltonError extends Error {
	readonly status: number;
	/** 后端错误码；纯字符串 echo 错误没有 code，此时为 undefined。 */
	readonly code?: number;
	readonly i18nParams?: Record<string, unknown>;
	readonly details?: unknown;
	readonly invalidFields?: string[];
	/** 原始响应体，排查用。 */
	readonly body: unknown;

	constructor(status: number, body: unknown, fallbackMessage: string) {
		const parsed = (typeof body === 'object' && body !== null ? body : {}) as CaltonErrorBody;
		super(parsed.message?.trim() || fallbackMessage);
		this.name = 'CaltonError';
		this.status = status;
		this.code = typeof parsed.code === 'number' ? parsed.code : undefined;
		this.i18nParams = parsed.i18n_params;
		this.details = parsed.details;
		this.invalidFields = parsed.invalid_fields;
		this.body = body;
	}

	/**
	 * 是否未认证。判的是 **status === 401**，不是 code。
	 *
	 * 后端约定 401 的 body 固定为 `{"code":11,...}`（漏项 #4），但网关/代理返回的 401
	 * 可能根本没有 body，按 code 判会漏掉那些。用它代替到处手写 `err.status === 401`。
	 */
	get isUnauthenticated() {
		return this.status === 401;
	}
}

/** 前端与后端契约对不上（少响应头、body 不是预期形状）—— 这不是业务错误，别混进 CaltonError。 */
export class ContractViolationError extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'ContractViolationError';
	}
}

export async function toCaltonError(response: Response): Promise<CaltonError> {
	const fallback = `${response.status} ${response.statusText || 'Request failed'}`;
	// HEAD 与部分错误返回空 body，text() 拿到空串
	const text = await response.text().catch(() => '');
	if (!text) return new CaltonError(response.status, null, fallback);

	try {
		return new CaltonError(response.status, JSON.parse(text), fallback);
	} catch {
		// 非 JSON（网关 HTML 错误页之类）：原文进 message，方便定位是谁返回的
		return new CaltonError(response.status, text, text.slice(0, 200) || fallback);
	}
}
