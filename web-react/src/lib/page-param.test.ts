import { describe, expect, it } from 'vitest';

import { parsePageParam } from './page-param';

describe('parsePageParam', () => {
	it.each(['1', '2', '999'])('%s 原样接受', (raw) => {
		expect(parsePageParam(raw)).toBe(Number(raw));
	});

	/**
	 * 非法值降级到第 1 页而不是抛错：page 来自用户可以随手改的 URL，
	 * 崩溃页比"回到第一页"糟糕得多。降级的前提是**这里挡住**——
	 * 漏过去就是拿 NaN 打接口，后端回 400，UI 上表现成"翻页报错"。
	 */
	it.each(['0', '-1', '1.5', 'abc', '', ' 2', '2 ', '1e3', null, undefined])(
		'%s 降级到第 1 页',
		(raw) => {
			expect(parsePageParam(raw)).toBe(1);
		},
	);

	/**
	 * ★ 超长数字串。`/^\d+$/` 会放行它，但 Number → String 之后变成科学计数法，
	 * 拼进 query 后 Go 的 Atoi 报 invalid syntax → 400，
	 * 正是本模块 docstring 说要防的那个结局。
	 */
	it.each(['9'.repeat(22), '9'.repeat(25), '1'.repeat(40)])(
		'★ 超长数字串 %s 降级到第 1 页',
		(raw) => {
			expect(parsePageParam(raw)).toBe(1);
		},
	);

	/** 界内的大数仍然放行，且不会变成科学计数法。 */
	it('★ 安全整数范围内的大页码仍然接受，且序列化后仍是纯数字', () => {
		const max = String(Number.MAX_SAFE_INTEGER);
		expect(parsePageParam(max)).toBe(Number.MAX_SAFE_INTEGER);
		expect(String(parsePageParam(max))).toMatch(/^\d+$/);
	});

	/** ★ 真正的判据：任何输入的输出拼进 query 都必须是 Go 的 Atoi 能吃的纯数字。 */
	it.each(['1', '999', '9'.repeat(22), '9'.repeat(25), 'abc', String(Number.MAX_SAFE_INTEGER)])(
		'★ 输入 %s 的结果序列化后是纯数字（Atoi 能解析）',
		(raw) => {
			expect(String(parsePageParam(raw))).toMatch(/^\d+$/);
		},
	);
});
