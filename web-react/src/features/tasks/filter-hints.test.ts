import { describe, expect, it } from 'vitest';

import { filterHints } from './filter-hints';

function ids(filter: string): string[] {
	return filterHints(filter).map((hint) => hint.id);
}

/**
 * ⚠️ 这些用例的**反例样本**是承重的，不是凑数。
 *
 * "`assignees like` 会被提示"这条如果只有正例，一个"永远返回这条提示"的实现照样全绿。
 * 所以每条提示都配一个**结构极相近但不该触发**的反例
 * （`assignees = 'bob'` vs `assignees like 'bob'`），
 * 让"提示条件"这个变换在样本上真的有分歧 —— 否则样本就是不动点。
 */
describe('assignees like（条件被静默丢弃，结果变多）', () => {
	it('★ 命中 like 写法', () => {
		expect(ids("assignees like 'bob'")).toContain('assignees-like-dropped');
	});

	it('★ 反例：assignees = 是正常写法，不该被提示', () => {
		// 少了这条，"无条件返回该提示"的实现也能过上面那条
		expect(ids("assignees = 'bob'")).not.toContain('assignees-like-dropped');
	});

	it('反例：别的字段用 like 是合法的（字符串字段），不提示', () => {
		expect(ids("title like 'urgent'")).toEqual([]);
	});
});

describe('assignees 比的是用户名不是 id', () => {
	it('★ 数字取值命中', () => {
		expect(ids('assignees = 901')).toContain('assignees-expects-username');
	});

	it('★ 反例：用户名取值不该提示', () => {
		expect(ids("assignees = 'bob'")).not.toContain('assignees-expects-username');
	});

	it('带引号的数字同样命中（引号不改变它是个 id）', () => {
		expect(ids("assignees = '901'")).toContain('assignees-expects-username');
	});
});

describe('labels 比的是标签 id 不是名字', () => {
	it('★ 名字取值命中', () => {
		expect(ids("labels = 'bug'")).toContain('labels-expects-id');
	});

	it('★ 反例：数字取值是正确写法，不该提示', () => {
		// labels 与 assignees 的正确类型恰好相反，两条反例合起来才说明
		// 实现是按字段区分的，而不是"看到引号就提示"
		expect(ids('labels = 950')).not.toContain('labels-expects-id');
	});

	it("反例：带引号的数字 labels = '950' 也不提示", () => {
		expect(ids("labels = '950'")).not.toContain('labels-expects-id');
	});
});

describe('整体行为', () => {
	it('干净的 filter 没有任何提示', () => {
		expect(filterHints('done = false && priority >= 3 && due_date < now+7d')).toEqual([]);
	});

	it('空 filter 没有提示', () => {
		expect(filterHints('')).toEqual([]);
	});

	it('一条 filter 里可以同时命中多条提示', () => {
		const result = ids("assignees like 'x' && labels = 'bug'");
		expect(result).toContain('assignees-like-dropped');
		expect(result).toContain('labels-expects-id');
	});

	it('★ assignees like 的文案必须说清方向是"结果变多"，而不只是"被忽略"', () => {
		// 这条提示的价值全在方向上：被丢掉的是收窄条件，用户会看到比预期更多的数据。
		// 只说"该条件无效"会让用户以为顶多是白写了，而不会想到结果不可信。
		const [hint] = filterHints("assignees like 'zzz'");
		expect(hint.message).toContain('多');
	});
});
