import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { SavedFilter } from '@/api/saved-filters';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';
const FILTER_ID = 2;
/** filter 2 的伪项目 id：-filterId - 1 = -3 */
const PSEUDO_PROJECT_ID = -3;
const LIST_VIEW_ID = 41;

function savedFilter(overrides: Partial<SavedFilter> = {}): SavedFilter {
	return {
		id: FILTER_ID,
		title: '我的未完成',
		filters: { filter: 'done = false' },
		...overrides,
	};
}

function listResponse(items: unknown[]) {
	return HttpResponse.json(items, {
		headers: {
			'x-pagination-result-count': String(items.length),
			'x-pagination-total-pages': items.length ? '1' : '0',
		},
	});
}

interface FilterMock {
	taskRequests: URL[];
	viewRequests: URL[];
	posts: Record<string, unknown>[];
	deletes: number;
}

function mockFilter(filter: SavedFilter = savedFilter()): FilterMock {
	const mock: FilterMock = { taskRequests: [], viewRequests: [], posts: [], deletes: 0 };

	server.use(
		http.get(`${API}/filters/${FILTER_ID}`, () => HttpResponse.json(filter)),
		http.post(`${API}/filters/${FILTER_ID}`, async ({ request }) => {
			mock.posts.push((await request.json()) as Record<string, unknown>);
			return HttpResponse.json(filter);
		}),
		http.delete(`${API}/filters/${FILTER_ID}`, () => {
			mock.deletes += 1;
			return new HttpResponse(null, { status: 204 });
		}),
		// ⚠️ 视图与任务都走**伪项目 id** 路径
		http.get(`${API}/projects/:projectId/views`, ({ request }) => {
			mock.viewRequests.push(new URL(request.url));
			return listResponse([
				{ id: LIST_VIEW_ID, project_id: PSEUDO_PROJECT_ID, title: 'list', view_kind: 'list' },
			]);
		}),
		http.get(`${API}/projects/:projectId/views/:viewId/tasks`, ({ request }) => {
			mock.taskRequests.push(new URL(request.url));
			return listResponse([{ id: 9, title: '过滤出来的任务' }]);
		}),
	);

	return mock;
}

describe('筛选器页：路由与伪项目换算', () => {
	/**
	 * ★★ 验收要求"点击走伪 project id 路由"：
	 * URL 里是**正的 filter id**，而任务/视图请求打的是**负的伪项目 id**。
	 */
	it('★★ URL 用正的 filter id，任务请求走伪项目 id（-filterId-1）', async () => {
		const mock = mockFilter();
		renderApp(`/filters/${FILTER_ID}`);

		await screen.findByText('过滤出来的任务');

		expect(mock.viewRequests[0]!.pathname).toBe(`/api/v1/projects/${PSEUDO_PROJECT_ID}/views`);
		expect(mock.taskRequests[0]!.pathname).toBe(
			`/api/v1/projects/${PSEUDO_PROJECT_ID}/views/${LIST_VIEW_ID}/tasks`,
		);
	});

	it('渲染筛选器标题与条件', async () => {
		mockFilter();
		renderApp(`/filters/${FILTER_ID}`);

		expect(await screen.findByRole('heading', { name: '我的未完成' })).toBeInTheDocument();
		expect(screen.getByTestId('filter-expression')).toHaveTextContent('done = false');
	});

	it.each(['0', '-3', 'abc', 'new'])('非法 filter id「%s」给提示且不打接口', async (raw) => {
		let requested = false;
		server.use(
			http.get(`${API}/filters/:id`, () => {
				requested = true;
				return HttpResponse.json(savedFilter());
			}),
		);

		renderApp(`/filters/${raw}`);

		expect(await screen.findByTestId('invalid-filter-route')).toHaveTextContent('无效的筛选器');
		await waitFor(() => expect(requested).toBe(false));
	});

	it('筛选器不存在时展示后端消息', async () => {
		server.use(
			http.get(`${API}/filters/${FILTER_ID}`, () =>
				HttpResponse.json({ code: 3001, message: '筛选器不存在' }, { status: 404 }),
			),
		);
		renderApp(`/filters/${FILTER_ID}`);

		expect(await screen.findByRole('alert')).toHaveTextContent('筛选器不存在');
	});

	it('伪项目没有列表视图时如实报错', async () => {
		mockFilter();
		server.use(http.get(`${API}/projects/:projectId/views`, () => listResponse([])));
		renderApp(`/filters/${FILTER_ID}`);

		expect(await screen.findByTestId('filter-missing-view')).toHaveTextContent('没有列表视图');
	});
});

describe('筛选器管理：重命名与删除', () => {
	it('重命名发 POST 到 /filters/{id}', async () => {
		const mock = mockFilter();
		renderApp(`/filters/${FILTER_ID}`);

		await userEvent.click(await screen.findByTestId('filter-rename'));
		const input = screen.getByTestId('filter-title-input');
		await userEvent.clear(input);
		await userEvent.type(input, '改名了');
		await userEvent.click(screen.getByTestId('filter-rename-save'));

		await waitFor(() => expect(mock.posts).toHaveLength(1));
		expect(mock.posts[0]).toMatchObject({ title: '改名了' });
	});

	/**
	 * ★ `filters` 是后端 `valid:"required"` 的字段。重命名时不回传它，
	 * 过滤条件就没了 —— 用户只是改了个名字，筛选器却变成了"匹配一切"。
	 */
	it('★ 重命名时回传 filters，条件不会丢', async () => {
		const mock = mockFilter();
		renderApp(`/filters/${FILTER_ID}`);

		await userEvent.click(await screen.findByTestId('filter-rename'));
		const input = screen.getByTestId('filter-title-input');
		await userEvent.clear(input);
		await userEvent.type(input, '新名字');
		await userEvent.click(screen.getByTestId('filter-rename-save'));

		await waitFor(() => expect(mock.posts).toHaveLength(1));
		expect(mock.posts[0]!.filters).toEqual({ filter: 'done = false' });
	});

	it('★ 空标题被前端拦下，不发请求', async () => {
		const mock = mockFilter();
		renderApp(`/filters/${FILTER_ID}`);

		await userEvent.click(await screen.findByTestId('filter-rename'));
		await userEvent.clear(screen.getByTestId('filter-title-input'));
		await userEvent.click(screen.getByTestId('filter-rename-save'));

		// 只断言报了错，不断言那句话怎么写（现在来自 filters.create.titleRequired）
		expect(await screen.findByRole('alert')).toBeInTheDocument();
		expect(mock.posts).toHaveLength(0);
	});

	it('取消重命名不发请求', async () => {
		const mock = mockFilter();
		renderApp(`/filters/${FILTER_ID}`);

		await userEvent.click(await screen.findByTestId('filter-rename'));
		await userEvent.click(screen.getByTestId('filter-rename-cancel'));

		expect(await screen.findByRole('heading', { name: '我的未完成' })).toBeInTheDocument();
		expect(mock.posts).toHaveLength(0);
	});

	it('删除后跳回项目页', async () => {
		const mock = mockFilter();
		const { router } = renderApp(`/filters/${FILTER_ID}`);

		await userEvent.click(await screen.findByTestId('filter-delete'));

		await waitFor(() => expect(mock.deletes).toBe(1));
		await waitFor(() => expect(router.state.location.pathname).toBe('/projects'));
	});

	it('写失败时展示后端消息', async () => {
		mockFilter();
		server.use(
			http.post(`${API}/filters/${FILTER_ID}`, () =>
				HttpResponse.json({ code: 4001, message: '改不动' }, { status: 500 }),
			),
		);
		renderApp(`/filters/${FILTER_ID}`);

		await userEvent.click(await screen.findByTestId('filter-rename'));
		const input = screen.getByTestId('filter-title-input');
		await userEvent.clear(input);
		await userEvent.type(input, 'x');
		await userEvent.click(screen.getByTestId('filter-rename-save'));

		expect(await screen.findByTestId('filter-error')).toHaveTextContent('改不动');
	});
});
