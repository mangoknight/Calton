import { ContractViolationError } from './errors';

/**
 * 分页头解析（终稿 §1.6）：
 *   x-pagination-result-count  本次返回条数
 *   x-pagination-total-pages   总页数（result_count == 0 时后端强制为 0）
 *
 * 头缺失时**必须报错**：浏览器读不到头的典型原因是后端漏了
 * Access-Control-Expose-Headers，静默算出 NaN 会一路漏到分页控件上，
 * 表现成"翻页没反应"，极难回溯。
 */

export interface Paginated<T> {
	items: T[];
	resultCount: number;
	totalPages: number;
}

export const RESULT_COUNT_HEADER = 'x-pagination-result-count';
export const TOTAL_PAGES_HEADER = 'x-pagination-total-pages';
export const MAX_PERMISSION_HEADER = 'x-max-permission';

function requireIntHeader(headers: Headers, name: string): number {
	const raw = headers.get(name);
	if (raw === null) {
		throw new ContractViolationError(
			`响应缺少 ${name} 头。若后端确实返回了，检查它有没有把该头列进 Access-Control-Expose-Headers —— 否则浏览器读不到。`,
		);
	}
	const value = Number(raw);
	if (!Number.isInteger(value) || value < 0) {
		throw new ContractViolationError(`${name} 不是非负整数：${JSON.stringify(raw)}`);
	}
	return value;
}

export function parsePagination(headers: Headers): Omit<Paginated<never>, 'items'> {
	return {
		resultCount: requireIntHeader(headers, RESULT_COUNT_HEADER),
		totalPages: requireIntHeader(headers, TOTAL_PAGES_HEADER),
	};
}

/**
 * 豁免端点的分页值：后端一个头都不发，只能按拿到的数组推。
 *
 * `totalPages` 空时取 0 而非 1，是跟着 v1 分页端点的口径走
 * （`result_count == 0` 时后端强制 `total_pages = 0`），
 * 免得分页控件对两类端点表现不一致。
 */
export function derivePagination(itemCount: number): Omit<Paginated<never>, 'items'> {
	return { resultCount: itemCount, totalPages: itemCount > 0 ? 1 : 0 };
}

/**
 * ReadOne 才有的 x-max-permission（Read=0 / Write=1 / Admin=2）。
 * 它是可选的：不是所有单体端点都返回，缺失返回 null，不报错。
 */
export function parseMaxPermission(headers: Headers): number | null {
	const raw = headers.get(MAX_PERMISSION_HEADER);
	if (raw === null) return null;
	const value = Number(raw);
	return Number.isInteger(value) ? value : null;
}
