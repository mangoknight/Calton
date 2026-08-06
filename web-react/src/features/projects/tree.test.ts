import { describe, expect, it } from 'vitest';

import type { Project } from '@/api/projects';
import { buildProjectTree, collectSubtree, flattenTree, foreignOwnedProjects } from './tree';

function project(id: number, parent = 0, extra: Partial<Project> = {}): Project {
	return { id, title: `P${id}`, parent_project_id: parent, ...extra };
}

/** 树 → `id(childIds…)` 形式，断言起来比嵌套对象好读。 */
function shape(nodes: ReturnType<typeof buildProjectTree>['roots']): string {
	return nodes
		.map((n) =>
			n.children.length ? `${n.project.id}(${shape(n.children)})` : String(n.project.id),
		)
		.join(',');
}

describe('建树', () => {
	it('三层嵌套正确挂接', () => {
		const { roots, cycles } = buildProjectTree([
			project(1),
			project(2, 1),
			project(3, 2),
			project(4, 1),
		]);

		expect(shape(roots)).toBe('1(2(3),4)');
		expect(cycles).toEqual([]);
		expect(flattenTree(roots).map((n) => n.depth)).toEqual([0, 1, 2, 1]);
	});

	/**
	 * Go 模型里 parent_project_id 是 *int64（可空、无 omitempty），
	 * 线上三种形态都可能出现：显式 0 / null / 键缺失。
	 * 实测口径 tester 还在验，所以三种都当顶层处理 —— 无论结果是哪种都不会错。
	 */
	it.each([
		['显式 0', { parent_project_id: 0 }],
		['null', { parent_project_id: null }],
		['键缺失', {}],
	])('顶层的三种形态：%s', (_label, parentField) => {
		const input = [
			{ id: 1, title: 'P1', ...parentField },
			{ id: 2, title: 'P2', parent_project_id: 1 },
		] as Project[];

		const { roots, cycles } = buildProjectTree(input);
		expect(shape(roots)).toBe('1(2)');
		expect(cycles).toEqual([]);
	});

	it('父项目不在返回集里时按顶层渲染，整棵子树不会消失', () => {
		// 父项目可能因无权限/已归档被后端过滤掉
		const { roots, cycles } = buildProjectTree([project(5, 999), project(6, 5)]);

		expect(shape(roots)).toBe('5(6)');
		expect(cycles).toEqual([]);
	});

	it('按 position 排序，position 相同再按标题', () => {
		const { roots } = buildProjectTree([
			project(1, 0, { position: 20, title: 'B' }),
			project(2, 0, { position: 10, title: 'C' }),
			project(3, 0, { position: 10, title: 'A' }),
		]);

		expect(shape(roots)).toBe('3,2,1');
	});

	it('子节点也按 position 排序', () => {
		const { roots } = buildProjectTree([
			project(1),
			project(2, 1, { position: 30 }),
			project(3, 1, { position: 10 }),
		]);

		expect(shape(roots)).toBe('1(3,2)');
	});

	it('空列表不炸', () => {
		expect(buildProjectTree([])).toEqual({ roots: [], cycles: [] });
	});
});

describe('★ 负 ID 伪项目（saved filter 的投影）', () => {
	it('不进项目树 —— 否则会作为顶层节点混在真实项目里冒出来', () => {
		const { roots, cycles } = buildProjectTree([
			project(1),
			{ id: -2, title: 'My Open Tasks', parent_project_id: 0 },
			project(2, 1),
		]);

		expect(shape(roots)).toBe('1(2)');
		expect(cycles).toEqual([]);
	});

	it('伪项目也不会混进 cycles（是过滤掉，不是判成环）', () => {
		const { cycles } = buildProjectTree([{ id: -2, title: 'My Open Tasks', parent_project_id: 0 }]);
		expect(cycles).toEqual([]);
	});

	it('真实项目误挂到伪项目下时按顶层渲染，不会消失', () => {
		const { roots, cycles } = buildProjectTree([
			{ id: -2, title: 'My Open Tasks', parent_project_id: 0 },
			project(5, -2),
		]);

		expect(shape(roots)).toBe('5');
		expect(cycles).toEqual([]);
	});
});

describe('★ parent 成环', () => {
	it('两节点互为父子：不死循环，两个都进 cycles', () => {
		const { roots, cycles } = buildProjectTree([project(1, 2), project(2, 1)]);

		expect(roots).toEqual([]);
		expect(cycles.map((p) => p.id)).toEqual([1, 2]);
	});

	it('三节点环 A→B→C→A：整环进 cycles', () => {
		const { roots, cycles } = buildProjectTree([project(1, 3), project(2, 1), project(3, 2)]);

		expect(roots).toEqual([]);
		expect(cycles.map((p) => p.id)).toEqual([1, 2, 3]);
	});

	it('自环（自己是自己的父）也被剔出来', () => {
		const { roots, cycles } = buildProjectTree([project(1, 1), project(2)]);

		expect(shape(roots)).toBe('2');
		expect(cycles.map((p) => p.id)).toEqual([1]);
	});

	it('环之外的正常项目照常成树，不被环连累', () => {
		const { roots, cycles } = buildProjectTree([
			project(1),
			project(2, 1),
			project(10, 11),
			project(11, 10),
		]);

		expect(shape(roots)).toBe('1(2)');
		expect(cycles.map((p) => p.id)).toEqual([10, 11]);
	});

	it('挂在环上的子树一并进 cycles（它没有顶层祖先）', () => {
		const { roots, cycles } = buildProjectTree([
			project(1, 2),
			project(2, 1),
			project(3, 1), // 父在环里
		]);

		expect(roots).toEqual([]);
		expect(cycles.map((p) => p.id)).toEqual([1, 2, 3]);
	});

	it('一个项目都不丢：roots + cycles 覆盖全部输入', () => {
		const input = [project(1), project(2, 1), project(10, 11), project(11, 10), project(20, 999)];
		const { roots, cycles } = buildProjectTree(input);

		const seen = [...flattenTree(roots).map((n) => n.project.id), ...cycles.map((p) => p.id)];
		expect(seen.sort((a, b) => a - b)).toEqual([1, 2, 10, 11, 20]);
	});

	it('大环也能在合理时间内返回（不是靠限深硬扛）', () => {
		// 1000 个节点首尾相接成一个大环
		const size = 1000;
		const input = Array.from({ length: size }, (_, i) => project(i + 1, i === 0 ? size : i));

		const started = Date.now();
		const { roots, cycles } = buildProjectTree(input);

		expect(roots).toEqual([]);
		expect(cycles).toHaveLength(size);
		expect(Date.now() - started).toBeLessThan(1000);
	});
});

describe('删除影响面（collectSubtree / foreignOwnedProjects）', () => {
	it('★ 收集整棵子树，不只一层 —— 删除是完全递归的', () => {
		// 实测：P→C1→C2 三层，删 P 之后三个全部 404
		const projects = [project(1), project(2, 1), project(3, 2), project(9)];
		expect(collectSubtree(projects, 1).map((p) => p.id)).toEqual([1, 2, 3]);
	});

	it('只含自己时返回自己', () => {
		expect(collectSubtree([project(1)], 1).map((p) => p.id)).toEqual([1]);
	});

	it('不误收兄弟节点', () => {
		const projects = [project(1), project(2, 1), project(3)];
		expect(collectSubtree(projects, 1).map((p) => p.id)).toEqual([1, 2]);
	});

	it('目标不存在时返回空', () => {
		expect(collectSubtree([project(1)], 99)).toEqual([]);
	});

	it('成环数据下不死循环（本函数从任意节点进入，环防护是承重的）', () => {
		const projects = [project(1, 2), project(2, 1)];
		const started = Date.now();
		const subtree = collectSubtree(projects, 1);

		expect(subtree.map((p) => p.id).sort()).toEqual([1, 2]);
		expect(Date.now() - started).toBeLessThan(1000);
	});

	it('★ 挑出属于别人的项目 —— 删除会跨所有权边界硬删', () => {
		const subtree = [
			{ ...project(1), owner: { id: 10, username: 'alice' } },
			{ ...project(2, 1), owner: { id: 20, username: 'bob' } },
			{ ...project(3, 1), owner: { id: 10, username: 'alice' } },
		];

		expect(foreignOwnedProjects(subtree, 10).map((p) => p.id)).toEqual([2]);
	});

	it('owner 缺失时不误报为"别人的"（数据不全时别吓唬用户）', () => {
		const subtree = [project(1), project(2, 1)];
		expect(foreignOwnedProjects(subtree, 10)).toEqual([]);
	});

	it('当前用户未知时不下结论', () => {
		const subtree = [{ ...project(1), owner: { id: 20, username: 'bob' } }];
		expect(foreignOwnedProjects(subtree, undefined)).toEqual([]);
	});
});
