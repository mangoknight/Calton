import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import {
	parseSortParam,
	serializeSortParam,
	SORTABLE_TASK_FIELDS,
	toggleSort,
	toSortQuery,
	type SortSpec,
} from './table-sort';

const SORT_GO = resolve(process.cwd(), '..', 'pkg/models/task_collection_sort.go');

/**
 * 可排序字段表必须与后端的 `validateTaskFieldForSorting` 一致。
 *
 * 前端多写一个字段 → 用户点了列头拿到后端报错；少写一个 → 那一列静默不可排序。
 * 两种都不会在开发期暴露，所以直接读 Go 源码对账（它是对拍基准，原地不动）。
 */
describe('可排序字段表与 Go 白名单对账', () => {
	const source = existsSync(SORT_GO) ? readFileSync(SORT_GO, 'utf8') : '';

	it('能读到 Go 的排序校验源码（读不到则后面的对账是假绿）', () => {
		expect(existsSync(SORT_GO)).toBe(true);
		expect(source).toContain('func validateTaskFieldForSorting');
	});

	/** 把 `taskPropertyFoo string = "foo"` 解析成 常量名 → 字面值。 */
	function constantValues(): Map<string, string> {
		const map = new Map<string, string>();
		for (const match of source.matchAll(/(taskProperty\w+)\s+string\s*=\s*"([^"]+)"/g)) {
			map.set(match[1]!, match[2]!);
		}
		return map;
	}

	/** 取 validateTaskFieldForSorting 的 case 列表里引用的常量。 */
	function goSortableFields(): string[] {
		const body = source.slice(source.indexOf('func validateTaskFieldForSorting'));
		const caseBlock = body.slice(body.indexOf('case'), body.indexOf('return nil'));
		const values = constantValues();
		return [...caseBlock.matchAll(/taskProperty\w+/g)]
			.map((match) => values.get(match[0]))
			.filter((value): value is string => value !== undefined);
	}

	it('解析出的 Go 白名单非空（解析失败会让下面的比较退化成空集相等）', () => {
		expect(goSortableFields().length).toBeGreaterThan(10);
	});

	it('★ 前端表与 Go 白名单逐字相同（多一个 = 点了报错，少一个 = 静默不可排序）', () => {
		expect([...SORTABLE_TASK_FIELDS].sort()).toEqual(goSortableFields().sort());
	});

	/**
	 * relevance 只在带 `s` 搜索词时有意义，故意不进列头。
	 * 它在 Go 里也不在 validateTaskFieldForSorting 内（sortParam.validate 单独放行），
	 * 所以上面那条等式成立本身就依赖这个前提 —— 在这里写明，免得日后有人"补全"它。
	 */
	it('relevance 不在两边的表里（它由 sortParam.validate 单独放行）', () => {
		expect(SORTABLE_TASK_FIELDS).not.toContain('relevance');
		expect(goSortableFields()).not.toContain('relevance');
	});
});

describe('parseSortParam', () => {
	it('按出现顺序解析（顺序即语义，不能重排）', () => {
		expect(parseSortParam('due_date:asc,priority:desc,title:asc')).toEqual([
			{ field: 'due_date', direction: 'asc' },
			{ field: 'priority', direction: 'desc' },
			{ field: 'title', direction: 'asc' },
		]);
	});

	it('缺方向时默认 asc（与后端"OrderBy 短了补 asc"一致）', () => {
		expect(parseSortParam('title')).toEqual([{ field: 'title', direction: 'asc' }]);
	});

	it.each([null, undefined, ''])('%s → 空数组', (raw) => {
		expect(parseSortParam(raw)).toEqual([]);
	});

	it('不在白名单的字段被丢掉（否则后端会直接报错）', () => {
		expect(parseSortParam('labels:asc,title:asc')).toEqual([{ field: 'title', direction: 'asc' }]);
	});

	it('非法方向被丢掉', () => {
		expect(parseSortParam('title:sideways,priority:desc')).toEqual([
			{ field: 'priority', direction: 'desc' },
		]);
	});

	it('重复字段只认第一次', () => {
		expect(parseSortParam('title:asc,title:desc')).toEqual([{ field: 'title', direction: 'asc' }]);
	});

	it('序列化与解析往返一致', () => {
		const specs: SortSpec[] = [
			{ field: 'done', direction: 'desc' },
			{ field: 'due_date', direction: 'asc' },
		];
		expect(parseSortParam(serializeSortParam(specs))).toEqual(specs);
	});
});

describe('toggleSort 三态循环', () => {
	it('未排序 → asc → desc → 移出', () => {
		let specs: SortSpec[] = [];
		specs = toggleSort(specs, 'title');
		expect(specs).toEqual([{ field: 'title', direction: 'asc' }]);
		specs = toggleSort(specs, 'title');
		expect(specs).toEqual([{ field: 'title', direction: 'desc' }]);
		specs = toggleSort(specs, 'title');
		expect(specs).toEqual([]);
	});

	/** ★ 先点的是主序。插到开头会让第二次点击悄悄夺走主序。 */
	it('★ 新字段追加到末尾，先点的列保持主序', () => {
		const specs = toggleSort(toggleSort([], 'due_date'), 'priority');
		expect(specs.map((s) => s.field)).toEqual(['due_date', 'priority']);
	});

	it('切换已有字段的方向不改变它在序列中的位置', () => {
		const start: SortSpec[] = [
			{ field: 'due_date', direction: 'asc' },
			{ field: 'priority', direction: 'asc' },
		];
		const next = toggleSort(start, 'due_date');
		expect(next).toEqual([
			{ field: 'due_date', direction: 'desc' },
			{ field: 'priority', direction: 'asc' },
		]);
	});

	it('移除中间的字段不打乱其余顺序', () => {
		const start: SortSpec[] = [
			{ field: 'due_date', direction: 'asc' },
			{ field: 'priority', direction: 'desc' },
			{ field: 'title', direction: 'asc' },
		];
		expect(toggleSort(start, 'priority').map((s) => s.field)).toEqual(['due_date', 'title']);
	});

	it('不修改传入的数组（状态要能安全地放进 URL/state）', () => {
		const start: SortSpec[] = [{ field: 'title', direction: 'asc' }];
		toggleSort(start, 'title');
		toggleSort(start, 'priority');
		expect(start).toEqual([{ field: 'title', direction: 'asc' }]);
	});
});

describe('toSortQuery：按下标成对', () => {
	it('★ 两个数组等长且同序', () => {
		const specs: SortSpec[] = [
			{ field: 'due_date', direction: 'desc' },
			{ field: 'priority', direction: 'asc' },
			{ field: 'title', direction: 'desc' },
		];
		expect(toSortQuery(specs)).toEqual({
			sort_by: ['due_date', 'priority', 'title'],
			order_by: ['desc', 'asc', 'desc'],
		});
	});

	/**
	 * ★ 配对是按下标的，所以"每个字段拿到自己的方向"这件事必须逐位成立。
	 * 光断言两个数组各自内容正确不够 —— 顺序整体反转也能骗过那种断言。
	 */
	it('★ 逐位配对：第 i 个 order_by 属于第 i 个 sort_by', () => {
		// ⚠️ 方向序列**故意不是回文**（asc,desc,desc）：用 asc,desc,asc 这种回文的话，
		// "把 order_by 整体反转"这个 bug 能原样骗过本条断言。变异测试实测踩过。
		const specs: SortSpec[] = [
			{ field: 'done', direction: 'asc' },
			{ field: 'due_date', direction: 'desc' },
			{ field: 'priority', direction: 'desc' },
		];
		const { sort_by, order_by } = toSortQuery(specs);

		expect(sort_by).toHaveLength(order_by.length);
		specs.forEach((spec, i) => {
			expect([sort_by[i], order_by[i]]).toEqual([spec.field, spec.direction]);
		});
	});

	it('空排序产出两个空数组（buildQuery 会把它们整体略掉）', () => {
		expect(toSortQuery([])).toEqual({ sort_by: [], order_by: [] });
	});
});
