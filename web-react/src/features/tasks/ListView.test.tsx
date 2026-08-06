import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { Task } from '@/api/tasks';
import { TASKS_PER_PAGE } from '@/api/tasks';
import type { ProjectView, ViewKind } from '@/api/views';
import { ZERO_TIME } from '@/lib/datetime';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';
const PROJECT_ID = 12;
const LIST_VIEW_ID = 1;

function view(id: number, view_kind: ViewKind): ProjectView {
	return { id, project_id: PROJECT_ID, title: view_kind, view_kind };
}

function mockViews() {
	server.use(
		http.get(`${API}/projects/:projectId/views`, () => {
			const views = [
				view(LIST_VIEW_ID, 'list'),
				view(2, 'gantt'),
				view(3, 'table'),
				view(4, 'kanban'),
			];
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
	/** 每次请求收到的 URL，用来断言 page/per_page 真的发出去了。 */
	requests: URL[];
}

/**
 * 分页 mock：按 page 切片，头按后端口径发
 * （`result_count == 0` 时 `total_pages` 强制为 0，见 read_all.go:102-111）。
 */
function mockViewTasks(all: Task[], perPage = TASKS_PER_PAGE): TasksMock {
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
					// ⚠️ 按**本页条数**判 0，不是按总数：后端的规则是
					// `result_count == 0` 时把 total_pages 强制为 0（read_all.go:102-111）。
					// 按总数算的话，越界页（?page=9）mock 会回 3 而真后端回 0，
					// 于是"翻过最后一页"那条测的是一个真实不存在的场景。
					'x-pagination-total-pages': String(slice.length ? Math.ceil(all.length / perPage) : 0),
				},
			});
		}),
	);
	return { requests };
}

function renderList(path = `/projects/${PROJECT_ID}/list`) {
	mockViews();
	return renderApp(path);
}

describe('List 视图：渲染', () => {
	it('渲染任务行，标题链到任务详情', async () => {
		mockViewTasks([task(7, { identifier: 'PRJ-7' }), task(8)]);
		renderList();

		const rows = await screen.findAllByTestId('task-row');
		expect(rows).toHaveLength(2);
		expect(rows[0]).toHaveAttribute('data-task-id', '7');
		expect(within(rows[0]!).getByRole('link', { name: /任务 7/ })).toHaveAttribute(
			'href',
			'/tasks/7',
		);
		expect(rows[0]).toHaveTextContent('PRJ-7');
	});

	/**
	 * ★ 零值时间。这条会真红：直接 new Date("0001-01-01T00:00:00Z") 是个合法 Date，
	 * 于是每一条没有到期日的任务都会渲染出 "0001-01-01"。
	 */
	it('★ 到期日零值不渲染成公元 1 年，而是根本不渲染', async () => {
		mockViewTasks([
			task(1, { due_date: ZERO_TIME }),
			task(2, { due_date: '2026-08-20T09:00:00Z' }),
			task(3),
		]);
		renderList();

		await screen.findAllByTestId('task-row');
		const dates = screen.getAllByTestId('task-due-date');
		expect(dates).toHaveLength(1);
		expect(screen.queryByText(/0001-01-01/)).not.toBeInTheDocument();
	});

	it('done 的任务勾上且带删除线样式，勾选框是只读的（改 done 归 F08a）', async () => {
		mockViewTasks([task(1, { done: true }), task(2, { done: false })]);
		renderList();

		await screen.findAllByTestId('task-row');
		// 按 testid 取那一行的复选框；完成态看 data-done，不看 aria-label 文案
		const doneBox = screen.getByTestId('task-done-1');
		expect(doneBox).toBeChecked();
		expect(doneBox).toBeDisabled();
	});

	it('标签与优先级有值才渲染', async () => {
		mockViewTasks([
			task(1, { labels: [{ id: 5, title: 'bug' }], priority: 4 }),
			task(2, { labels: null, priority: 0 }),
		]);
		renderList();

		await screen.findAllByTestId('task-row');
		expect(screen.getAllByTestId('task-labels')).toHaveLength(1);
		expect(screen.getByTestId('task-priority')).toHaveTextContent('紧急');
	});
});

describe('List 视图：分页', () => {
	const many = Array.from({ length: 7 }, (_, i) => task(i + 1));

	it('★ 翻页拿到下一页数据，页码进 URL', async () => {
		mockViewTasks(many, 3);
		const { router } = renderList();

		expect(await screen.findByText('任务 1')).toBeInTheDocument();
		expect(screen.getByTestId('pagination-status')).toHaveTextContent('第 1 / 3 页');

		await userEvent.click(screen.getByTestId('pagination-next'));

		expect(await screen.findByText('任务 4')).toBeInTheDocument();
		expect(screen.queryByText('任务 1')).not.toBeInTheDocument();
		expect(router.state.location.search).toBe('?page=2');
	});

	it('★ page 真的发给后端（不是只改了 URL 本地切片）', async () => {
		const mock = mockViewTasks(many, 3);
		renderList();

		await screen.findByText('任务 1');
		await userEvent.click(screen.getByTestId('pagination-next'));
		await screen.findByText('任务 4');

		// 第 1 页也显式发 page=1，不靠后端默认值——省掉它就等于把首页行为交给后端配置
		const pages = mock.requests.map((url) => url.searchParams.get('page'));
		expect(pages).toEqual(['1', '2']);
		// per_page 也必须发，否则页数与后端默认值耦合，改默认值时前端静默变行为
		expect(mock.requests[0]!.searchParams.get('per_page')).toBe(String(TASKS_PER_PAGE));
	});

	it('直接用 ?page=3 进来就落在第 3 页', async () => {
		mockViewTasks(many, 3);
		renderList(`/projects/${PROJECT_ID}/list?page=3`);

		expect(await screen.findByText('任务 7')).toBeInTheDocument();
		expect(screen.getByTestId('pagination-status')).toHaveTextContent('第 3 / 3 页');
	});

	it('回到第 1 页时把 page 从 URL 里摘掉（链接保持干净）', async () => {
		mockViewTasks(many, 3);
		const { router } = renderList(`/projects/${PROJECT_ID}/list?page=2`);

		expect(await screen.findByText('任务 4')).toBeInTheDocument();
		await userEvent.click(screen.getByTestId('pagination-prev'));

		expect(await screen.findByText('任务 1')).toBeInTheDocument();
		expect(router.state.location.search).toBe('');
	});

	it('首页禁用"上一页"，末页禁用"下一页"', async () => {
		mockViewTasks(many, 3);
		renderList();

		await screen.findByText('任务 1');
		expect(screen.getByTestId('pagination-prev')).toBeDisabled();
		expect(screen.getByTestId('pagination-next')).toBeEnabled();
	});

	it('只有一页时不渲染分页控件', async () => {
		mockViewTasks([task(1)], 3);
		renderList();

		await screen.findByText('任务 1');
		expect(screen.queryByTestId('pagination')).not.toBeInTheDocument();
	});

	/** 非法 page 不该把 NaN 甩给后端 —— 后端会回 400，UI 上表现成"翻页报错"。 */
	it.each(['abc', '0', '-1', '1.5', ''])('page=%s 降级到第 1 页且不发非法值', async (raw) => {
		const mock = mockViewTasks(many, 3);
		renderList(`/projects/${PROJECT_ID}/list?page=${raw}`);

		expect(await screen.findByText('任务 1')).toBeInTheDocument();
		expect(mock.requests[0]!.searchParams.get('page')).toBe('1');
	});
});

describe('List 视图：空态', () => {
	it('★ 项目没有任务时给空态，而不是空白或报错', async () => {
		mockViewTasks([]);
		renderList();

		expect(await screen.findByTestId('list-empty')).toHaveTextContent('这个项目还没有任务');
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
		// total_pages 为 0，分页控件不该出现
		expect(screen.queryByTestId('pagination')).not.toBeInTheDocument();
	});

	/**
	 * ★ 翻过最后一页（手改 URL，或翻页途中数据变少）。
	 * 和"项目没有任务"必须是不同的话 —— 混成一句会让人以为任务全没了。
	 */
	it('★ 翻过最后一页时提示的是"这一页没有"，并给回第一页的出口', async () => {
		mockViewTasks([task(1)], 3);
		const { router } = renderList(`/projects/${PROJECT_ID}/list?page=5`);

		expect(await screen.findByTestId('list-empty-page')).toHaveTextContent('第 5 页没有任务');
		expect(screen.queryByTestId('list-empty')).not.toBeInTheDocument();

		await userEvent.click(screen.getByTestId('back-to-first-page'));

		expect(await screen.findByText('任务 1')).toBeInTheDocument();
		expect(router.state.location.search).toBe('');
	});
});

describe('List 视图：与容器的接线', () => {
	/** kind → view 对象的解析结果：容器把 list view 的 id 交给了 ListView。 */
	it('★ 任务请求打的是容器解析出的 view id，不是项目 id', async () => {
		const mock = mockViewTasks([task(1)]);
		renderList();

		await screen.findByText('任务 1');
		await waitFor(() => expect(mock.requests).toHaveLength(1));
		expect(mock.requests[0]!.pathname).toBe(
			`/api/v1/projects/${PROJECT_ID}/views/${LIST_VIEW_ID}/tasks`,
		);
	});

	it('切到别的视图再切回来，页码已重置（视图间不共享页码）', async () => {
		mockViewTasks(
			Array.from({ length: 7 }, (_, i) => task(i + 1)),
			3,
		);
		const { router } = renderList(`/projects/${PROJECT_ID}/list?page=2`);

		expect(await screen.findByText('任务 4')).toBeInTheDocument();
		await userEvent.click(screen.getByTestId('view-tab-table'));
		await screen.findByTestId('table-view');
		await userEvent.click(screen.getByTestId('view-tab-list'));

		expect(await screen.findByText('任务 1')).toBeInTheDocument();
		expect(router.state.location.search).toBe('');
	});

	it('任务接口报错时展示后端消息，不是白屏', async () => {
		server.use(
			http.get(`${API}/projects/:projectId/views/:viewId/tasks`, () =>
				HttpResponse.json({ code: 4001, message: '任务列表取不到' }, { status: 500 }),
			),
		);
		renderList();

		expect(await screen.findByRole('alert')).toHaveTextContent('任务列表取不到');
	});
});
