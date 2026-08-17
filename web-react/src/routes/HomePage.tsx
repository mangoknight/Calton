import { Star } from 'lucide-react';
import { Link } from 'react-router-dom';

import type { Task } from '@/api/tasks';
import { TaskQueryError } from '@/features/tasks/FilterError';
import {
	useFavoriteTasks,
	useHasAnyTasks,
	useThisWeekTasks,
	useTodayTasks,
} from '@/features/tasks/home-queries';
import { useTranslation } from '@/i18n/context';
import { formatApiDate } from '@/lib/datetime';
import { cn } from '@/lib/utils';

/**
 * Home（F12）：今日 / 本周 / 收藏。
 *
 * 三个分区各自取数、各自失败 —— 一个分区 500 不该让另外两个也空着。
 *
 * ⚠️ 时间语义全部由服务端的 datemath 决定，前端**不另算一遍**"本周是几号到几号"
 * （理由见 `home-filters.ts` 文件头）。分区标题因此只说"今日/本周"，
 * 不显示具体日期区间 —— 显示了就等于前端在对区间做承诺，而那个承诺前端保证不了。
 */
export function HomePage() {
	const today = useTodayTasks();
	const week = useThisWeekTasks();
	const favorites = useFavoriteTasks();
	// 用来区分"筛选没匹配到"与"这个账号根本还没有任务"
	const hasAnyTasks = useHasAnyTasks();
	const t = useTranslation();

	return (
		<section className="p-6" data-testid="home-page">
			<h1 className="ink-heading text-2xl">{t('navigation.overview')}</h1>

			<div className="mt-4 space-y-6">
				<Section
					testId="home-today"
					title="今日到期"
					query={today}
					hasAnyTasks={hasAnyTasks.data}
					emptyFiltered="今天没有到期的任务。"
				/>
				<Section
					testId="home-week"
					title="本周到期"
					query={week}
					hasAnyTasks={hasAnyTasks.data}
					emptyFiltered="本周没有到期的任务。"
				/>
				<Section
					testId="home-favorites"
					title={t('project.pseudo.favorites.title')}
					query={favorites}
					hasAnyTasks={hasAnyTasks.data}
					emptyFiltered="还没有收藏任何任务。给任务点上星标，它会出现在这里。"
				/>
			</div>
		</section>
	);
}

interface SectionQuery {
	isPending: boolean;
	isError: boolean;
	error: Parameters<typeof TaskQueryError>[0]['error'] | null;
	data?: { items: Task[] };
}

function Section({
	testId,
	title,
	query,
	hasAnyTasks,
	emptyFiltered,
}: {
	testId: string;
	title: string;
	query: SectionQuery;
	/** undefined = 还没问出来；此时不下"你还没有任务"的结论。 */
	hasAnyTasks: boolean | undefined;
	emptyFiltered: string;
}) {
	return (
		<section data-testid={testId}>
			<h2 data-testid={`${testId}-title`} className="ink-heading ink-tick pl-3 text-base">
				{title}
			</h2>

			<div className="mt-2">
				{query.isPending ? (
					<p className="text-sm text-muted-foreground">加载中…</p>
				) : query.isError && query.error ? (
					<TaskQueryError error={query.error} />
				) : query.data && query.data.items.length > 0 ? (
					<ul className="divide-border divide-y border-t border-border" data-testid={`${testId}-list`}>
						{query.data.items.map((task) => (
							<TaskRow key={task.id} task={task} />
						))}
					</ul>
				) : (
					<SectionEmpty hasAnyTasks={hasAnyTasks} emptyFiltered={emptyFiltered} />
				)}
			</div>
		</section>
	);
}

/**
 * 两种空要分开说，因为用户该做的事相反：
 *  - 账号里一条任务都没有 → 该去建任务（引导）
 *  - 有任务，只是这个分区没匹配到 → 什么都不用做（如实说"这段时间没有"）
 *
 * ⚠️ `hasAnyTasks === undefined` 表示那条查询还没回来/失败了。此时**两句都不说死**，
 * 只说本分区为空 —— 猜错方向的代价是对着一个有几百条任务的账号说"你还没有任务"。
 */
function SectionEmpty({
	hasAnyTasks,
	emptyFiltered,
}: {
	hasAnyTasks: boolean | undefined;
	emptyFiltered: string;
}) {
	if (hasAnyTasks === false) {
		return (
			<div data-testid="home-empty-account" className="py-4">
				<p className="text-sm font-medium text-foreground">你还没有任何任务</p>
				<p className="mt-1 text-sm text-muted-foreground">
					先
					<Link to="/projects" className="text-primary underline-offset-4 hover:underline">
						建一个项目
					</Link>
					，然后在里面添加任务。
				</p>
			</div>
		);
	}

	return (
		<p data-testid="home-empty-filtered" className="py-4 text-sm text-muted-foreground">
			{emptyFiltered}
		</p>
	);
}

function TaskRow({ task }: { task: Task }) {
	const due = formatApiDate(task.due_date);

	return (
		<li className="flex items-center gap-3 rounded-md px-2 py-2 transition-colors hover:bg-accent/60">
			{task.is_favorite ? (
				<Star className="size-4 shrink-0 text-xyz-orange-6" aria-label="已收藏" />
			) : null}

			<Link
				to={`/tasks/${task.id}`}
				className={cn(
					'min-w-0 flex-1 truncate text-sm hover:underline',
					task.done ? 'text-muted-foreground line-through' : 'text-foreground',
				)}
			>
				{task.title}
			</Link>

			{due ? <span className="shrink-0 text-sm text-muted-foreground">{due}</span> : null}
		</li>
	);
}
