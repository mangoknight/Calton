import { useAllTasks } from '@/features/board/queries';
import { useProjects } from '@/features/projects/queries';
import { dueList } from '@/features/dashboard/metrics';
import { TaskList } from './DashboardPage';

/**
 * 即将到期：跨项目的逾期 + 未来 7 天到期清单，按到期日升序。
 *
 * 之前这条路由是占位页。数据与管理面板同源（全局 `GET /tasks` 聚合），
 * 这里给全量、不截断，专门盯 deadline。
 */
export function DueSoonPage() {
	const tasksQuery = useAllTasks();
	const projectsQuery = useProjects();

	const now = Date.now();
	const tasks = tasksQuery.data?.tasks ?? [];
	const projects = (projectsQuery.data?.items ?? []).filter((p) => p.id > 0);
	const projectName = (id: number | undefined) =>
		projects.find((p) => p.id === id)?.title ?? (id ? `项目#${id}` : '无项目');
	const overdue = dueList(tasks, now, 'overdue');
	const soon = dueList(tasks, now, 'soon');

	return (
		<section className="flex h-full flex-col gap-5 overflow-y-auto p-6" data-testid="due-soon-page">
			<h1 className="text-xl font-semibold text-foreground">即将到期</h1>

			{tasksQuery.isPending ? (
				<p className="text-sm text-muted-foreground">加载中…</p>
			) : tasksQuery.isError ? (
				<p role="alert" className="text-sm text-red-600">
					{tasksQuery.error.message}
				</p>
			) : (
				<>
					<div className="rounded-lg border border-xyz-gray-3 bg-white">
				<div className="flex items-baseline justify-between border-b border-xyz-gray-3 px-4 py-2.5">
					<h2 className="text-sm font-medium text-red-600">逾期</h2>
					<span className="text-xs text-muted-foreground">{overdue.length} 条</span>
				</div>
				<div className="p-4">
					<TaskList tasks={overdue} tone="red" projectName={projectName} empty="没有逾期任务 🎉" />
				</div>
			</div>

			<div className="rounded-lg border border-xyz-gray-3 bg-white">
				<div className="flex items-baseline justify-between border-b border-xyz-gray-3 px-4 py-2.5">
					<h2 className="text-sm font-medium text-xyz-orange-6">未来 7 天</h2>
					<span className="text-xs text-muted-foreground">{soon.length} 条</span>
				</div>
					<div className="p-4">
						<TaskList tasks={soon} tone="orange" projectName={projectName} empty="7 天内没有到期任务" />
					</div>
				</div>
				</>
			)}
		</section>
	);
}
