import { describe, expect, it } from 'vitest';

import type { Project } from '@/api/projects';
import {
	applyParentSelection,
	parseParentSelection,
	PARENT_KEEP,
	selectableParents,
	TOP_LEVEL_PARENT_ID,
} from './parent-field';

function project(id: number, extra: Partial<Project> = {}): Project {
	return { id, title: `P${id}`, ...extra };
}

describe('两态解析', () => {
	it.each([
		[PARENT_KEEP, PARENT_KEEP],
		['0', 0],
		['42', 42],
	])('%s → %s', (input, expected) => {
		expect(parseParentSelection(input)).toBe(expected);
	});
});

describe('编码进 payload', () => {
	it('顶层就是 0（实测定案）', () => {
		expect(TOP_LEVEL_PARENT_ID).toBe(0);
		expect(applyParentSelection({ title: 'x' }, TOP_LEVEL_PARENT_ID)).toEqual({
			title: 'x',
			parent_project_id: 0,
		});
	});

	it('选具体项目 → 带上该 id', () => {
		expect(applyParentSelection({ title: 'x' }, 42)).toEqual({ title: 'x', parent_project_id: 42 });
	});

	/**
	 * ★ 这组是 F04b 最要紧的行为。
	 *
	 * 后端实测：省略该字段 = 不改父级（Go 侧 *int64 得到 nil 指针，更新时跳过）。
	 * 该字段是 AC-6「POST 全量替换」的显式例外 —— **不能**为了迎合全量替换而
	 * 总是回传当前值，那样并发编辑时会用陈旧值覆盖别人刚做的移动操作。
	 */
	describe('不改（keep）：必须把键删掉，而不是回传旧值', () => {
		it('全量回传的对象里，parent_project_id 被删掉', () => {
			// 编辑走的是 {...project} 全量回传，服务端读来的旧值就在里面
			const payload = { ...project(3, { parent_project_id: 7 }), title: '改名后' };
			const result = applyParentSelection(payload, PARENT_KEEP);

			expect('parent_project_id' in result).toBe(false);
			// 其余字段照常全量回传
			expect(result).toMatchObject({ id: 3, title: '改名后' });
		});

		it('不是把它设成 0 —— 那会把子项目意外提到顶层', () => {
			const payload = { ...project(3, { parent_project_id: 7 }) };
			const result = applyParentSelection(payload, PARENT_KEEP) as Record<string, unknown>;

			expect(result.parent_project_id).toBeUndefined();
			expect(result.parent_project_id).not.toBe(0);
		});

		it('本来就没有该键时也不会凭空造一个', () => {
			const result = applyParentSelection({ title: 'x' }, PARENT_KEEP);
			expect('parent_project_id' in result).toBe(false);
		});
	});

	it('两态在 payload 上可区分：keep 省略、选顶层显式发 0', () => {
		const payload = { ...project(3, { parent_project_id: 7 }) };

		const keep = applyParentSelection(payload, PARENT_KEEP) as Record<string, unknown>;
		const top = applyParentSelection(payload, TOP_LEVEL_PARENT_ID) as Record<string, unknown>;

		expect('parent_project_id' in keep).toBe(false);
		expect(top.parent_project_id).toBe(0);
	});
});

describe('可选上级项目', () => {
	it('编辑时排除自己，否则一键造出自环', () => {
		const list = [project(1), project(2), project(3)];
		expect(selectableParents(list, project(2)).map((p) => p.id)).toEqual([1, 3]);
	});

	it('新建时没有自己，全部可选', () => {
		const list = [project(1), project(2)];
		expect(selectableParents(list).map((p) => p.id)).toEqual([1, 2]);
	});
});
