import { describe, expect, it } from 'vitest';

import type { Task } from '@/api/tasks';
import { buildTaskTree } from './subtask-tree';

function task(id: number, extra: Partial<Task> = {}): Task {
	return { id, title: `T${id}`, ...extra };
}

/** 造一条 subtask 边：parent 的 related_tasks.subtask 含 child，child 回指 parent。 */
function link(parent: Task, ...children: Task[]): void {
	parent.related_tasks = {
		...(parent.related_tasks ?? {}),
		subtask: [...(parent.related_tasks?.subtask ?? []), ...children.map((c) => task(c.id))],
	};
	for (const child of children) {
		child.related_tasks = {
			...(child.related_tasks ?? {}),
			parenttask: [...(child.related_tasks?.parenttask ?? []), task(parent.id)],
		};
	}
}

describe('buildTaskTree', () => {
	it('把 subtask 挂到父任务下，父任务是顶层', () => {
		const p = task(1);
		const c = task(2);
		link(p, c);

		const { roots } = buildTaskTree([p, c]);
		expect(roots).toHaveLength(1);
		expect(roots[0].task.id).toBe(1);
		expect(roots[0].children.map((n) => n.task.id)).toEqual([2]);
		expect(roots[0].children[0].depth).toBe(1);
	});

	it('多层子任务，depth 递增', () => {
		const a = task(1);
		const b = task(2);
		const c = task(3);
		link(a, b);
		link(b, c);

		const { roots } = buildTaskTree([a, b, c]);
		expect(roots.map((n) => n.task.id)).toEqual([1]);
		expect(roots[0].children[0].task.id).toBe(2);
		expect(roots[0].children[0].children[0].task.id).toBe(3);
		expect(roots[0].children[0].children[0].depth).toBe(2);
	});

	it('父任务不在集合内时，子任务当顶层平铺（不消失）', () => {
		const child = task(2);
		child.related_tasks = { parenttask: [task(99)] }; // 父在别的项目

		const { roots } = buildTaskTree([child]);
		expect(roots.map((n) => n.task.id)).toEqual([2]);
	});

	it('subtask 指向集合外的任务时忽略该边（不炸）', () => {
		const p = task(1);
		p.related_tasks = { subtask: [task(88)] };

		const { roots } = buildTaskTree([p]);
		expect(roots.map((n) => n.task.id)).toEqual([1]);
		expect(roots[0].children).toEqual([]);
	});

	it('成环数据不死循环：环被打断，每个节点在树中恰好出现一次', () => {
		const a = task(1);
		const b = task(2);
		link(a, b);
		link(b, a); // a→b→a 成环

		const { roots } = buildTaskTree([a, b]);
		// 收集树里出现的全部 id（含各层）
		const ids: number[] = [];
		const walk = (nodes: typeof roots): void => {
			for (const n of nodes) {
				ids.push(n.task.id);
				walk(n.children);
			}
		};
		walk(roots);
		// 不无限展开、不丢节点：1 和 2 各恰好一次
		expect(ids.slice().sort()).toEqual([1, 2]);
	});

	it('未完成排在已完成前面', () => {
		const done = task(1, { done: true });
		const todo = task(2, { done: false });

		const { roots } = buildTaskTree([done, todo]);
		expect(roots.map((n) => n.task.id)).toEqual([2, 1]);
	});

	it('没有任何关系时，全部是顶层叶子', () => {
		const { roots } = buildTaskTree([task(1), task(2), task(3)]);
		expect(roots.map((n) => n.task.id)).toEqual([1, 2, 3]);
		expect(roots.every((n) => n.children.length === 0)).toBe(true);
	});
});
