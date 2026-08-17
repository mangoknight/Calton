import { Link, useSearchParams } from 'react-router-dom';

import type { Task } from '@/api/tasks';
import { Button } from '@/components/ui/button';
import { Pagination } from '@/components/ui/pagination';
import { formatApiDate } from '@/lib/datetime';
import { parsePageParam } from '@/lib/page-param';
import { cn } from '@/lib/utils';
import { TaskQueryError } from './FilterError';
import { toFilterQuery, useFilterParam } from './filter-param';
import { QuickAddTask } from './QuickAddTask';
import { useViewTasks } from './queries';

/**
 * List 视图（F05b）。由视图容器解析出 view 之后挂进来。
 *
 * 分页页码走 URL query（`?page=2`），见 `lib/page-param.ts`。
 * 切换视图时容器的 NavLink 不带 query，页码自然回到第 1 页 —— 这是想要的：
 * 列表第 3 页的页码放到看板上没有意义。
 */
export function ListView({ projectId, viewId }: { projectId: number; viewId: number }) {
	const [searchParams, setSearchParams] = useSearchParams();
	const page = parsePageParam(searchParams.get('page'));

	const { filter } = useFilterParam();
	const query = useViewTasks(projectId, viewId, { page, ...toFilterQuery(filter) });

	function goToPage(next: number) {
		const params = new URLSearchParams(searchParams);
		// 第 1 页不写进 URL，保持链接干净（也让"回到第一页"和初次进入是同一个地址）
		if (next <= 1) params.delete('page');
		else params.set('page', String(next));
		setSearchParams(params);
	}

	if (query.isPending) {
		return <p className="text-sm text-muted-foreground">加载中…</p>;
	}

	if (query.isError) {
		return <TaskQueryError error={query.error} />;
	}

	const { items, resultCount, totalPages } = query.data;

	return (
		<div className="flex h-full flex-col gap-3" data-testid="list-view">
			{/* 建任务入口：放列表最上方，空态/分页下都可见 */}
			<QuickAddTask projectId={projectId} />

			{items.length === 0 ? (
				<EmptyState page={page} onBackToFirstPage={() => goToPage(1)} />
			) : (
				<ul className="min-h-0 flex-1 divide-border divide-y overflow-y-auto" data-testid="task-list">
					{items.map((task) => (
						<TaskRow key={task.id} task={task} />
					))}
				</ul>
			)}

			<Pagination
				page={page}
				totalPages={totalPages}
				resultCount={resultCount}
				onPageChange={goToPage}
				// keepPreviousData 下翻页时 isPending 是 false，用 isFetching 才拦得住连点
				busy={query.isFetching}
			/>
		</div>
	);
}

/**
 * 两种"没有内容"要分开说：
 * - 项目里一条任务都没有 → 这是正常的新项目，给建任务的引导；
 * - 翻过了最后一页（手改 URL 或数据在翻页间变少）→ 这是走错地方了，给回第一页的出口。
 * 混成同一句会让第二种情况看起来像"任务全没了"。
 */
function EmptyState({ page, onBackToFirstPage }: { page: number; onBackToFirstPage: () => void }) {
	if (page > 1) {
		return (
			<div data-testid="list-empty-page" className="flex flex-1 flex-col items-start gap-3 py-10">
				<p className="text-sm text-muted-foreground">第 {page} 页没有任务。</p>
				<Button
					type="button"
					variant="outline"
					size="sm"
					data-testid="back-to-first-page"
					onClick={onBackToFirstPage}
				>
					回到第一页
				</Button>
			</div>
		);
	}

	return (
		<div data-testid="list-empty" className="flex flex-1 flex-col items-start gap-1 py-10">
			<p className="text-sm font-medium text-foreground">这个项目还没有任务</p>
			<p className="text-sm text-muted-foreground">新建一条任务，它会出现在这里。</p>
		</div>
	);
}

const PRIORITY_LABELS: Record<number, string> = {
	1: '低',
	2: '中',
	3: '高',
	4: '紧急',
	5: '马上做',
};

function TaskRow({ task }: { task: Task }) {
	// ⚠️ 到期日的零值是 "0001-01-01T00:00:00Z"，直接 new Date() 会渲染成公元 1 年
	const dueDate = formatApiDate(task.due_date);
	const priority = task.priority ?? 0;

	return (
		<li
			className="flex items-center gap-3 rounded-md px-2 py-2 transition-colors hover:bg-accent/60"
			data-testid="task-row"
			data-task-id={task.id}
		>
			{/* 勾选框此处只读：改 done 是 F08a 的事，这里给个假的可点控件会让人以为已经能改 */}
			<input
				type="checkbox"
				checked={task.done ?? false}
				readOnly
				disabled
				data-testid={`task-done-${task.id}`}
				data-done={task.done ? 'true' : 'false'}
				aria-label={task.done ? '已完成' : '未完成'}
				className="size-4 shrink-0"
			/>

			<Link
				to={`/tasks/${task.id}`}
				className={cn(
					'min-w-0 flex-1 truncate text-sm hover:underline',
					task.done ? 'text-muted-foreground line-through' : 'text-foreground',
				)}
			>
				{task.identifier ? (
					<span className="mr-2 text-xs text-muted-foreground">{task.identifier}</span>
				) : null}
				{task.title}
			</Link>

			{task.labels?.length ? (
				<span className="flex shrink-0 gap-1" data-testid="task-labels">
					{task.labels.map((label) => (
						<span
							key={label.id}
							className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground"
						>
							{label.title}
						</span>
					))}
				</span>
			) : null}

			{priority > 0 && PRIORITY_LABELS[priority] ? (
				<span className="shrink-0 text-xs text-muted-foreground" data-testid="task-priority">
					{PRIORITY_LABELS[priority]}
				</span>
			) : null}

			{dueDate ? (
				<time
					dateTime={task.due_date}
					className="shrink-0 text-xs text-muted-foreground"
					data-testid="task-due-date"
				>
					{dueDate}
				</time>
			) : null}
		</li>
	);
}
