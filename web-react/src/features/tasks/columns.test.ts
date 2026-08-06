import { describe, expect, it } from 'vitest';

import { SORTABLE_TASK_FIELDS } from '@/lib/table-sort';
import {
	COLUMNS_STORAGE_KEY,
	DEFAULT_VISIBLE_COLUMNS,
	loadVisibleColumns,
	saveVisibleColumns,
	TASK_COLUMNS,
} from './columns';

interface FakeStorage {
	value: string | null;
	getItem(): string | null;
	setItem(key: string, value: string): void;
}

function fakeStorage(initial: string | null): FakeStorage {
	return {
		value: initial,
		getItem() {
			return this.value;
		},
		setItem(_key, value) {
			this.value = value;
		},
	};
}

/** 隐私模式下 localStorage 的方法会直接抛，列偏好不值得为此让整页挂掉。 */
function throwingStorage(): FakeStorage {
	return {
		value: null,
		getItem(): string | null {
			throw new DOMException('denied');
		},
		setItem(): void {
			throw new DOMException('denied');
		},
	};
}

describe('列定义', () => {
	it('列 id 唯一', () => {
		const ids = TASK_COLUMNS.map((c) => c.id);
		expect(new Set(ids).size).toBe(ids.length);
	});

	/**
	 * ★ 列上写的 sortField 必须是后端认的字段。TypeScript 已经用
	 * SortableTaskField 约束住了，这条是防有人把类型放宽成 string 之后静默漏过。
	 */
	it('★ 每个 sortField 都在后端白名单里', () => {
		for (const column of TASK_COLUMNS) {
			if (!column.sortField) continue;
			expect(SORTABLE_TASK_FIELDS).toContain(column.sortField);
		}
	});

	it('默认可见列非空（否则首屏是个没有列的表格）', () => {
		expect(DEFAULT_VISIBLE_COLUMNS.length).toBeGreaterThan(0);
	});

	it('标签与指派给明确不可排序（后端不支持）', () => {
		const byId = new Map(TASK_COLUMNS.map((c) => [c.id, c]));
		expect(byId.get('labels')?.sortField).toBeUndefined();
		expect(byId.get('assignees')?.sortField).toBeUndefined();
	});
});

describe('列偏好持久化', () => {
	it('没存过时用默认集合', () => {
		expect(loadVisibleColumns(fakeStorage(null))).toEqual(DEFAULT_VISIBLE_COLUMNS);
	});

	it('存过的原样读回', () => {
		expect(loadVisibleColumns(fakeStorage(JSON.stringify(['title', 'done'])))).toEqual([
			'title',
			'done',
		]);
	});

	/** ★ 列定义会随版本变，存着旧 id 的浏览器不该渲染出空列。 */
	it('★ 丢掉已不存在的列 id', () => {
		expect(loadVisibleColumns(fakeStorage(JSON.stringify(['title', '早就删了的列'])))).toEqual([
			'title',
		]);
	});

	it('全部失效时退回默认集合，而不是空表格', () => {
		expect(loadVisibleColumns(fakeStorage(JSON.stringify(['早就删了的列'])))).toEqual(
			DEFAULT_VISIBLE_COLUMNS,
		);
	});

	it.each(['not json', '{}', '123', 'null', '[]'])('存了 %s 这种脏数据时退回默认集合', (raw) => {
		expect(loadVisibleColumns(fakeStorage(raw))).toEqual(DEFAULT_VISIBLE_COLUMNS);
	});

	it('★ localStorage 抛异常时降级到默认集合而不是让页面崩', () => {
		expect(loadVisibleColumns(throwingStorage())).toEqual(DEFAULT_VISIBLE_COLUMNS);
		expect(() => saveVisibleColumns(throwingStorage(), ['title'])).not.toThrow();
	});

	it('写入的是 JSON 数组，key 固定', () => {
		const storage = fakeStorage(null);
		saveVisibleColumns(storage, ['title', 'done']);
		expect(JSON.parse(storage.value!)).toEqual(['title', 'done']);
		expect(COLUMNS_STORAGE_KEY).toBe('calton.table.columns');
	});
});
