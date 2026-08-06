import { describe, expect, it } from 'vitest';

import { parseFilterParam, toFilterQuery } from './filter-param';

describe('parseFilterParam', () => {
	it('缺省为空串', () => {
		expect(parseFilterParam(null)).toBe('');
		expect(parseFilterParam(undefined)).toBe('');
	});

	it('★ 不 trim —— 纯空白必须原样传给后端', () => {
		// 后端只对**恰好为空串**短路返回；纯空白会走到 parser 并被判为空表达式（4024）。
		// 前端 trim 掉的话，一个后端会报错的输入就被悄悄变成了"没有筛选"。
		// ☠ 样本必须是"空白但非空"，用 '' 测不出 trim 与不 trim 的差别（那是不动点）。
		expect(parseFilterParam('   ')).toBe('   ');
	});

	it('★ 不规范化内部空白与大小写', () => {
		expect(parseFilterParam('done  =   FALSE')).toBe('done  =   FALSE');
	});
});

describe('toFilterQuery', () => {
	it('空串不产生 filter 键（与后端对空串的短路语义一致）', () => {
		expect(toFilterQuery('')).toEqual({});
	});

	it('★ 非空时原样带上，不做任何转义或改写', () => {
		expect(toFilterQuery("done = false && assignees = 'bob'")).toEqual({
			filter: "done = false && assignees = 'bob'",
		});
	});

	it('★ 纯空白会被发出去（它不是"没有筛选"）', () => {
		// 与 parseFilterParam 那条配对：只有两条都在，"不 trim"这个决定才被完整钉住 ——
		// 光有 parse 那条，实现可以在 toFilterQuery 里 trim 掉，照样绿。
		expect(toFilterQuery('   ')).toEqual({ filter: '   ' });
	});
});
