import { describe, expect, it } from 'vitest';

import type { Task } from '@/api/tasks';
import { DAY_MS, dueList, dueUrgency, perPerson, perProject, summarize } from './metrics';

const NOW = Date.UTC(2026, 7, 15, 12, 0, 0); // 2026-08-15 12:00 UTC
const iso = (offsetDays: number) => new Date(NOW + offsetDays * DAY_MS).toISOString();

function task(over: Partial<Task>): Task {
	return { id: 1, title: 't', assignees: [], ...over } as Task;
}

const TASKS: Task[] = [
	task({ id: 1, title: '逾期', due_date: iso(-2), assignees: [{ id: 10, name: 'A' }] }),
	task({ id: 2, title: '今天后天', due_date: iso(2), assignees: [{ id: 10, name: 'A' }, { id: 11, name: 'B' }] }),
	task({ id: 3, title: '很久以后', due_date: iso(30), assignees: [{ id: 11, name: 'B' }] }),
	task({ id: 4, title: '无到期日进行中', percent_done: 50, assignees: [{ id: 10, name: 'A' }] }),
	task({ id: 5, title: '已完成', done: true, due_date: iso(-5), assignees: [{ id: 11, name: 'B' }] }),
	task({ id: 6, title: '无主逾期', due_date: iso(-1), assignees: [] }),
];

describe('dashboard metrics', () => {
	it('dueUrgency 按到期日与完成态分类', () => {
		expect(dueUrgency(TASKS[0], NOW)).toBe('overdue');
		expect(dueUrgency(TASKS[1], NOW)).toBe('soon');
		expect(dueUrgency(TASKS[2], NOW)).toBe('later');
		expect(dueUrgency(TASKS[3], NOW)).toBe('none'); // 无到期日
		expect(dueUrgency(TASKS[4], NOW)).toBeNull(); // 已完成不参与
	});

	it('summarize 汇总各口径', () => {
		const s = summarize(TASKS, NOW);
		expect(s.total).toBe(6);
		expect(s.done).toBe(1);
		expect(s.doing).toBe(1); // id4 有进度
		expect(s.todo).toBe(4);
		expect(s.overdue).toBe(2); // id1, id6
		expect(s.dueSoon).toBe(1); // id2
		expect(s.unassigned).toBe(1); // id6 未完成且无 assignee
	});

	it('perPerson 多 assignee 各记一次 + 未分配行,按未完成降序', () => {
		const rows = perPerson(TASKS, NOW);
		const byName = Object.fromEntries(rows.map((r) => [r.name, r]));
		expect(byName['A'].open).toBe(3); // id1,2,4
		expect(byName['A'].overdue).toBe(1); // id1
		expect(byName['B'].open).toBe(2); // id2,3
		expect(byName['B'].done).toBe(1); // id5
		expect(byName['未分配'].open).toBe(1); // id6
		expect(rows[0].name).toBe('A'); // 未完成最多在前
	});

	it('perProject 完成率与逾期', () => {
		const tasks = [
			task({ id: 1, project_id: 7, done: true }),
			task({ id: 2, project_id: 7, done: false, due_date: iso(-1) }),
			task({ id: 3, project_id: 7, done: false }),
			task({ id: 4, project_id: -3 }), // 伪项目,忽略
		];
		const rows = perProject(tasks, [{ id: 7, title: '项目甲' } as never], NOW);
		expect(rows).toHaveLength(1);
		expect(rows[0]).toMatchObject({ id: 7, title: '项目甲', total: 3, done: 1, overdue: 1, pct: 33 });
	});

	it('dueList 逾期/即将到期各自按到期日升序', () => {
		expect(dueList(TASKS, NOW, 'overdue').map((t) => t.id)).toEqual([1, 6]); // -2 在 -1 前
		expect(dueList(TASKS, NOW, 'soon').map((t) => t.id)).toEqual([2]);
	});
});
