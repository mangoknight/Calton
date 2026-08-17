import { Link } from 'react-router-dom';

import type { Task } from '@/api/tasks';
import { parseApiTime } from '@/lib/datetime';
import { cn } from '@/lib/utils';
import { TaskQueryError } from './FilterError';
import { toFilterQuery, useFilterParam } from './filter-param';
import { useViewTasks } from './queries';

/**
 * 甘特图视图（P3）。由视图容器解析出 view 之后挂进来，取数范式与 List 视图一致。
 *
 * 时间轴是纯 CSS/div 实现（CSP 禁外链，不引第三方甘特库）：每个任务一行，
 * 右侧时间条用百分比绝对定位落在全局 [min,max] 区间里。
 *
 * ⚠️ 时间零值是 "0001-01-01T00:00:00Z" 而非 null，一律过 `parseApiTime`
 * （零值/空值 → null），绝不直接 `new Date`，否则会把没排期的任务钉到公元 1 年。
 */
export function GanttView({ projectId, viewId }: { projectId: number; viewId: number }) {
	const { filter } = useFilterParam();
	// 甘特图要看全貌，不分页（一页至多 50，够铺满时间轴；真超了后续再谈跨页合并）
	const query = useViewTasks(projectId, viewId, { ...toFilterQuery(filter) });

	if (query.isPending) {
		return <p className="text-sm text-muted-foreground">加载中…</p>;
	}

	if (query.isError) {
		return <TaskQueryError error={query.error} />;
	}

	const tasks = query.data.items;

	if (tasks.length === 0) {
		return (
			<div className="flex flex-col gap-6" data-testid="gantt-view">
				<div data-testid="gantt-empty" className="flex flex-col items-start gap-1 py-10">
					<p className="text-sm font-medium text-foreground">这个项目还没有任务</p>
					<p className="text-sm text-muted-foreground">给任务排上起止日期，它就会出现在时间轴上。</p>
				</div>
			</div>
		);
	}

	// 有任意有效日期的进时间轴，一个日期都没有的归入"未排期"
	const scheduled: { task: Task; span: TaskSpan }[] = [];
	const unscheduled: Task[] = [];
	for (const task of tasks) {
		const span = taskSpan(task);
		if (span) scheduled.push({ task, span });
		else unscheduled.push(task);
	}

	// 全局区间：所有排期任务日期的最小/最大值
	const bounds = globalBounds(scheduled.map((entry) => entry.span));

	return (
		<div className="flex flex-col gap-6" data-testid="gantt-view">
			{bounds ? (
				<section className="flex flex-col gap-3">
					<h2 className="ink-heading ink-tick pl-3 text-base">时间轴</h2>
					<div className="ink-card overflow-x-auto p-4">
						{/* minWidth 让长跨度的项目横向滚动，短跨度时铺满不留大空白 */}
						<div style={{ minWidth: bounds.minWidth }}>
							<Axis bounds={bounds} />
							<ul>
								{scheduled.map(({ task, span }) => (
									<GanttRow key={task.id} task={task} span={span} bounds={bounds} />
								))}
							</ul>
						</div>
					</div>
				</section>
			) : null}

			{unscheduled.length ? (
				<section className="flex flex-col gap-3" data-testid="gantt-unscheduled">
					<h2 className="ink-heading text-base">未排期</h2>
					<ul className="ink-card divide-border divide-y">
						{unscheduled.map((task) => (
							<li key={task.id} data-testid="gantt-unscheduled-row" data-task-id={task.id}>
								<Link
									to={`/tasks/${task.id}`}
									className="flex items-center gap-2 px-3 py-2 transition-colors hover:bg-accent/60"
								>
									<TaskName task={task} />
								</Link>
							</li>
						))}
					</ul>
				</section>
			) : null}
		</div>
	);
}

/** 左侧任务名列的固定宽度（px），轴与每一行共用，保证时间刻度对齐。 */
const LABEL_WIDTH = 224;

const DAY = 86_400_000;

interface TaskSpan {
	/** 任务占据的最早时间戳（ms）。 */
	lo: number;
	/** 任务占据的最晚时间戳（ms）；`hi === lo` 表示只有单个日期（画标记而非条）。 */
	hi: number;
}

/**
 * 从任务的三个日期字段推出它在时间轴上的占位区间。
 * start/end/due 都过 `parseApiTime`（零值 → null），一个都没有返回 null（= 未排期）。
 * 有区间（lo<hi）画时间条；只有单个日期（lo===hi，典型是只有 due_date）画当天标记。
 */
function taskSpan(task: Task): TaskSpan | null {
	const times: number[] = [];
	for (const raw of [task.start_date, task.end_date, task.due_date]) {
		const date = parseApiTime(raw);
		if (date) times.push(date.getTime());
	}
	if (times.length === 0) return null;
	return { lo: Math.min(...times), hi: Math.max(...times) };
}

interface Bounds {
	min: number;
	max: number;
	/** max-min，最小为 1，避免全部同一天时除零。 */
	span: number;
	minWidth: number;
	ticks: { left: number; label: string }[];
}

/** 汇总所有排期任务，算出全局区间、横向最小宽度与时间刻度。 */
function globalBounds(spans: TaskSpan[]): Bounds | null {
	if (spans.length === 0) return null;
	let min = Infinity;
	let max = -Infinity;
	for (const s of spans) {
		if (s.lo < min) min = s.lo;
		if (s.hi > max) max = s.hi;
	}
	const span = Math.max(1, max - min);
	const days = span / DAY;
	// 每天给多少像素随跨度自适应：短跨度铺得开，长跨度压紧，整体夹在 [560, 4000]
	const pxPerDay = days <= 14 ? 44 : days <= 90 ? 14 : 5;
	const trackWidth = clamp(days * pxPerDay, 560, 4000);
	return {
		min,
		max,
		span,
		minWidth: LABEL_WIDTH + trackWidth,
		ticks: buildTicks(min, max, span, days),
	};
}

function clamp(value: number, lo: number, hi: number): number {
	return Math.min(hi, Math.max(lo, value));
}

/** 起点归零到当天 00:00（本地时区），刻度从整天开始更好读。 */
function startOfDay(t: number): Date {
	const d = new Date(t);
	return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

/** 按跨度自适应粒度生成刻度：≤14 天按天、≤90 天按周、更长按月。 */
function buildTicks(min: number, max: number, span: number, days: number): { left: number; label: string }[] {
	const ticks: { left: number; label: string }[] = [];
	const pct = (t: number) => clamp(((t - min) / span) * 100, 0, 100);
	const mmdd = (d: Date) => `${d.getMonth() + 1}/${d.getDate()}`;

	if (days <= 90) {
		const step = days <= 14 ? 1 : 7; // 天 / 周
		const cursor = startOfDay(min);
		while (cursor.getTime() <= max) {
			ticks.push({ left: pct(cursor.getTime()), label: mmdd(cursor) });
			cursor.setDate(cursor.getDate() + step);
		}
	} else {
		const start = new Date(min);
		const cursor = new Date(start.getFullYear(), start.getMonth(), 1);
		if (cursor.getTime() < min) cursor.setMonth(cursor.getMonth() + 1);
		while (cursor.getTime() <= max) {
			ticks.push({ left: pct(cursor.getTime()), label: `${cursor.getFullYear()}/${cursor.getMonth() + 1}` });
			cursor.setMonth(cursor.getMonth() + 1);
		}
	}

	// 兜底：至少给一个刻度，别让轴空着
	if (ticks.length === 0) {
		ticks.push({ left: 0, label: mmdd(new Date(min)) });
	}
	return ticks;
}

/** 时间刻度轴，与下方每一行的时间条共用同一套百分比坐标。 */
function Axis({ bounds }: { bounds: Bounds }) {
	return (
		<div className="flex">
			<div className="shrink-0" style={{ width: LABEL_WIDTH }} />
			<div className="border-border relative h-6 flex-1 border-b">
				{bounds.ticks.map((tick, i) => (
					<span
						key={i}
						className="text-muted-foreground absolute top-0 -translate-x-1/2 whitespace-nowrap text-[11px]"
						style={{ left: `${tick.left}%` }}
					>
						{tick.label}
					</span>
				))}
			</div>
		</div>
	);
}

function GanttRow({ task, span, bounds }: { task: Task; span: TaskSpan; bounds: Bounds }) {
	const left = ((span.lo - bounds.min) / bounds.span) * 100;
	const width = ((span.hi - span.lo) / bounds.span) * 100;
	const isBar = span.hi > span.lo;
	const done = task.done ?? false;

	return (
		<li
			className="hover:bg-accent/40 flex items-center gap-2 rounded-md transition-colors"
			data-testid="gantt-row"
			data-task-id={task.id}
		>
			<Link
				to={`/tasks/${task.id}`}
				className="shrink-0 py-2 pr-2"
				style={{ width: LABEL_WIDTH }}
			>
				<TaskName task={task} />
			</Link>

			<div className="relative h-8 flex-1">
				{isBar ? (
					<div
						data-testid="gantt-bar"
						data-kind="bar"
						title={task.title}
						className={cn(
							'absolute top-1/2 h-4 min-w-[6px] -translate-y-1/2 rounded',
							done ? 'bg-muted' : 'bg-primary/70',
						)}
						style={{ left: `${left}%`, width: `${width}%` }}
					/>
				) : (
					// 只有单个日期（典型是只有到期日）：画一个当天的菱形标记
					<div
						data-testid="gantt-bar"
						data-kind="marker"
						title={task.title}
						className={cn(
							'absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rotate-45 rounded-[2px]',
							done ? 'bg-muted' : 'bg-primary/70',
						)}
						style={{ left: `${left}%` }}
					/>
				)}
			</div>
		</li>
	);
}

/** 任务名：identifier + 标题，完成态删除线 + 变灰。列表/未排期/时间轴行共用。 */
function TaskName({ task }: { task: Task }) {
	return (
		<span
			className={cn(
				'block truncate text-sm hover:underline',
				task.done ? 'text-muted-foreground line-through' : 'text-foreground',
			)}
		>
			{task.identifier ? (
				<span className="text-muted-foreground mr-2 text-xs">{task.identifier}</span>
			) : null}
			{task.title}
		</span>
	);
}
