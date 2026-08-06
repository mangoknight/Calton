import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';

import type { Task } from '@/api/tasks';
import type { ProjectView, ViewKind } from '@/api/views';
import { ZERO_TIME } from '@/lib/datetime';
import { useUIStore } from '@/store/ui';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';
import { COLUMNS_STORAGE_KEY } from './columns';

const API = '*/api/v1';
const PROJECT_ID = 12;
const TABLE_VIEW_ID = 3;

function view(id: number, view_kind: ViewKind): ProjectView {
	return { id, project_id: PROJECT_ID, title: view_kind, view_kind };
}

function mockViews() {
	server.use(
		http.get(`${API}/projects/:projectId/views`, () => {
			const views = [view(1, 'list'), view(2, 'gantt'), view(TABLE_VIEW_ID, 'table')];
			return HttpResponse.json(views, {
				headers: {
					'x-pagination-result-count': String(views.length),
					'x-pagination-total-pages': '1',
				},
			});
		}),
	);
}

function task(id: number, overrides: Partial<Task> = {}): Task {
	return { id, title: `任务 ${id}`, ...overrides };
}

interface TasksMock {
	requests: URL[];
	/** 最后一次请求里的 sort_by / order_by，按下标成对是本任务的验收要点。 */
	lastSort(): { sort_by: string[]; order_by: string[] };
}

function mockViewTasks(all: Task[], perPage = 50): TasksMock {
	const requests: URL[] = [];
	server.use(
		http.get(`${API}/projects/:projectId/views/:viewId/tasks`, ({ request }) => {
			const url = new URL(request.url);
			requests.push(url);
			const page = Number(url.searchParams.get('page') ?? '1');
			const slice = all.slice((page - 1) * perPage, page * perPage);
			return HttpResponse.json(slice, {
				headers: {
					'x-pagination-result-count': String(slice.length),
					'x-pagination-total-pages': String(all.length ? Math.ceil(all.length / perPage) : 0),
				},
			});
		}),
	);
	return {
		requests,
		lastSort() {
			const url = requests[requests.length - 1]!;
			return {
				sort_by: url.searchParams.getAll('sort_by'),
				order_by: url.searchParams.getAll('order_by'),
			};
		},
	};
}

function renderTable(path = `/projects/${PROJECT_ID}/table`) {
	mockViews();
	return renderApp(path);
}

/**
 * ⚠️ 按**列 id** 取表头，不按列名文字。
 *
 * 列名（`标题`/`优先级`/`到期日`…）是文案，属于 i18n 迁移范围；
 * 而这些用例验的是"排序打哪个字段、aria-sort 对不对、哪些列不可排序"——
 * 与列名写什么无关（F13 规矩，模板见 `components/layout/AppShell.test.tsx`）。
 */
function queryHeader(columnId: string): HTMLElement | null {
	return screen
		.getByTestId('table-view')
		.querySelector<HTMLElement>(`th[data-column="${columnId}"]`);
}

function header(columnId: string): HTMLElement {
	const th = queryHeader(columnId);
	if (!th) throw new Error(`找不到列 ${columnId} 的表头`);
	return th;
}

function headerButton(columnId: string) {
	return within(header(columnId)).getByRole('button');
}

beforeEach(() => {
	// 列配置存 localStorage，跨用例会串味
	window.localStorage.clear();
});

describe('Table 视图：渲染', () => {
	it('默认列渲染成表格，标题链到任务详情', async () => {
		mockViewTasks([task(7, { identifier: 'PRJ-7', done: true, priority: 3 })]);
		renderTable();

		const row = await screen.findByTestId('task-table-row');
		expect(row).toHaveAttribute('data-task-id', '7');
		expect(within(row).getByRole('link', { name: '任务 7' })).toHaveAttribute('href', '/tasks/7');
		expect(row.querySelector('[data-column="done"]')).toHaveTextContent('已完成');
		expect(row.querySelector('[data-column="priority"]')).toHaveTextContent('高');
	});

	/** ★ 零值时间在表格里同样不能变成公元 1 年。 */
	it('★ 到期日零值渲染成占位符而不是 0001-01-01', async () => {
		mockViewTasks([task(1, { due_date: ZERO_TIME })]);
		renderTable();

		const row = await screen.findByTestId('task-table-row');
		expect(row.querySelector('[data-column="due_date"]')).toHaveTextContent('—');
		expect(screen.queryByText(/0001-01-01/)).not.toBeInTheDocument();
	});

	it('percent_done 是 0..1 的小数，按百分数渲染', async () => {
		window.localStorage.setItem(COLUMNS_STORAGE_KEY, JSON.stringify(['title', 'percent_done']));
		mockViewTasks([task(1, { percent_done: 0.25 })]);
		renderTable();

		const row = await screen.findByTestId('task-table-row');
		expect(row.querySelector('[data-column="percent_done"]')).toHaveTextContent('25%');
	});

	it('空表格给空态提示', async () => {
		mockViewTasks([]);
		renderTable();

		expect(await screen.findByTestId('table-empty')).toHaveTextContent('这个项目还没有任务');
	});
});

describe('★ Table 视图：sort_by / order_by 按下标成对', () => {
	it('点一列发出一对参数', async () => {
		const mock = mockViewTasks([task(1)]);
		renderTable();

		await screen.findByTestId('task-table-row');
		await userEvent.click(headerButton('due_date'));

		await screen.findByTestId('sort-marker-due_date');
		expect(mock.lastSort()).toEqual({ sort_by: ['due_date'], order_by: ['asc'] });
	});

	/**
	 * ★ 本任务的验收要点：点两列时顺序正确。
	 * 先点的列是主序，两个数组等长同序、逐位配对。
	 */
	it('★ 点两列时按点击顺序成对发出，先点的是主序', async () => {
		const mock = mockViewTasks([task(1)]);
		renderTable();

		await screen.findByTestId('task-table-row');
		await userEvent.click(headerButton('due_date')); // due_date asc
		await userEvent.click(headerButton('priority')); // priority asc
		await userEvent.click(headerButton('priority')); // priority desc

		await screen.findByTestId('sort-marker-priority');
		expect(mock.lastSort()).toEqual({
			sort_by: ['due_date', 'priority'],
			order_by: ['asc', 'desc'],
		});
	});

	/**
	 * ★ 逐位配对必须真的成立：只断言两个数组各自的内容，
	 * 把 order_by 整体反过来也能骗过去。
	 */
	it('★ 逐位配对：每个 sort_by 拿到的是自己那一列的方向', async () => {
		const mock = mockViewTasks([task(1)]);
		// ⚠️ 方向序列故意不是回文（desc,asc,asc）：回文的话"order_by 整体反转"
		// 这个 bug 能原样骗过本条断言。变异测试实测踩过。
		renderTable(`/projects/${PROJECT_ID}/table?sort=title:desc,due_date:asc,priority:asc`);

		await screen.findByTestId('task-table-row');
		const { sort_by, order_by } = mock.lastSort();

		expect(sort_by).toEqual(['title', 'due_date', 'priority']);
		expect(order_by).toHaveLength(sort_by.length);
		[
			['title', 'desc'],
			['due_date', 'asc'],
			['priority', 'asc'],
		].forEach(([field, direction], i) => {
			expect([sort_by[i], order_by[i]]).toEqual([field, direction]);
		});
	});

	it('三态循环：再点一次取消该列排序', async () => {
		const mock = mockViewTasks([task(1)]);
		const { router } = renderTable();

		await screen.findByTestId('task-table-row');
		await userEvent.click(headerButton('title')); // asc
		await userEvent.click(headerButton('title')); // desc
		await userEvent.click(headerButton('title')); // 取消

		expect(screen.queryByTestId('sort-marker-title')).not.toBeInTheDocument();
		expect(mock.lastSort()).toEqual({ sort_by: [], order_by: [] });
		// 排序清空后 sort 参数也要从 URL 里摘掉，不留 ?sort=
		expect(router.state.location.search).toBe('');
	});

	it('排序状态进 URL，可分享可回退', async () => {
		mockViewTasks([task(1)]);
		const { router } = renderTable();

		await screen.findByTestId('task-table-row');
		await userEvent.click(headerButton('due_date'));

		expect(router.state.location.search).toBe('?sort=due_date%3Aasc');
	});

	it('URL 里的排序在首屏就生效（不是点了才生效）', async () => {
		const mock = mockViewTasks([task(1)]);
		renderTable(`/projects/${PROJECT_ID}/table?sort=priority:desc`);

		await screen.findByTestId('task-table-row');
		expect(mock.lastSort()).toEqual({ sort_by: ['priority'], order_by: ['desc'] });
		expect(header('priority')).toHaveAttribute('aria-sort', 'descending');
	});

	/** 改排序后停在第 3 页会让用户看到一堆陌生数据，还以为数据错了。 */
	it('★ 改排序时回到第 1 页', async () => {
		const mock = mockViewTasks(
			Array.from({ length: 7 }, (_, i) => task(i + 1)),
			3,
		);
		const { router } = renderTable(`/projects/${PROJECT_ID}/table?page=2`);

		await screen.findAllByTestId('task-table-row');
		await userEvent.click(headerButton('title'));

		await screen.findByTestId('sort-marker-title');
		expect(router.state.location.search).toBe('?sort=title%3Aasc');
		expect(mock.requests[mock.requests.length - 1]!.searchParams.get('page')).toBe('1');
	});

	it('后端不支持排序的列（标签/指派给）列头不可点', async () => {
		window.localStorage.setItem(COLUMNS_STORAGE_KEY, JSON.stringify(['title', 'labels']));
		mockViewTasks([task(1)]);
		renderTable();

		await screen.findByTestId('task-table-row');
		const labelHeader = header('labels');
		expect(within(labelHeader).queryByRole('button')).not.toBeInTheDocument();
	});

	it('多列排序时标出第几序（只有箭头看不出谁是主序）', async () => {
		mockViewTasks([task(1)]);
		renderTable(`/projects/${PROJECT_ID}/table?sort=title:asc,priority:desc`);

		await screen.findByTestId('task-table-row');
		expect(screen.getByTestId('sort-marker-title')).toHaveTextContent('1');
		expect(screen.getByTestId('sort-marker-priority')).toHaveTextContent('2');
	});

	/**
	 * ★ 后端**总会**在末尾追加 `id asc` 作为兜底排序
	 * （tasks.go:340-346：末位不是 id 时就 append 一个），前端不要自己也补一个。
	 *
	 * 补了不会报错——这正是危险之处：请求里多一个 id，UI 上还得决定要不要给它画箭头，
	 * 于是用户会看到一个自己没点过的排序列。兜底是服务端的实现细节，对用户不可见。
	 */
	it('★ 前端不自作主张追加 id 兜底排序（那是后端的事，且对用户不可见）', async () => {
		const mock = mockViewTasks([task(1)]);
		renderTable(`/projects/${PROJECT_ID}/table?sort=title:asc`);

		await screen.findByTestId('task-table-row');
		expect(mock.lastSort()).toEqual({ sort_by: ['title'], order_by: ['asc'] });
		// 也不能给"编号"列画上排序标记——用户没点过它
		expect(screen.queryByTestId('sort-marker-index')).not.toBeInTheDocument();
		expect(header('index')).not.toHaveAttribute('aria-sort');
	});

	/**
	 * ★ 排序状态不能假设"只有一个排序字段"：多列时每一列都要各自播报自己的方向。
	 * 只给主序列写 aria-sort 的实现，在这条上会红。
	 */
	it('★ 多列排序时每一列都有自己的 aria-sort', async () => {
		mockViewTasks([task(1)]);
		renderTable(`/projects/${PROJECT_ID}/table?sort=title:asc,priority:desc,due_date:desc`);

		await screen.findByTestId('task-table-row');
		expect(header('title')).toHaveAttribute('aria-sort', 'ascending');
		expect(header('priority')).toHaveAttribute('aria-sort', 'descending');
		expect(header('due_date')).toHaveAttribute('aria-sort', 'descending');
		// 没参与排序的列不该有 aria-sort（"无排序"不是 "none"，是根本没有这个属性）
		expect(header('done')).not.toHaveAttribute('aria-sort');
	});
});

describe('Table 视图：列配置', () => {
	it('勾选后该列出现，取消后消失', async () => {
		mockViewTasks([task(1, { start_date: '2026-08-10T00:00:00Z' })]);
		renderTable();

		await screen.findByTestId('task-table-row');
		expect(queryHeader('start_date')).not.toBeInTheDocument();

		await userEvent.click(screen.getByTestId('column-toggle-start_date'));
		await waitFor(() => expect(queryHeader('start_date')).toBeInTheDocument());

		await userEvent.click(screen.getByTestId('column-toggle-start_date'));
		expect(queryHeader('start_date')).not.toBeInTheDocument();
	});

	/**
	 * ★ 藏起正在排序的列时，排序必须一并摘掉。
	 *
	 * 不摘的话 sort_by 照发，而 UI 上没有任何出口能取消 —— 列头已经不在页面上，
	 * 用户只能手改 URL。探针实测过这个洞。
	 */
	it('★ 隐藏正在排序的列时，一并摘掉它的排序（否则排序不可见却仍在生效）', async () => {
		const mock = mockViewTasks([task(1)]);
		const { router } = renderTable(`/projects/${PROJECT_ID}/table?sort=due_date:asc`);

		await screen.findByTestId('task-table-row');
		expect(mock.lastSort().sort_by).toEqual(['due_date']);

		await userEvent.click(screen.getByTestId('column-toggle-due_date'));

		await waitFor(() => expect(mock.lastSort().sort_by).toEqual([]));
		expect(router.state.location.search).toBe('');
		expect(queryHeader('due_date')).not.toBeInTheDocument();
	});

	it('隐藏没在排序的列不影响现有排序', async () => {
		const mock = mockViewTasks([task(1)]);
		renderTable(`/projects/${PROJECT_ID}/table?sort=title:asc`);

		await screen.findByTestId('task-table-row');
		await userEvent.click(screen.getByTestId('column-toggle-due_date'));

		await waitFor(() => expect(queryHeader('due_date')).not.toBeInTheDocument());
		expect(mock.lastSort()).toEqual({ sort_by: ['title'], order_by: ['asc'] });
	});

	/** 多列排序时只摘掉被隐藏那一列，其余保持顺序。 */
	it('★ 多列排序时只摘掉被隐藏的那一列，其余次序不变', async () => {
		const mock = mockViewTasks([task(1)]);
		renderTable(`/projects/${PROJECT_ID}/table?sort=title:asc,due_date:desc,priority:asc`);

		await screen.findByTestId('task-table-row');
		await userEvent.click(screen.getByTestId('column-toggle-due_date'));

		await waitFor(() =>
			expect(mock.lastSort()).toEqual({ sort_by: ['title', 'priority'], order_by: ['asc', 'asc'] }),
		);
	});

	it('★ 列顺序按定义顺序，不按勾选顺序', async () => {
		window.localStorage.setItem(COLUMNS_STORAGE_KEY, JSON.stringify(['due_date']));
		mockViewTasks([task(1)]);
		renderTable();

		await screen.findByTestId('task-table-row');
		// 后勾的"标题"在定义里排在"到期日"前面，应该插到前面而不是追加到末尾
		await userEvent.click(screen.getByTestId('column-toggle-title'));

		// 断列 id 而不是列名文字：这条验的是**顺序**，与列名写什么无关（同 `header()` 的理由）。
		const headers = await screen.findAllByRole('columnheader');
		expect(headers.map((h) => h.dataset.column)).toEqual(['title', 'due_date']);
	});

	it('列配置存进 localStorage 并在重新进入时生效', async () => {
		mockViewTasks([task(1)]);
		const first = renderTable();

		await screen.findByTestId('task-table-row');
		await userEvent.click(screen.getByTestId('column-toggle-start_date'));
		await waitFor(() => expect(queryHeader('start_date')).toBeInTheDocument());
		first.unmount();

		mockViewTasks([task(1)]);
		renderTable();
		await screen.findByTestId('task-table-row');
		expect(queryHeader('start_date')).toBeInTheDocument();
	});

	/** 全关掉会得到一个没有任何列的表格。 */
	it('★ 不允许把最后一列也关掉', async () => {
		window.localStorage.setItem(COLUMNS_STORAGE_KEY, JSON.stringify(['title']));
		mockViewTasks([task(1)]);
		renderTable();

		await screen.findByTestId('task-table-row');
		await userEvent.click(screen.getByTestId('column-toggle-title'));

		expect(header('title')).toBeInTheDocument();
		expect(screen.getByTestId('column-toggle-title')).toBeChecked();
	});
});

describe('Table 视图：与容器的接线', () => {
	it('任务请求打的是 table view 的 id', async () => {
		const mock = mockViewTasks([task(1)]);
		renderTable();

		await screen.findByTestId('task-table-row');
		expect(mock.requests[0]!.pathname).toBe(
			`/api/v1/projects/${PROJECT_ID}/views/${TABLE_VIEW_ID}/tasks`,
		);
	});

	it('接口报错时展示后端消息', async () => {
		mockViews();
		server.use(
			http.get(`${API}/projects/:projectId/views/:viewId/tasks`, () =>
				HttpResponse.json({ code: 4001, message: '任务列表取不到' }, { status: 500 }),
			),
		);
		renderApp(`/projects/${PROJECT_ID}/table`);

		expect(await screen.findByRole('alert')).toHaveTextContent('任务列表取不到');
	});
});

describe('Table 视图：列名走 i18n', () => {
	/**
	 * ★★★ 切到英文后**表头文字真的变**。
	 *
	 * ## 这条是变异验证补出来的
	 *
	 * `TASK_COLUMNS` 是模块级常量、**只算一次**，所以 `labelKey` 必须存 key、
	 * 渲染时才 `t()`。我把 `labelKey` 改回写死的中文做变异 —— **全绿**，
	 * 也就是说这条设计当时没有任何东西守着（与批次二 zod 校验消息那次一模一样，
	 * 同一个坑在不同表上又出现了一次）。
	 *
	 * 判别式：locale 设 en，断言表头是英文。存文字的实现在这里仍显示中文。
	 *
	 * ⚠️ 本文件其余用例一律**不按文案定位**；这一条例外，
	 * 因为**它的被测对象就是文案本身**。
	 */
	it('★★★ 切到英文后表头是英文（不是写死的中文）', async () => {
		useUIStore.setState({ locale: 'en' });
		mockViewTasks([task(1)]);
		renderTable();

		await screen.findByTestId('task-table-row');
		// task.attributes.title 的 en 译文
		expect(header('title')).toHaveTextContent('Title');
		expect(header('title')).not.toHaveTextContent('标题');
	});
});
