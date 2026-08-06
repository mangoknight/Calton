import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { Task } from '@/api/tasks';
import { THIS_WEEK_FILTER, TODAY_FILTER } from '@/features/tasks/home-filters';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';

function task(id: number, extra: Partial<Task> = {}): Task {
	return { id, title: `T${id}`, ...extra };
}

function page(items: Task[]) {
	return HttpResponse.json(items, {
		headers: {
			'x-pagination-result-count': String(items.length),
			'x-pagination-total-pages': items.length ? '1' : '0',
		},
	});
}

interface HomeMocks {
	/** 按 filter 串分派；键为 filter 值，`''` 表示不带 filter（即 useHasAnyTasks）。 */
	byFilter?: Record<string, Task[]>;
	favorites?: Task[];
	/** 让某个 filter 分区返回错误。 */
	errorFor?: { filter: string; status: number; body: { code: number; message: string } };
}

/** 记录所有请求 URL，断言"发了什么"而不只是"渲染了什么"。 */
function mockHome({ byFilter = {}, favorites = [], errorFor }: HomeMocks) {
	const urls: URL[] = [];

	server.use(
		http.get(`${API}/tasks`, ({ request }) => {
			const url = new URL(request.url);
			urls.push(url);
			const filter = url.searchParams.get('filter') ?? '';

			if (errorFor && filter === errorFor.filter) {
				return HttpResponse.json(errorFor.body, { status: errorFor.status });
			}
			return page(byFilter[filter] ?? []);
		}),
		http.get(`${API}/projects/:projectId/tasks`, ({ request }) => {
			urls.push(new URL(request.url));
			return page(favorites);
		}),
	);

	return urls;
}

const BROWSER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

describe('取数口径', () => {
	it('★ 今日/本周用 datemath 表达式，且**必须**带 filter_timezone', async () => {
		// ☠ datemath 的默认时区是 UTC。不发 filter_timezone 的话 `now/d` 截的是 UTC 的今天，
		//   在 +08:00 下"今日到期"会变成本地时间今早 8 点到明早 8 点 —— 请求 200、无任何报错。
		//   所以这条断言的重点是 filter_timezone 存在，而不只是 filter 对。
		const urls = mockHome({ byFilter: { [TODAY_FILTER]: [task(1)] } });

		renderApp('/');
		await waitFor(() => expect(urls.length).toBeGreaterThanOrEqual(3));

		const today = urls.find((u) => u.searchParams.get('filter') === TODAY_FILTER);
		expect(today).toBeDefined();
		expect(today!.searchParams.get('filter_timezone')).toBe(BROWSER_TZ);

		const week = urls.find((u) => u.searchParams.get('filter') === THIS_WEEK_FILTER);
		expect(week).toBeDefined();
		expect(week!.searchParams.get('filter_timezone')).toBe(BROWSER_TZ);
	});

	it('★ 本周用 now/w（周起点是周一，上游从不改 start_of_week）', () => {
		expect(THIS_WEEK_FILTER).toContain('now/w');
		expect(THIS_WEEK_FILTER).toContain('now/w+1w');
		// 半开区间：恰好落在边界零点的任务不该被两个区间同时收进去
		expect(THIS_WEEK_FILTER).not.toContain('>=');
		expect(THIS_WEEK_FILTER).not.toContain('<=');
	});

	it('★ 收藏走伪项目 -1 的项目入口，而不是 filter=is_favorite', async () => {
		// ☠ `is_favorite` **不在**可筛选白名单里（FILTERABLE_TASK_FIELDS = 可排序字段 ∪
		//   {assignees,labels,reminders}），写成 filter 会得到 400/4016。
		//   所以这条同时钉两件事：打对了路径，且**没有**去 filter 上碰 is_favorite。
		const urls = mockHome({ favorites: [task(7, { title: '收藏的任务' })] });

		renderApp('/');
		// ⚠️ 「收藏的任务」是 mock 里的**任务标题（数据）**，不是分区标题（文案）。
		// 按它断言是对的：验的是"这条任务真的渲染出来了"。
		// 换成分区标题的 testid 会让断言变弱 —— 列表空着它也照样绿。
		expect(await screen.findByText('收藏的任务')).toBeInTheDocument();

		expect(urls.some((u) => u.pathname.endsWith('/projects/-1/tasks'))).toBe(true);
		expect(urls.every((u) => !(u.searchParams.get('filter') ?? '').includes('is_favorite'))).toBe(
			true,
		);
	});

	it('★ 全局集合打的是 /tasks，不是 /tasks/all', async () => {
		// /tasks/all 是本 fork 未注册的 Calton-only 别名，已认证时是 400/2004 ——
		// 而上游前端历史上用的就是它，照抄会得到一个看起来毫不相干的 400。
		const urls = mockHome({});

		renderApp('/');
		await waitFor(() => expect(urls.length).toBeGreaterThanOrEqual(3));

		expect(urls.some((u) => u.pathname.endsWith('/tasks'))).toBe(true);
		expect(urls.every((u) => !u.pathname.includes('/tasks/all'))).toBe(true);
	});
});

describe('两种空态必须分开', () => {
	it('★ 账号里一条任务都没有 → 引导去建任务', async () => {
		// 不带 filter 的探测查询返回空 ⇒ 这个账号确实没有任务
		const urls = mockHome({ byFilter: { '': [] } });

		renderApp('/');

		expect(await screen.findAllByTestId('home-empty-account')).not.toHaveLength(0);
		await waitFor(() => expect(urls.length).toBeGreaterThanOrEqual(3));
	});

	it('★ 有任务、只是这个分区没匹配到 → 如实说"这段时间没有"，不许说"你还没有任务"', async () => {
		// ☠ 判别点全在这里：两条用例的**分区结果完全相同**（都是空），
		//   区别只在那条不带 filter 的探测查询。若实现只看分区自己的结果，
		//   两条用例会给出同一句文案，其中一条必错。
		mockHome({ byFilter: { '': [task(99)] } }); // 账号里有任务，但三个分区都空

		renderApp('/');

		expect(await screen.findAllByTestId('home-empty-filtered')).not.toHaveLength(0);
		expect(screen.queryByTestId('home-empty-account')).not.toBeInTheDocument();
	});

	it('★ 探测查询失败时不下结论 —— 不能对着未知说"你还没有任务"', async () => {
		// hasAnyTasks 是 undefined。猜错方向的代价是对一个有几百条任务的账号说"你还没有任务"。
		server.use(
			http.get(`${API}/tasks`, ({ request }) => {
				const url = new URL(request.url);
				if (!url.searchParams.get('filter')) {
					return HttpResponse.json({ code: 1, message: 'boom' }, { status: 500 });
				}
				return page([]);
			}),
			http.get(`${API}/projects/:projectId/tasks`, () => page([])),
		);

		renderApp('/');

		expect(await screen.findAllByTestId('home-empty-filtered')).not.toHaveLength(0);
		expect(screen.queryByTestId('home-empty-account')).not.toBeInTheDocument();
	});
});

describe('分区互不影响', () => {
	it('★ 一个分区失败，其余分区照常显示', async () => {
		// ☠ 三个分区共用一个 catch 的话，今日 500 会把本周和收藏一起变成错误块。
		mockHome({
			byFilter: { [THIS_WEEK_FILTER]: [task(5, { title: '本周的任务' })] },
			favorites: [task(7, { title: '收藏的任务' })],
			errorFor: {
				filter: TODAY_FILTER,
				status: 500,
				body: { code: 1, message: '今日分区炸了' },
			},
		});

		renderApp('/');

		const today = await screen.findByTestId('home-today');
		expect(await within(today).findByText('今日分区炸了')).toBeInTheDocument();

		// 同上：这两个都是**任务标题**。这条用例验的是"一个分区炸了，另外两个照常出数据"，
		// 所以必须断言**数据**渲染出来了，断言分区标题存在是验不到这件事的。
		expect(await screen.findByText('本周的任务')).toBeInTheDocument();
		expect(await screen.findByText('收藏的任务')).toBeInTheDocument();
	});

	it('筛选表达式写错时透出后端原文（复用 F11a 的错误展示）', async () => {
		mockHome({
			errorFor: {
				filter: TODAY_FILTER,
				status: 400,
				body: { code: 4016, message: "The task field 'due_dat' is invalid." },
			},
		});

		renderApp('/');

		const today = await screen.findByTestId('home-today');
		expect(await within(today).findByTestId('filter-error-message')).toHaveTextContent('due_dat');
	});
});
