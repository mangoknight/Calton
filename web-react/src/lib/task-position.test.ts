import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import {
	MIN_POSITION_SPACING,
	POSITION_STEP,
	positionBetween,
	positionForInsert,
	willTriggerRecalculation,
	type Positioned,
} from './task-position';

/**
 * 两个常量必须跟后端一致，而且它们**不一致时不会报错**：
 * 间距写小了会更频繁地触发后端重算（性能问题，看不出来）；
 * MIN 写错了则"要不要重取"这个判断会失灵，用户看到的 position 与库里不符。
 */
describe('position 常量与 Go 源码对账', () => {
	const positionGo = resolve(process.cwd(), '..', 'pkg/models/task_position.go');
	const tasksGo = resolve(process.cwd(), '..', 'pkg/models/tasks.go');

	it('能读到两个 Go 源文件（读不到则以下对账是假绿）', () => {
		expect(existsSync(positionGo)).toBe(true);
		expect(existsSync(tasksGo)).toBe(true);
	});

	it('★ MIN_POSITION_SPACING 与 Go 的 MinPositionSpacing 相同', () => {
		const source = readFileSync(positionGo, 'utf8');
		const match = source.match(/MinPositionSpacing\s*=\s*([\d.eE+-]+)/);
		expect(match).not.toBeNull();
		expect(Number(match![1])).toBe(MIN_POSITION_SPACING);
	});

	it('★ POSITION_STEP 与 Go 的 calculateDefaultPosition 用的 2^16 相同', () => {
		const source = readFileSync(tasksGo, 'utf8');
		expect(source).toMatch(/calculateDefaultPosition/);
		expect(source).toMatch(/math\.Pow\(2,\s*16\)/);
		expect(POSITION_STEP).toBe(2 ** 16);
	});

	/**
	 * ★ 判据是 position 的**绝对值**，不是相邻两者的间距。
	 * 读成"间距小于 0.01 才重算"会让 willTriggerRecalculation 在该报的时候不报。
	 */
	it('★ Go 的重算判据是绝对值 tp.Position < MinPositionSpacing', () => {
		const source = readFileSync(positionGo, 'utf8');
		expect(source).toMatch(/tp\.Position\s*<\s*MinPositionSpacing/);
		expect(source).toMatch(/RecalculateTaskPositions/);
	});
});

describe('positionBetween', () => {
	it('两侧都有邻居时取中值', () => {
		expect(positionBetween(100, 200)).toBe(150);
	});

	/** 拖到最顶上：只有下邻居，取它的一半，保证严格小于它。 */
	it('拖到最顶上时取下邻居的一半', () => {
		expect(positionBetween(undefined, 100)).toBe(50);
	});

	/** 拖到最底下：只有上邻居，往后推一个默认间距。 */
	it('拖到最底下时在上邻居之后加一个默认间距', () => {
		expect(positionBetween(100, undefined)).toBe(100 + POSITION_STEP);
	});

	it('空列时给一个默认间距', () => {
		expect(positionBetween(undefined, undefined)).toBe(POSITION_STEP);
	});

	/** position 可能是 0（后端在某些路径下确实会给 0），0 是合法邻居不是"没有邻居"。 */
	it('★ 邻居的 position 为 0 时仍算作有邻居', () => {
		expect(positionBetween(0, 100)).toBe(50);
		expect(positionBetween(undefined, 0)).toBe(0);
	});

	it('NaN / Infinity 当作没有邻居，不产出 NaN', () => {
		expect(positionBetween(Number.NaN, 100)).toBe(50);
		expect(Number.isFinite(positionBetween(Number.NaN, Number.NaN))).toBe(true);
	});

	/** ★ 结果必须严格落在两个邻居之间，否则排序结果和用户看到的落点不一致。 */
	it.each([
		[100, 200],
		[0, 1],
		[1e-6, 2e-6],
		[65536, 131072],
	])('★ 结果严格落在 (%s, %s) 之间', (before, after) => {
		const result = positionBetween(before, after);
		expect(result).toBeGreaterThan(before);
		expect(result).toBeLessThan(after);
	});
});

describe('willTriggerRecalculation', () => {
	it.each([0, 0.001, MIN_POSITION_SPACING / 2])('%s 会触发后端重算', (position) => {
		expect(willTriggerRecalculation(position)).toBe(true);
	});

	it.each([MIN_POSITION_SPACING, 1, POSITION_STEP])('%s 不触发', (position) => {
		expect(willTriggerRecalculation(position)).toBe(false);
	});

	/** 反复拖到最顶上会一路对半分，最终必然跌破阈值——这条记录该行为确实可达。 */
	it('★ 反复拖到最顶端最终会跌破阈值（说明重取不是纸上谈兵）', () => {
		let position = POSITION_STEP;
		let steps = 0;
		while (!willTriggerRecalculation(position) && steps < 100) {
			position = positionBetween(undefined, position);
			steps += 1;
		}
		expect(willTriggerRecalculation(position)).toBe(true);
		expect(steps).toBeLessThan(30);
	});
});

describe('positionForInsert', () => {
	const items: Positioned[] = [
		{ id: 1, position: 100 },
		{ id: 2, position: 200 },
		{ id: 3, position: 300 },
	];

	it('插到中间取相邻两者的中值', () => {
		expect(positionForInsert(items, 99, 1)).toBe(150);
	});

	it('插到最前面取首个的一半', () => {
		expect(positionForInsert(items, 99, 0)).toBe(50);
	});

	it('插到最后面在末位之后加一个间距', () => {
		expect(positionForInsert(items, 99, 3)).toBe(300 + POSITION_STEP);
	});

	/**
	 * ★ 同列内下移：必须先把自己从列表里剔除再算邻居。
	 * 不剔除的话，把 id=1 拖到 index 1 会拿"自己(100)"和"200"算中值 = 150，
	 * 但它本来就在 100，视觉上等于没动 —— 一个只在同列拖动时才出现的 bug。
	 */
	it('★ 同列内下移时先剔除自己，落点才是真的下移', () => {
		// 把 id=1（当前最前）拖到第 2 个位置：剔除自己后邻居是 200 和 300
		expect(positionForInsert(items, 1, 1)).toBe(250);
	});

	it('★ 同列内上移同样剔除自己', () => {
		// 把 id=3 拖到最前：剔除自己后下邻居是 100
		expect(positionForInsert(items, 3, 0)).toBe(50);
	});

	it('越界的 targetIndex 被夹住而不是产出 undefined 邻居', () => {
		expect(positionForInsert(items, 99, -5)).toBe(50);
		expect(positionForInsert(items, 99, 99)).toBe(300 + POSITION_STEP);
	});

	it('空列表时给默认间距', () => {
		expect(positionForInsert([], 99, 0)).toBe(POSITION_STEP);
	});

	it('缺 position 字段的条目当作没有邻居', () => {
		expect(positionForInsert([{ id: 1 }], 99, 1)).toBe(POSITION_STEP);
	});
});
