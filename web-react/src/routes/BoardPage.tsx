import {
	DndContext,
	PointerSensor,
	useDraggable,
	useDroppable,
	useSensor,
	useSensors,
	type DragEndEvent,
} from '@dnd-kit/core';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import type { Task } from '@/api/tasks';
import { cn } from '@/lib/utils';
import { useProjects } from '@/features/projects/queries';
import {
	statusOf,
	useAllTasks,
	useReassignTask,
	useSetTaskStatus,
	type BoardStatus,
} from '@/features/board/queries';

/**
 * 全局看板（跨项目）。两种分组方式，顶部切换：
 *
 * - **按人**：列 = assignee（+「未分配」）。多 assignee 的任务在每个人的列里都出现。
 *   拖卡片到另一个人的列 = 改 assignee。
 * - **按状态**：列 = 待办 / 进行中 / 已完成（由 done + percent_done 推导，不是桶）。
 *   拖卡片到另一列 = 改状态（见 `features/board/queries.ts`）。
 *
 * 项目 / 人两个过滤器在两种模式下都可用；过滤在客户端做（project 不在 filter DSL 白名单）。
 */

type GroupBy = 'person' | 'status';

const STATUS_COLUMNS: { key: BoardStatus; label: string }[] = [
	{ key: 'todo', label: '待办' },
	{ key: 'doing', label: '进行中' },
	{ key: 'done', label: '已完成' },
];

interface Person {
	id: number;
	name: string;
}

function personName(a: NonNullable<Task['assignees']>[number]): string {
	return a.name?.trim() || a.username?.trim() || `用户#${a.id}`;
}

const encodeCard = (taskId: number, from: string) => `card:${taskId}:${from}`;
function decodeCard(id: string): { taskId: number; from: string } | null {
	const m = /^card:(\d+):(.+)$/.exec(id);
	return m ? { taskId: Number(m[1]), from: m[2] } : null;
}
const encodeColumn = (key: string) => `col:${key}`;
function decodeColumn(id: string): string | null {
	const m = /^col:(.+)$/.exec(id);
	return m ? m[1] : null;
}

export function BoardPage() {
	const tasksQuery = useAllTasks();
	const projectsQuery = useProjects();
	const reassign = useReassignTask();
	const setStatus = useSetTaskStatus();

	const [groupBy, setGroupBy] = useState<GroupBy>('person');
	const [selectedProjects, setSelectedProjects] = useState<Set<number>>(new Set());
	const [selectedUsers, setSelectedUsers] = useState<Set<number | 'none'>>(new Set());

	// 6px 才算拖拽，否则点开任务详情会被当成拖拽吞掉（同 KanbanView）
	const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

	const tasks = tasksQuery.data?.tasks ?? [];

	const realProjects = useMemo(
		() => (projectsQuery.data?.items ?? []).filter((p) => p.id > 0),
		[projectsQuery.data],
	);
	const projectName = (id: number | undefined) =>
		realProjects.find((p) => p.id === id)?.title ?? (id ? `项目#${id}` : '无项目');

	const people = useMemo<Person[]>(() => {
		const map = new Map<number, Person>();
		for (const task of tasks) {
			for (const a of task.assignees ?? []) map.set(a.id, { id: a.id, name: personName(a) });
		}
		return [...map.values()].sort((a, b) => a.name.localeCompare(b.name));
	}, [tasks]);

	const projectFilterOn = selectedProjects.size > 0;
	const userFilterOn = selectedUsers.size > 0;

	const projectFiltered = useMemo(
		() => tasks.filter((t) => !projectFilterOn || selectedProjects.has(t.project_id ?? -1)),
		[tasks, projectFilterOn, selectedProjects],
	);

	const matchesUserFilter = (t: Task): boolean => {
		if (!userFilterOn) return true;
		const assignees = t.assignees ?? [];
		if (assignees.length === 0) return selectedUsers.has('none');
		return assignees.some((a) => selectedUsers.has(a.id));
	};

	const hasUnassigned = projectFiltered.some((t) => (t.assignees ?? []).length === 0);

	// 列 + 每列的任务，随分组方式变化
	const { columns, tasksInColumn } = useMemo(() => {
		if (groupBy === 'status') {
			// 状态列固定三列都显示；人过滤在这里当**任务过滤**用
			const filtered = projectFiltered.filter(matchesUserFilter);
			return {
				columns: STATUS_COLUMNS,
				tasksInColumn: (key: string) => filtered.filter((t) => statusOf(t) === key),
			};
		}
		// 按人：列 = 选中的人（未选则全部有任务的人）+「未分配」
		const cols: { key: string; label: string }[] = [];
		for (const p of people) {
			if (userFilterOn && !selectedUsers.has(p.id)) continue;
			cols.push({ key: String(p.id), label: p.name });
		}
		const showNone = userFilterOn ? selectedUsers.has('none') : hasUnassigned;
		if (showNone) cols.push({ key: 'none', label: '未分配' });
		return {
			columns: cols,
			tasksInColumn: (key: string) =>
				projectFiltered.filter((t) =>
					key === 'none'
						? (t.assignees ?? []).length === 0
						: (t.assignees ?? []).some((a) => String(a.id) === key),
				),
		};
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [groupBy, projectFiltered, people, userFilterOn, selectedUsers, hasUnassigned]);

	function toggle<T>(set: Set<T>, value: T): Set<T> {
		const next = new Set(set);
		if (next.has(value)) next.delete(value);
		else next.add(value);
		return next;
	}

	function handleDragEnd(event: DragEndEvent) {
		const card = decodeCard(String(event.active.id));
		const to = event.over ? decodeColumn(String(event.over.id)) : null;
		if (!card || to === null || to === card.from) return;

		if (groupBy === 'status') {
			const task = tasks.find((t) => t.id === card.taskId);
			if (task) setStatus.mutate({ task, status: to as BoardStatus });
			return;
		}
		reassign.mutate({
			taskId: card.taskId,
			fromUserId: card.from === 'none' ? null : Number(card.from),
			toUserId: to === 'none' ? null : Number(to),
		});
	}

	if (tasksQuery.isPending) {
		return <p className="p-6 text-sm text-muted-foreground">加载中…</p>;
	}
	if (tasksQuery.isError) {
		return (
			<p role="alert" className="p-6 text-sm text-xyz-red-6">
				{tasksQuery.error.message}
			</p>
		);
	}

	const mutationError = groupBy === 'status' ? setStatus.error : reassign.error;

	return (
		<section className="flex h-full flex-col gap-4 p-6" data-testid="board-page">
			<header className="flex flex-wrap items-center gap-3">
				<h1 className="ink-heading text-2xl">看板</h1>
				{/* 分组方式切换 */}
				<div className="flex rounded-md border border-border p-0.5" data-testid="board-groupby">
					{(
						[
							['person', '按人'],
							['status', '按状态'],
						] as const
					).map(([key, label]) => (
						<button
							key={key}
							type="button"
							data-testid="board-groupby-option"
							data-active={groupBy === key}
							onClick={() => setGroupBy(key)}
							className={cn(
								'rounded px-3 py-1 text-sm transition-colors',
								groupBy === key
									? 'bg-primary text-primary-foreground'
									: 'text-muted-foreground hover:bg-accent',
							)}
						>
							{label}
						</button>
					))}
				</div>
				{tasksQuery.data?.truncated ? (
					<span className="text-xs text-xyz-orange-6" data-testid="board-truncated">
						任务较多，仅展示前 {tasks.length} 条
					</span>
				) : null}
			</header>

			{/* 过滤器：项目 + 人，多选，空=全部 */}
			<div className="flex flex-col gap-2" data-testid="board-filters">
				<FilterRow label="项目">
					{realProjects.map((p) => (
						<Chip
							key={p.id}
							active={selectedProjects.has(p.id)}
							onClick={() => setSelectedProjects((s) => toggle(s, p.id))}
							testid="board-project-chip"
						>
							{p.title}
						</Chip>
					))}
				</FilterRow>
				<FilterRow label="人">
					{people.map((p) => (
						<Chip
							key={p.id}
							active={selectedUsers.has(p.id)}
							onClick={() => setSelectedUsers((s) => toggle(s, p.id))}
							testid="board-user-chip"
						>
							{p.name}
						</Chip>
					))}
					<Chip
						active={selectedUsers.has('none')}
						onClick={() => setSelectedUsers((s) => toggle(s, 'none'))}
						testid="board-user-chip"
					>
						未分配
					</Chip>
				</FilterRow>
			</div>

			{mutationError ? (
				<p role="alert" data-testid="board-mutation-error" className="text-sm text-xyz-red-6">
					{mutationError.message}
				</p>
			) : null}

			{columns.length === 0 ? (
				<p data-testid="board-empty" className="text-sm text-muted-foreground">
					没有匹配的任务。
				</p>
			) : (
				<DndContext sensors={sensors} onDragEnd={handleDragEnd}>
					<div className="flex flex-1 gap-3 overflow-x-auto pb-2">
						{columns.map((col) => (
							<Column key={col.key} column={col} tasks={tasksInColumn(col.key)}>
								{(task) => (
									<TaskCard
										key={`${task.id}:${col.key}`}
										task={task}
										from={col.key}
										projectName={projectName(task.project_id)}
									/>
								)}
							</Column>
						))}
					</div>
				</DndContext>
			)}
		</section>
	);
}

function FilterRow({ label, children }: { label: string; children: React.ReactNode }) {
	return (
		<div className="flex flex-wrap items-center gap-1.5">
			<span className="w-8 shrink-0 text-xs text-muted-foreground">{label}</span>
			{children}
		</div>
	);
}

function Chip({
	active,
	onClick,
	testid,
	children,
}: {
	active: boolean;
	onClick: () => void;
	testid: string;
	children: React.ReactNode;
}) {
	return (
		<button
			type="button"
			data-testid={testid}
			data-active={active}
			onClick={onClick}
			className={cn(
				'rounded-full border px-2.5 py-0.5 text-xs transition-colors',
				active
					? 'border-primary/40 bg-accent text-primary'
					: 'border-border text-muted-foreground hover:bg-accent',
			)}
		>
			{children}
		</button>
	);
}

function Column({
	column,
	tasks,
	children,
}: {
	column: { key: string; label: string };
	tasks: Task[];
	children: (task: Task) => React.ReactNode;
}) {
	const { setNodeRef, isOver } = useDroppable({ id: encodeColumn(column.key) });
	return (
		<div
			ref={setNodeRef}
			data-testid="board-column"
			data-column-key={column.key}
			className={cn(
				'ink-card flex w-64 shrink-0 flex-col',
				isOver && 'ring-2 ring-primary',
			)}
		>
			<div className="flex items-center justify-between border-b border-border px-3 py-2">
				<span className="ink-heading truncate text-sm">{column.label}</span>
				<span className="text-xs text-muted-foreground">{tasks.length}</span>
			</div>
			<div className="flex min-h-16 flex-1 flex-col gap-2 overflow-y-auto p-2">
				{tasks.map((task) => children(task))}
			</div>
		</div>
	);
}

function TaskCard({
	task,
	from,
	projectName,
}: {
	task: Task;
	from: string;
	projectName: string;
}) {
	const { setNodeRef, listeners, attributes, isDragging } = useDraggable({
		id: encodeCard(task.id, from),
	});
	return (
		<div
			ref={setNodeRef}
			{...listeners}
			{...attributes}
			data-testid="board-card"
			data-task-id={task.id}
			className={cn(
				'rounded-md border border-border bg-card p-2 text-sm shadow-sm transition hover:shadow',
				task.done && 'opacity-60',
				isDragging && 'opacity-50',
			)}
		>
			<Link
				to={`/tasks/${task.id}`}
				className="block font-medium text-foreground hover:underline"
				onClick={(e) => e.stopPropagation()}
			>
				{task.title}
			</Link>
			<div className="mt-1 truncate text-xs text-muted-foreground">{projectName}</div>
		</div>
	);
}
