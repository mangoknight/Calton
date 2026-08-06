import { useCallback, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { Pagination } from '@/components/ui/pagination';
import { parsePageParam } from '@/lib/page-param';
import {
	parseSortParam,
	serializeSortParam,
	toggleSort,
	toSortQuery,
	type SortSpec,
} from '@/lib/table-sort';
import { useTranslation } from '@/i18n/context';
import { cn } from '@/lib/utils';
import {
	DEFAULT_VISIBLE_COLUMNS,
	loadVisibleColumns,
	saveVisibleColumns,
	TASK_COLUMNS,
	type TaskColumn,
} from './columns';
import { TaskQueryError } from './FilterError';
import { toFilterQuery, useFilterParam } from './filter-param';
import { useViewTasks } from './queries';

/**
 * Table 视图（F06）。与 List 视图共用 `useViewTasks` 与分页控件，
 * 区别只在多列展示、多列排序、列显示配置。
 *
 * 排序状态放 URL（`?sort=due_date:asc,priority:desc`），列显示放 localStorage：
 * 排序是"我想让你看这个顺序"，该跟着链接走；列显示是个人偏好，不该强加给收链接的人。
 */
export function TableView({ projectId, viewId }: { projectId: number; viewId: number }) {
	const [searchParams, setSearchParams] = useSearchParams();
	const page = parsePageParam(searchParams.get('page'));
	const sort = parseSortParam(searchParams.get('sort'));

	const [visibleIds, setVisibleIds] = useState<string[]>(() =>
		typeof window === 'undefined'
			? DEFAULT_VISIBLE_COLUMNS
			: loadVisibleColumns(window.localStorage),
	);

	const { filter } = useFilterParam();
	const query = useViewTasks(projectId, viewId, {
		page,
		...toSortQuery(sort),
		...toFilterQuery(filter),
	});

	const updateParams = useCallback(
		(mutate: (params: URLSearchParams) => void) => {
			const params = new URLSearchParams(searchParams);
			mutate(params);
			setSearchParams(params);
		},
		[searchParams, setSearchParams],
	);

	function goToPage(next: number) {
		updateParams((params) => {
			if (next <= 1) params.delete('page');
			else params.set('page', String(next));
		});
	}

	function onHeaderClick(column: TaskColumn) {
		if (!column.sortField) return;
		const next = toggleSort(sort, column.sortField);
		updateParams((params) => {
			const serialized = serializeSortParam(next);
			if (serialized) params.set('sort', serialized);
			else params.delete('sort');
			// 换了排序，原来的第 N 页没有意义了 —— 不回第 1 页的话，
			// 用户会看到"排序变了但还在第 3 页"的一堆陌生数据
			params.delete('page');
		});
	}

	function toggleColumn(id: string) {
		const hiding = visibleIds.includes(id);
		const next = hiding ? visibleIds.filter((item) => item !== id) : [...visibleIds, id];

		// 全部关掉会得到一个没有任何列的表格，留最后一列不许关
		if (next.length === 0) return;

		setVisibleIds(next);
		// 副作用放在这里而不是 setState 的 updater 里：updater 在 StrictMode 下会跑两次
		saveVisibleColumns(window.localStorage, next);

		if (!hiding) return;

		/**
		 * ★ 藏起一个**正在参与排序**的列时，要把它的排序一并摘掉。
		 *
		 * 否则 `sort_by` 照发不误，而 UI 上没有任何出口能取消它 —— 那一列的列头
		 * 已经不在页面上了，用户只能手改 URL。这跟"不自作主张追加 id 兜底"
		 * 防的是同一个问题的两面：那边防"看到自己没点过的排序"，
		 * 这边防"看不到正在生效的排序"。
		 */
		const column = TASK_COLUMNS.find((c) => c.id === id);
		if (!column?.sortField) return;
		if (!sort.some((spec) => spec.field === column.sortField)) return;

		const remaining = sort.filter((spec) => spec.field !== column.sortField);
		updateParams((params) => {
			const serialized = serializeSortParam(remaining);
			if (serialized) params.set('sort', serialized);
			else params.delete('sort');
			params.delete('page');
		});
	}

	// 渲染顺序始终由 TASK_COLUMNS 的定义顺序决定，与 visibleIds 里的先后无关 ——
	// 列的先后是版面设计，不该随用户勾选的先后跳来跳去。
	const columns = TASK_COLUMNS.filter((column) => visibleIds.includes(column.id));

	return (
		<div className="flex h-full flex-col gap-3" data-testid="table-view">
			<ColumnPicker visibleIds={visibleIds} onToggle={toggleColumn} />

			{query.isPending ? <p className="text-sm text-muted-foreground">加载中…</p> : null}

			{query.isError ? <TaskQueryError error={query.error} /> : null}

			{query.isSuccess ? (
				<>
					{/*
					  这一层是表格**自己的**滚动容器：横向滚动与 sticky 表头都相对它发生。
					  它能成为滚动容器的前提是外壳把高度收住了（`AppShell` 的 `h-screen`）——
					  外壳一旦改回 `min-h-screen`，这里的 clientHeight 会等于 scrollHeight，
					  于是它退化成一个普通 div，sticky 静默失效。见 `e2e/test_table_layout.py`。
					*/}
					<div className="min-h-0 flex-1 overflow-auto" data-testid="table-scroll">
						<table className="w-full text-sm" data-testid="task-table">
							<thead className="sticky top-0 bg-card">
								<tr className="border-b text-left">
									{columns.map((column) => (
										<HeaderCell
											key={column.id}
											column={column}
											sort={sort}
											onClick={() => onHeaderClick(column)}
										/>
									))}
								</tr>
							</thead>
							<tbody className="divide-y">
								{query.data.items.map((task) => (
									<tr key={task.id} data-testid="task-table-row" data-task-id={task.id}>
										{columns.map((column) => (
											<td
												key={column.id}
												data-column={column.id}
												className={cn(
													'px-3 py-2 align-top',
													column.align === 'right' && 'text-right',
													task.done && 'text-muted-foreground',
												)}
											>
												{column.id === 'title' ? (
													<Link to={`/tasks/${task.id}`} className="hover:underline">
														{column.render(task)}
													</Link>
												) : (
													column.render(task)
												)}
											</td>
										))}
									</tr>
								))}
							</tbody>
						</table>

						{query.data.items.length === 0 ? (
							<p data-testid="table-empty" className="py-10 text-sm text-muted-foreground">
								{page > 1 ? `第 ${page} 页没有任务。` : '这个项目还没有任务。'}
							</p>
						) : null}
					</div>

					<Pagination
						page={page}
						totalPages={query.data.totalPages}
						resultCount={query.data.resultCount}
						onPageChange={goToPage}
						busy={query.isFetching}
					/>
				</>
			) : null}
		</div>
	);
}

const DIRECTION_MARK = { asc: '↑', desc: '↓' } as const;

function HeaderCell({
	column,
	sort,
	onClick,
}: {
	column: TaskColumn;
	sort: SortSpec[];
	onClick: () => void;
}) {
	const t = useTranslation();
	const index = sort.findIndex((spec) => spec.field === column.sortField);
	const spec = index === -1 ? null : sort[index]!;

	// aria-sort 是屏幕阅读器唯一能读到的排序状态，别只靠那个箭头字符
	const ariaSort = spec ? (spec.direction === 'asc' ? 'ascending' : 'descending') : undefined;

	// ⚠️ `whitespace-nowrap` 是**照抄上游**，不是我们的排版偏好：
	// `frontend/src/components/project/views/ProjectTable.vue` 的 scoped 样式里
	// 唯一一条 white-space 规则就是 `.table th { white-space: nowrap; }`。
	//
	// 不加的话实测「Due Date」「Start Date」「End Date」三个列头各折成两行
	// （列窄、标签两个词），而上游是一行。守它的是 e2e/test_table_layout.py。
	if (!column.sortField) {
		return (
			<th
				scope="col"
				data-column={column.id}
				className="whitespace-nowrap px-3 py-2 font-medium text-muted-foreground"
			>
				{t(column.labelKey)}
			</th>
		);
	}

	return (
		<th
			scope="col"
			aria-sort={ariaSort}
			data-column={column.id}
			className={cn(
				'whitespace-nowrap px-3 py-2 font-medium',
				column.align === 'right' && 'text-right',
			)}
		>
			<button
				type="button"
				onClick={onClick}
				className="inline-flex items-center gap-1 hover:text-foreground"
			>
				{t(column.labelKey)}
				{spec ? (
					<span data-testid={`sort-marker-${column.id}`} className="text-xs text-primary">
						{DIRECTION_MARK[spec.direction]}
						{/* 多列排序时标出第几序 —— 只有箭头的话用户看不出谁是主序 */}
						{sort.length > 1 ? <sup>{index + 1}</sup> : null}
					</span>
				) : null}
			</button>
		</th>
	);
}

/** 用 <details> 而不是自造下拉：原生的键盘与焦点行为已经对了，也不必引 Radix 新依赖。 */
function ColumnPicker({
	visibleIds,
	onToggle,
}: {
	visibleIds: string[];
	onToggle: (id: string) => void;
}) {
	const t = useTranslation();

	return (
		<details className="shrink-0" data-testid="column-picker">
			<summary className="inline-flex cursor-pointer list-none items-center text-sm text-muted-foreground hover:text-foreground">
				列配置（{visibleIds.length}/{TASK_COLUMNS.length}）
			</summary>
			<div className="mt-2 flex flex-wrap gap-x-4 gap-y-2 rounded-md border bg-card p-3">
				{TASK_COLUMNS.map((column) => (
					<label key={column.id} className="inline-flex items-center gap-2 text-sm">
						<input
							type="checkbox"
							data-testid={`column-toggle-${column.id}`}
							checked={visibleIds.includes(column.id)}
							onChange={() => onToggle(column.id)}
							className="size-4"
						/>
						{t(column.labelKey)}
					</label>
				))}
			</div>
		</details>
	);
}
