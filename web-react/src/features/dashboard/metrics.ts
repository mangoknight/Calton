import type { Project } from '@/api/projects';
import type { Task } from '@/api/tasks';
import { parseApiTime } from '@/lib/datetime';
import { statusOf, type BoardStatus } from '@/features/board/queries';

/**
 * 管理面板的纯聚合函数。全部无副作用、`now` 从外部传进来 —— 这样能直接单测，
 * 也不会因为"当前时间"让用例变得不确定。数据源是全局 `GET /tasks`（见 board queries）。
 *
 * "逾期"= 未完成 && 有到期日 && 到期日 < now。到期日的零值（`0001-01-01`）由
 * `parseApiTime` 归成 `null`，即"没有到期日"，不算逾期也不算即将到期。
 */

export const DAY_MS = 24 * 60 * 60 * 1000;

/** 到期紧迫度。已完成的任务不参与（返回 null）。 */
export type DueUrgency = 'overdue' | 'soon' | 'later' | 'none';

export function dueUrgency(task: Task, nowMs: number, soonDays = 7): DueUrgency | null {
	if (task.done) return null;
	const due = parseApiTime(task.due_date);
	if (!due) return 'none';
	const t = due.getTime();
	if (t < nowMs) return 'overdue';
	if (t <= nowMs + soonDays * DAY_MS) return 'soon';
	return 'later';
}

export interface Summary {
	total: number;
	todo: number;
	doing: number;
	done: number;
	overdue: number;
	dueSoon: number;
	unassigned: number;
}

export function summarize(tasks: Task[], nowMs: number, soonDays = 7): Summary {
	const s: Summary = { total: 0, todo: 0, doing: 0, done: 0, overdue: 0, dueSoon: 0, unassigned: 0 };
	for (const task of tasks) {
		s.total += 1;
		s[statusOf(task)] += 1;
		const urg = dueUrgency(task, nowMs, soonDays);
		if (urg === 'overdue') s.overdue += 1;
		else if (urg === 'soon') s.dueSoon += 1;
		if (!task.done && (task.assignees ?? []).length === 0) s.unassigned += 1;
	}
	return s;
}

export interface PersonLoad {
	/** 用户 id；`0` 表示「未分配」这一合成行。 */
	id: number;
	name: string;
	open: number;
	overdue: number;
	done: number;
}

/**
 * 每个人的负载。多 assignee 的任务给每个人各记一次（和看板一致）。
 * 追加一行「未分配」(id 0)：未分配的积压是管理者要看的，不是"某个人"的负载。
 * 排序：未完成多的在前，其次逾期多，再按名字稳定。
 */
export function perPerson(tasks: Task[], nowMs: number): PersonLoad[] {
	const map = new Map<number, PersonLoad>();
	const bump = (id: number, name: string, task: Task) => {
		const row = map.get(id) ?? { id, name, open: 0, overdue: 0, done: 0 };
		if (task.done) row.done += 1;
		else {
			row.open += 1;
			if (dueUrgency(task, nowMs) === 'overdue') row.overdue += 1;
		}
		map.set(id, row);
	};
	for (const task of tasks) {
		const assignees = task.assignees ?? [];
		if (assignees.length === 0) bump(0, '未分配', task);
		else for (const a of assignees) bump(a.id, a.name?.trim() || a.username?.trim() || `用户#${a.id}`, task);
	}
	return [...map.values()].sort(
		(a, b) => b.open - a.open || b.overdue - a.overdue || a.name.localeCompare(b.name),
	);
}

export interface ProjectProgress {
	id: number;
	title: string;
	total: number;
	done: number;
	overdue: number;
	/** 完成率 0–100（整数）；total 为 0 时为 0。 */
	pct: number;
}

/**
 * 每个项目的进度。只统计真实项目（正 id）；任务落在未知项目上时归到 `项目#id`。
 * 排序：未完成（total-done）多的在前，让"还有很多没做"的项目冒头。
 */
export function perProject(tasks: Task[], projects: Project[], nowMs: number): ProjectProgress[] {
	const name = (id: number) => projects.find((p) => p.id === id)?.title ?? `项目#${id}`;
	const map = new Map<number, ProjectProgress>();
	for (const task of tasks) {
		const pid = task.project_id ?? 0;
		if (pid <= 0) continue;
		const row = map.get(pid) ?? { id: pid, title: name(pid), total: 0, done: 0, overdue: 0, pct: 0 };
		row.total += 1;
		if (task.done) row.done += 1;
		else if (dueUrgency(task, nowMs) === 'overdue') row.overdue += 1;
		map.set(pid, row);
	}
	const rows = [...map.values()];
	for (const r of rows) r.pct = r.total ? Math.round((r.done / r.total) * 100) : 0;
	return rows.sort((a, b) => b.total - b.done - (a.total - a.done) || b.total - a.total);
}

/** 逾期在前、即将到期其次，各自按到期日升序；已完成/无到期日排除。用于两个列表分区。 */
export function dueList(
	tasks: Task[],
	nowMs: number,
	which: 'overdue' | 'soon',
	soonDays = 7,
): Task[] {
	return tasks
		.filter((t) => dueUrgency(t, nowMs, soonDays) === which)
		.sort((a, b) => (parseApiTime(a.due_date)?.getTime() ?? 0) - (parseApiTime(b.due_date)?.getTime() ?? 0));
}

export type { BoardStatus };
