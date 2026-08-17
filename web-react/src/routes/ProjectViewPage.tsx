import { NavLink, useParams } from 'react-router-dom';

import { isViewKind, VIEW_KINDS, type ProjectView, type ViewKind } from '@/api/views';
import { FilterBar } from '@/features/tasks/FilterBar';
import { GanttView } from '@/features/tasks/GanttView';
import { KanbanView } from '@/features/tasks/KanbanView';
import { ListView } from '@/features/tasks/ListView';
import { TableView } from '@/features/tasks/TableView';
import { useProjectViews } from '@/features/views/queries';
import { parseRouteId } from '@/lib/route-params';
import { cn } from '@/lib/utils';

/**
 * 视图容器：List / Table / Kanban / Gantt 四合一（终稿 §4）。
 *
 * URL 用 **view kind** 而不是 view id（`/projects/12/kanban`）：可读、可手写、
 * 不随后端 id 变化。容器负责把 kind 解析成具体的 view 对象，
 * 后续按 id 取任务的端点（`/projects/{p}/views/{v}/tasks`）由各视图自己调。
 *
 * 实测（tester）：新建项目会自动带出四个 view，所以**没有"项目还没有视图"的空态**——
 * 真拿到空列表说明数据异常，如实报错，不静默渲染空壳。
 */
export function ProjectViewPage() {
	const params = useParams();
	const projectId = parseRouteId(params.projectId);
	const viewParam = params.view ?? '';

	// ⚠️ 路由是 /projects/:projectId/:view，像 /projects/new/list 这种也会匹配进来，
	// projectId 会是 "new"。不校验就会拿 NaN 去打接口。
	if (projectId === null) {
		return (
			<InvalidState title="无效的项目">
				地址里的项目 ID「{params.projectId}」不是有效的数字。
			</InvalidState>
		);
	}

	if (!isViewKind(viewParam)) {
		return (
			<InvalidState title="未知的视图">
				「{viewParam}」不是支持的视图类型，可用的有：{VIEW_KINDS.join(' / ')}。
			</InvalidState>
		);
	}

	return <ResolvedProjectView projectId={projectId} kind={viewParam} />;
}

function ResolvedProjectView({ projectId, kind }: { projectId: number; kind: ViewKind }) {
	const query = useProjectViews(projectId);

	const views = query.data?.items ?? [];
	const view = views.find((item) => item.view_kind === kind);

	return (
		<section className="flex h-full flex-col" data-testid="view-container">
			<header className="shrink-0 border-b bg-card px-6 py-4">
				<nav aria-label="视图切换" className="flex gap-1">
					{VIEW_KINDS.map((candidate) => (
						<NavLink
							key={candidate}
							data-testid={`view-tab-${candidate}`}
							to={`/projects/${projectId}/${candidate}`}
							className={({ isActive }) =>
								cn(
									'px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground',
									isActive && 'border-b-2 border-primary font-medium text-foreground',
								)
							}
						>
							{VIEW_KIND_LABELS[candidate]}
						</NavLink>
					))}
				</nav>

				{/* 筛选条件对四个视图都适用，放在容器里，切视图不丢条件（它在 URL 上） */}
				<FilterBar />
			</header>

			<div className="min-h-0 flex-1 overflow-y-auto p-6">
				{query.isPending ? <p className="text-sm text-muted-foreground">加载中…</p> : null}

				{query.isError ? (
					<p role="alert" className="text-sm text-xyz-red-6">
						{query.error.message}
					</p>
				) : null}

				{query.isSuccess && !view ? (
					<p role="alert" data-testid="missing-view" className="text-sm text-xyz-red-6">
						该项目没有{VIEW_KIND_LABELS[kind]}视图。新建项目本应自动带上四种视图，
						出现这个提示说明视图数据异常，请联系管理员。
					</p>
				) : null}

				{query.isSuccess && view ? (
					<ViewBody kind={kind} view={view} projectId={projectId} />
				) : null}
			</div>
		</section>
	);
}

const VIEW_KIND_LABELS: Record<ViewKind, string> = {
	list: '列表',
	gantt: '甘特图',
	table: '表格',
	kanban: '看板',
};

/** 四种 kind 走同一个容器，只有这里分叉。各视图由后续任务实现。 */
function ViewBody({
	kind,
	view,
	projectId,
}: {
	kind: ViewKind;
	view: ProjectView;
	projectId: number;
}) {
	switch (kind) {
		case 'gantt':
			return <GanttView projectId={projectId} viewId={view.id} />;
		case 'list':
			return <ListView projectId={projectId} viewId={view.id} />;
		case 'table':
			return <TableView projectId={projectId} viewId={view.id} />;
		case 'kanban':
			return <KanbanView projectId={projectId} viewId={view.id} />;
	}
}

function InvalidState({ title, children }: { title: string; children: React.ReactNode }) {
	return (
		<section className="p-6" data-testid="invalid-view-route">
			<h1 className="ink-heading text-2xl">{title}</h1>
			<p role="alert" className="mt-2 text-sm text-muted-foreground">
				{children}
			</p>
		</section>
	);
}
