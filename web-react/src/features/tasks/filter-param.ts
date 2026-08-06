import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

/**
 * 筛选器走 URL query（`?filter=…`），与分页的 `?page=` 同一套路：
 * 可分享、可收藏、刷新不丢，也让 F11b 的"存为 saved filter"有个现成的来源。
 */

export const FILTER_PARAM = 'filter';

/**
 * ⚠️ **不做 trim，不做任何规范化** —— 原样透传用户输入。
 *
 * 空白串在后端**不是**空 filter：`parse_task_filter` 只对**恰好为空串**短路返回，
 * 纯空白会一路走到 parser 并被判为空表达式（400/4024）。
 * 前端若顺手 trim，就把一个后端会报错的输入悄悄变成了"没有筛选"——
 * 这正是 F10 立下的那条规矩的另一面：**不替后端做决定**，无论方向是拦还是放。
 *
 * 空串返回空串，调用方据此**不发 filter 参数**（与后端对空串的短路语义一致）。
 */
export function parseFilterParam(raw: string | null | undefined): string {
	return raw ?? '';
}

/**
 * 把 filter 转成请求参数。空串 ⇒ 不带这个键（而不是带一个空值）。
 * 后端对空串本来就是短路返回，两者等价；不带键能让 query key 更稳定。
 */
export function toFilterQuery(filter: string): { filter?: string } {
	return filter === '' ? {} : { filter };
}

export function useFilterParam(): {
	filter: string;
	setFilter: (next: string) => void;
} {
	const [searchParams, setSearchParams] = useSearchParams();
	const filter = parseFilterParam(searchParams.get(FILTER_PARAM));

	const setFilter = useCallback(
		(next: string) => {
			const params = new URLSearchParams(searchParams);
			if (next === '') params.delete(FILTER_PARAM);
			else params.set(FILTER_PARAM, next);

			// ⚠️ 换了筛选条件必须回到第 1 页：新的结果集通常更短，
			// 留在第 3 页会得到一个空列表，而它与"筛选结果为空"在界面上无法区分。
			params.delete('page');

			setSearchParams(params);
		},
		[searchParams, setSearchParams],
	);

	return { filter, setFilter };
}
