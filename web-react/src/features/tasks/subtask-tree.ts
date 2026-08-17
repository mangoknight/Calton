import type { Task } from '@/api/tasks';

/**
 * 扁平任务列表 → 子任务树。
 *
 * 边由 `related_tasks` 给出：父任务在 `subtask` 里列出它的子任务，
 * 子任务在 `parenttask` 里回指父任务。和项目树一样，后端**不保证这些边无环**
 *（subtask/parenttask 是用户手建的关系，A 是 B 的子任务、B 又是 A 的子任务这种
 * 脏数据完全可能），所以在**建树阶段**就把环剔掉，渲染层可以放心递归。
 *
 * 判定「谁是这棵树的顶」：一个任务若没有**在本集合内**的父任务，就是顶层
 *（parenttask 为空，或它的父任务在别的项目、不在这次拉取的集合里）。这样跨项目的
 * 子任务不会凭空消失，而是平铺在顶层。剩下从任何顶层都到不了的（纯在环里的）
 * 也不静默丢，追加为顶层节点，避免用户觉得任务"少了"。
 */

export interface TaskNode {
	task: Task;
	children: TaskNode[];
	depth: number;
}

export interface TaskTree {
	roots: TaskNode[];
}

function relationIds(task: Task, kind: string): number[] {
	const related = task.related_tasks?.[kind];
	if (!related) return [];
	return related.map((t) => t.id);
}

/** 有没有一个**在集合内**的父任务。跨集合的父任务不算 —— 那种当顶层处理。 */
function hasParentInSet(task: Task, byId: Map<number, Task>): boolean {
	return relationIds(task, 'parenttask').some((id) => byId.has(id));
}

export function buildTaskTree(input: Task[]): TaskTree {
	const byId = new Map<number, Task>();
	for (const task of input) byId.set(task.id, task);

	const rootTasks = input.filter((task) => !hasParentInSet(task, byId));

	// visited 兼作「谁被挂进树了」的记录：环内节点永远走不到，最后补挂到顶层。
	const visited = new Set<number>();

	function expand(task: Task, depth: number): TaskNode {
		visited.add(task.id);
		const children = relationIds(task, 'subtask')
			.map((id) => byId.get(id))
			.filter((child): child is Task => child !== undefined && !visited.has(child.id))
			.map((child) => expand(child, depth + 1));
		return { task, children, depth };
	}

	const roots = rootTasks.map((task) => expand(task, 0));

	// 纯在环里、没有集合内顶层祖先的任务：平铺到顶层，不丢。
	for (const task of input) {
		if (!visited.has(task.id)) roots.push(expand(task, 0));
	}

	sortTree(roots);
	return { roots };
}

/** 未完成在前、完成在后；再按 index/position、最后按 id。 */
function sortTree(nodes: TaskNode[]): void {
	nodes.sort((a, b) => {
		const doneDiff = Number(a.task.done ?? false) - Number(b.task.done ?? false);
		if (doneDiff !== 0) return doneDiff;
		const idxDiff = (a.task.index ?? 0) - (b.task.index ?? 0);
		if (idxDiff !== 0) return idxDiff;
		return a.task.id - b.task.id;
	});
	for (const node of nodes) sortTree(node.children);
}
