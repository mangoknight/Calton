import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { parseRouteId } from '@/lib/route-params';
import {
	FAVORITES_PROJECT_ID,
	isFavoritesProjectId,
	isSavedFilterProjectId,
	parseFilterRouteId,
	projectIdFromSavedFilterId,
	savedFilterIdFromProjectId,
} from './pseudo-project';

/**
 * ★★ 把换算式与"-1 是收藏夹"的判据钉在 Go 源码上。
 *
 * 这两处写错都**不报错**：换算式错一位会去查另一个过滤器；
 * 判据写成 `<= -1` 会把收藏夹当过滤器，去查一个后端视为无效的 filterID 0。
 */
describe('伪项目换算与 Go 源码对账', () => {
	const savedFiltersGo = resolve(process.cwd(), '..', 'pkg/models/saved_filters.go');
	const collectionGo = resolve(process.cwd(), '..', 'pkg/models/task_collection.go');

	it('能读到两个 Go 源文件（读不到则以下对账是假绿）', () => {
		expect(existsSync(savedFiltersGo)).toBe(true);
		expect(existsSync(collectionGo)).toBe(true);
	});

	it('★★ 换算式与 Go 一致（filterID = projectID*-1 - 1）', () => {
		const source = readFileSync(savedFiltersGo, 'utf8');
		expect(source).toMatch(/filterID = projectID\*-1 - 1/);
		expect(source).toMatch(/projectID = filterID\*-1 - 1/);
	});

	/**
	 * ★★ 判据是**严格小于** -1。这条红了说明后端把收藏夹也纳入了 saved filter 分支，
	 * 前端的 `< -1` 就得跟着改 —— 或者反过来，有人在前端写成了 `<= -1`。
	 */
	it('★★ Go 侧用 `ProjectID < -1` 判 saved filter（-1 留给收藏夹）', () => {
		const source = readFileSync(collectionGo, 'utf8');
		expect(source).toMatch(/tf\.ProjectID < -1/);
		// 同处注释也写明了 -1 是收藏夹
		expect(source).toMatch(/-1 is the favorites project/);
	});

	it('★ ToProject 用的就是这个换算（侧栏里的伪项目 id 由它产生）', () => {
		const source = readFileSync(savedFiltersGo, 'utf8');
		const start = source.indexOf('func (sf *SavedFilter) ToProject');
		expect(source.slice(start, start + 300)).toMatch(
			/ID:\s+getProjectIDFromSavedFilterID\(sf\.ID\)/,
		);
	});
});

describe('isSavedFilterProjectId', () => {
	/** ★ -1 是收藏夹，不是 saved filter。差一个等号就错。 */
	it('★ -1（收藏夹）不算 saved filter', () => {
		expect(isSavedFilterProjectId(FAVORITES_PROJECT_ID)).toBe(false);
		expect(isFavoritesProjectId(FAVORITES_PROJECT_ID)).toBe(true);
	});

	it.each([-2, -3, -100])('%s 算 saved filter', (id) => {
		expect(isSavedFilterProjectId(id)).toBe(true);
	});

	it.each([0, 1, 12])('正常项目 %s 不算', (id) => {
		expect(isSavedFilterProjectId(id)).toBe(false);
	});

	it('非整数不算', () => {
		expect(isSavedFilterProjectId(-2.5)).toBe(false);
		expect(isSavedFilterProjectId(Number.NaN)).toBe(false);
	});
});

describe('两个方向的换算', () => {
	it.each([
		[1, -2],
		[2, -3],
		[99, -100],
	])('filter %s ↔ 伪项目 %s', (filterId, projectId) => {
		expect(projectIdFromSavedFilterId(filterId)).toBe(projectId);
		expect(savedFilterIdFromProjectId(projectId)).toBe(filterId);
	});

	/** ★ 往返必须回到原值，任何一侧差一位都会在这里露出来。 */
	it.each([1, 2, 7, 500])('★ filter %s 往返一致', (filterId) => {
		const projectId = projectIdFromSavedFilterId(filterId)!;
		expect(savedFilterIdFromProjectId(projectId)).toBe(filterId);
	});

	/** ★ 收藏夹换不出 filter id —— 返回 null 而不是 0。 */
	it('★ 收藏夹 -1 换算出 null，不是 0', () => {
		expect(savedFilterIdFromProjectId(-1)).toBeNull();
		expect(savedFilterIdFromProjectId(-1)).not.toBe(0);
	});

	it('正常项目 id 换不出 filter id', () => {
		expect(savedFilterIdFromProjectId(12)).toBeNull();
		expect(savedFilterIdFromProjectId(0)).toBeNull();
	});

	/** filter id 0 / 负数是无效的（后端把 filterID 0 视为无效）。 */
	it.each([0, -1, 1.5])('filter id %s 无效', (filterId) => {
		expect(projectIdFromSavedFilterId(filterId)).toBeNull();
	});
});

describe('★ parseFilterRouteId 与 parseRouteId 的分工', () => {
	it.each([
		['1', 1],
		['2', 2],
		['500', 500],
	])('接受正整数 %s', (raw, expected) => {
		expect(parseFilterRouteId(raw)).toBe(expected);
	});

	it.each(['0', '-2', 'abc', '', '1.5', undefined])('拒绝 %s', (raw) => {
		expect(parseFilterRouteId(raw)).toBeNull();
	});

	/**
	 * ★★ 这条锁住的是一个**设计选择**，不是实现细节。
	 *
	 * URL 里放的是**正的 filter id**，负数伪项目 ID 只在调接口时出现。
	 * 所以 `parseRouteId` 不需要为了 F11b 放宽去接受负数 ——
	 * 放宽它等于把 `/projects/new/list` 那道守卫一起拆了。
	 * （这是我在 F05a 埋下的悬置项，当时记的是"F11b 届时需改 parseRouteId"；
	 * 真做到这里发现更好的解法是**在边界换算**，而不是让负数在路由里流通。）
	 */
	it('★★ parseRouteId 仍然拒绝负数，F11b 没有放宽它', () => {
		expect(parseRouteId('-2')).toBeNull();
		expect(parseRouteId('0')).toBeNull();
		expect(parseRouteId('12')).toBe(12);
	});
});
