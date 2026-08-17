import { Link } from 'react-router-dom';

import type { Task } from '@/api/tasks';
import { cn } from '@/lib/utils';
import { formatApiDate } from '@/lib/datetime';
import { useAllTasks } from '@/features/board/queries';
import { useProjects } from '@/features/projects/queries';
import {
	dueList,
	perPerson,
	perProject,
	summarize,
	type PersonLoad,
	type ProjectProgress,
} from '@/features/dashboard/metrics';

/**
 * 管理面板：跨项目的全局视角 —— 指标 / 人员负载 / 项目进度 / 逾期与即将到期。
 *
 * 与"概览"(个人今日/本周/收藏) 分工不同：这里是**管理者看全局**。
 * 全部由全局 `GET /tasks` 客户端聚合而来（纯函数在 `features/dashboard/metrics.ts`，
 * 有单测），不依赖任何后端新接口。
 */
export function DashboardPage() {
	const tasksQuery = useAllTasks();
	const projectsQuery = useProjects();

	if (tasksQuery.isPending) {
		return <p className="p-6 text-sm text-muted-foreground">加载中…</p>;
	}
	if (tasksQuery.isError) {
		return (
			<p role="alert" className="p-6 text-sm text-red-600">
				{tasksQuery.error.message}
			</p>
		);
	}

	const now = Date.now();
	const tasks = tasksQuery.data.tasks;
	const projects = (projectsQuery.data?.items ?? []).filter((p) => p.id > 0);
	const s = summarize(tasks, now);
	const people = perPerson(tasks, now);
	const projectRows = perProject(tasks, projects, now);
	const overdue = dueList(tasks, now, 'overdue');
	const soon = dueList(tasks, now, 'soon');
	const projectName = (id: number | undefined) =>
		projects.find((p) => p.id === id)?.title ?? (id ? `项目#${id}` : '无项目');

	return (
		<section className="flex h-full flex-col gap-5 overflow-y-auto p-6" data-testid="dashboard-page">
			<header className="flex flex-wrap items-center gap-3">
				<h1 className="ink-heading text-2xl">管理面板</h1>
				{tasksQuery.data.truncated ? (
					<span className="text-xs text-xyz-orange-6">任务较多，统计基于前 {tasks.length} 条</span>
				) : null}
			</header>

			{/* 指标卡 */}
			<div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6" data-testid="dashboard-metrics">
				<Metric label="总任务" value={s.total} />
				<Metric label="待办" value={s.todo} />
				<Metric label="进行中" value={s.doing} tone="blue" />
				<Metric label="已完成" value={s.done} tone="green" />
				<Metric label="逾期" value={s.overdue} tone="red" />
				<Metric label="7 天内到期" value={s.dueSoon} tone="orange" />
			</div>

			<div className="grid gap-5 lg:grid-cols-2">
				{/* 人员负载 */}
				<Panel title="人员负载" hint="未完成 / 逾期 / 已完成，多指派各记一次">
					{people.length === 0 ? (
						<Empty>没有任务。</Empty>
					) : (
						<table className="w-full text-sm" data-testid="dashboard-workload">
							<thead>
								<tr className="text-left text-xs text-muted-foreground">
									<th className="pb-1 font-normal">成员</th>
									<th className="pb-1 text-right font-normal">未完成</th>
									<th className="pb-1 text-right font-normal">逾期</th>
									<th className="pb-1 text-right font-normal">已完成</th>
								</tr>
							</thead>
							<tbody>
								{people.map((p) => (
									<WorkloadRow key={p.id} p={p} max={people[0].open || 1} />
								))}
							</tbody>
						</table>
					)}
				</Panel>

				{/* 项目进度 */}
				<Panel title="项目进度" hint="完成率与逾期数">
					{projectRows.length === 0 ? (
						<Empty>没有任务。</Empty>
					) : (
						<table className="w-full text-sm" data-testid="dashboard-projects">
							<tbody>
								{projectRows.map((r) => (
									<ProjectRow key={r.id} r={r} />
								))}
							</tbody>
						</table>
					)}
				</Panel>

				{/* 逾期任务 */}
				<Panel title="逾期任务" hint={`${overdue.length} 条`}>
					<TaskList tasks={overdue.slice(0, 12)} tone="red" projectName={projectName} empty="没有逾期任务 🎉" />
				</Panel>

				{/* 即将到期 */}
				<Panel title="即将到期（7 天）" hint={`${soon.length} 条`}>
					<TaskList tasks={soon.slice(0, 12)} tone="orange" projectName={projectName} empty="7 天内没有到期任务" />
				</Panel>
			</div>
		</section>
	);
}

const TONE: Record<string, string> = {
	blue: 'text-primary',
	green: 'text-green-600',
	red: 'text-red-600',
	orange: 'text-xyz-orange-6',
	default: 'text-foreground',
};

function Metric({ label, value, tone = 'default' }: { label: string; value: number; tone?: string }) {
	return (
		<div className="ink-card p-4" data-testid="dashboard-metric">
			<div className="text-xs text-muted-foreground">{label}</div>
			<div className={cn('ink-figure mt-1 text-3xl', TONE[tone])}>{value}</div>
		</div>
	);
}

function Panel({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
	return (
		<div className="ink-card flex flex-col">
			<div className="flex items-baseline justify-between border-b border-border px-4 py-2.5">
				<h2 className="ink-heading text-base">{title}</h2>
				{hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
			</div>
			<div className="p-4">{children}</div>
		</div>
	);
}

function Empty({ children }: { children: React.ReactNode }) {
	return <p className="text-sm text-muted-foreground">{children}</p>;
}

function WorkloadRow({ p, max }: { p: PersonLoad; max: number }) {
	return (
		<tr className="border-t border-border" data-testid="workload-row" data-user-id={p.id}>
			<td className="py-1.5">
				<div className="truncate text-foreground">{p.name}</div>
				<div className="mt-1 h-1.5 w-full rounded-full bg-muted">
					<div
						className="h-1.5 rounded-full bg-primary"
						style={{ width: `${Math.round((p.open / max) * 100)}%` }}
					/>
				</div>
			</td>
			<td className="pl-2 text-right align-top text-foreground">{p.open}</td>
			<td className={cn('pl-2 text-right align-top', p.overdue ? 'text-red-600' : 'text-muted-foreground')}>
				{p.overdue}
			</td>
			<td className="pl-2 text-right align-top text-muted-foreground">{p.done}</td>
		</tr>
	);
}

function ProjectRow({ r }: { r: ProjectProgress }) {
	return (
		<tr className="border-t border-border first:border-t-0" data-testid="project-row" data-project-id={r.id}>
			<td className="py-2">
				<div className="flex items-baseline justify-between">
					<span className="truncate text-foreground">{r.title}</span>
					<span className="pl-2 text-xs text-muted-foreground">
						{r.done}/{r.total}
						{r.overdue ? <span className="ml-2 text-red-600">逾期 {r.overdue}</span> : null}
					</span>
				</div>
				<div className="mt-1 flex items-center gap-2">
					<div className="h-1.5 flex-1 rounded-full bg-muted">
						<div className="h-1.5 rounded-full bg-green-500" style={{ width: `${r.pct}%` }} />
					</div>
					<span className="w-9 text-right text-xs text-muted-foreground">{r.pct}%</span>
				</div>
			</td>
		</tr>
	);
}

export function TaskList({
	tasks,
	tone,
	projectName,
	empty,
}: {
	tasks: Task[];
	tone: 'red' | 'orange';
	projectName: (id: number | undefined) => string;
	empty: string;
}) {
	if (tasks.length === 0) return <Empty>{empty}</Empty>;
	return (
		<ul className="space-y-1.5" data-testid="due-list">
			{tasks.map((task) => (
				<li key={task.id} className="flex items-baseline justify-between gap-2 text-sm" data-testid="due-row">
					<Link
						to={`/tasks/${task.id}`}
						className="min-w-0 flex-1 truncate text-foreground hover:underline"
					>
						{task.title}
					</Link>
					<span className="shrink-0 text-xs text-muted-foreground">{projectName(task.project_id)}</span>
					<span className={cn('w-20 shrink-0 text-right text-xs', tone === 'red' ? 'text-red-600' : 'text-xyz-orange-6')}>
						{formatApiDate(task.due_date)}
					</span>
				</li>
			))}
		</ul>
	);
}
