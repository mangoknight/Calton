import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { Bucket } from '@/api/buckets';
import type { Task } from '@/api/tasks';
import type { ProjectView, ViewKind } from '@/api/views';
import { ZERO_TIME } from '@/lib/datetime';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';
const PROJECT_ID = 12;
const KANBAN_VIEW_ID = 4;

function view(id: number, view_kind: ViewKind): ProjectView {
	return { id, project_id: PROJECT_ID, title: view_kind, view_kind };
}

function mockViews() {
	server.use(
		http.get(`${API}/projects/:projectId/views`, () => {
			const views = [view(1, 'list'), view(KANBAN_VIEW_ID, 'kanban')];
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

function bucket(id: number, overrides: Partial<Bucket> = {}): Bucket {
	return {
		id,
		title: `列 ${id}`,
		project_view_id: KANBAN_VIEW_ID,
		count: overrides.tasks?.length ?? 0,
		limit: 0,
		...overrides,
	};
}

interface BoardMock {
	/** 板面被重新拉取的次数——写操作后必须失效重取。 */
	fetches(): number;
	writes: { method: string; url: string; body: unknown }[];
}

function mockBoard(buckets: Bucket[]): BoardMock {
	let fetches = 0;
	const writes: BoardMock['writes'] = [];

	server.use(
		http.get(`${API}/projects/:projectId/views/:viewId/tasks`, () => {
			fetches += 1;
			return HttpResponse.json(buckets, {
				headers: {
					'x-pagination-result-count': String(buckets.length),
					'x-pagination-total-pages': buckets.length ? '1' : '0',
				},
			});
		}),
		http.put(`${API}/projects/:projectId/views/:viewId/buckets`, async ({ request }) => {
			writes.push({ method: 'PUT', url: request.url, body: await request.json() });
			return HttpResponse.json(bucket(99));
		}),
		http.post(`${API}/projects/:projectId/views/:viewId/buckets/:bucketId`, async ({ request }) => {
			writes.push({ method: 'POST', url: request.url, body: await request.json() });
			return HttpResponse.json(bucket(1));
		}),
		http.delete(`${API}/projects/:projectId/views/:viewId/buckets/:bucketId`, ({ request }) => {
			writes.push({ method: 'DELETE', url: request.url, body: null });
			return new HttpResponse(null, { status: 204 });
		}),
	);

	return { fetches: () => fetches, writes };
}

function renderKanban() {
	mockViews();
	return renderApp(`/projects/${PROJECT_ID}/kanban`);
}

describe('看板：静态渲染', () => {
	it('每个桶渲染成一列，任务渲染成卡片', async () => {
		mockBoard([
			bucket(1, { title: 'To-Do', tasks: [task(7), task(8)], count: 2 }),
			bucket(2, { title: 'Done', tasks: [], count: 0 }),
		]);
		renderKanban();

		const columns = await screen.findAllByTestId('bucket-column');
		expect(columns).toHaveLength(2);
		expect(columns[0]).toHaveAttribute('data-bucket-id', '1');
		expect(within(columns[0]!).getAllByTestId('task-card')).toHaveLength(2);
		expect(within(columns[0]!).getByRole('link', { name: /任务 7/ })).toHaveAttribute(
			'href',
			'/tasks/7',
		);
		expect(within(columns[1]!).queryAllByTestId('task-card')).toHaveLength(0);
	});

	it('★ 卡片上的到期日零值不渲染成公元 1 年', async () => {
		mockBoard([bucket(1, { tasks: [task(7, { due_date: ZERO_TIME })], count: 1 })]);
		renderKanban();

		await screen.findByTestId('task-card');
		expect(screen.queryByText(/0001-01-01/)).not.toBeInTheDocument();
	});

	it('一个桶都没有时给空态', async () => {
		mockBoard([]);
		renderKanban();

		expect(await screen.findByTestId('kanban-empty')).toHaveTextContent('这个看板还没有列');
	});

	it('接口报错时展示消息', async () => {
		mockViews();
		server.use(
			http.get(`${API}/projects/:projectId/views/:viewId/tasks`, () =>
				HttpResponse.json({ code: 3001, message: '看板取不到' }, { status: 500 }),
			),
		);
		renderApp(`/projects/${PROJECT_ID}/kanban`);

		expect(await screen.findByRole('alert')).toHaveTextContent('看板取不到');
	});

	/**
	 * ★ 多态端点：view 的 bucket_configuration_mode 为 none 时后端返回扁平任务列表。
	 * 静默渲染成空板面比报错糟糕得多——用户看到的是"我的任务全没了"。
	 */
	it('★ 后端返回扁平任务列表时如实报错，不渲染空板面', async () => {
		mockViews();
		server.use(
			http.get(`${API}/projects/:projectId/views/:viewId/tasks`, () =>
				HttpResponse.json([{ id: 9, title: '任务 9', project_id: PROJECT_ID }], {
					headers: { 'x-pagination-result-count': '1', 'x-pagination-total-pages': '1' },
				}),
			),
		);
		renderApp(`/projects/${PROJECT_ID}/kanban`);

		expect(await screen.findByRole('alert')).toHaveTextContent('bucket_configuration_mode');
		expect(screen.queryByTestId('bucket-column')).not.toBeInTheDocument();
	});
});

describe('★ 看板：limit 已满提示', () => {
	it('★ 达到上限时给出提示并标红', async () => {
		mockBoard([bucket(1, { limit: 2, count: 2, tasks: [task(1), task(2)] })]);
		renderKanban();

		const column = await screen.findByTestId('bucket-column');
		expect(column).toHaveAttribute('data-full', 'true');
		// 提示**出现**即可，那句话现在来自上游 `project.kanban.bucketLimitReached`，
		// 属于 i18n 迁移范围。真正的被测对象是"什么时候提示"（下面两条用例守边界）。
		expect(within(column).getByTestId('bucket-full-notice')).toBeInTheDocument();
		expect(within(column).getByTestId('bucket-count')).toHaveTextContent('2/2');
	});

	it('未达上限时不提示', async () => {
		mockBoard([bucket(1, { limit: 3, count: 2, tasks: [task(1), task(2)] })]);
		renderKanban();

		const column = await screen.findByTestId('bucket-column');
		expect(column).not.toHaveAttribute('data-full');
		expect(screen.queryByTestId('bucket-full-notice')).not.toBeInTheDocument();
	});

	/** ★ limit 为 0 是"不限"。判反了每一列都会挂上红色告警。 */
	it('★ limit 为 0 表示不限，不提示也不渲染成 "N/0"', async () => {
		mockBoard([bucket(1, { limit: 0, count: 99, tasks: [task(1)] })]);
		renderKanban();

		const column = await screen.findByTestId('bucket-column');
		expect(screen.queryByTestId('bucket-full-notice')).not.toBeInTheDocument();
		expect(within(column).getByTestId('bucket-count')).toHaveTextContent('99');
		expect(within(column).getByTestId('bucket-count')).not.toHaveTextContent('/0');
	});

	/**
	 * ★ count 是总数，tasks 只是当前页（每列最多取 50 条）。
	 * 拿 tasks.length 判满会在任务多于一页时判错，且用户以为这列就这么几张卡。
	 */
	it('★ count 超过本页卡片数时提示还有多少未显示，且判满用 count 而非卡片数', async () => {
		mockBoard([bucket(1, { limit: 60, count: 60, tasks: [task(1), task(2)] })]);
		renderKanban();

		const column = await screen.findByTestId('bucket-column');
		expect(within(column).getByTestId('bucket-truncated')).toHaveTextContent(
			'还有 58 个任务未显示',
		);
		// 卡片只有 2 张，但 count=60 已达 limit=60 → 必须判满
		expect(column).toHaveAttribute('data-full', 'true');
	});
});

describe('看板：列（桶）CRUD', () => {
	it('新建列发 PUT 并带上标题与上限', async () => {
		const board = mockBoard([bucket(1, { tasks: [] })]);
		renderKanban();

		await screen.findByTestId('bucket-column');
		await userEvent.click(screen.getByTestId('new-bucket'));
		await userEvent.type(screen.getByTestId('bucket-title-input'), '进行中');
		await userEvent.clear(screen.getByTestId('bucket-limit-input'));
		await userEvent.type(screen.getByTestId('bucket-limit-input'), '5');
		await userEvent.click(screen.getByTestId('bucket-form-submit'));

		await waitFor(() => expect(board.writes).toHaveLength(1));
		expect(board.writes[0]).toMatchObject({
			method: 'PUT',
			body: { title: '进行中', limit: 5 },
		});
	});

	it('空标题被前端拦下，不发请求', async () => {
		const board = mockBoard([bucket(1, { tasks: [] })]);
		renderKanban();

		await screen.findByTestId('bucket-column');
		await userEvent.click(screen.getByTestId('new-bucket'));
		await userEvent.click(screen.getByTestId('bucket-form-submit'));

		expect(await screen.findByRole('alert')).toHaveTextContent('请填写标题');
		expect(board.writes).toHaveLength(0);
	});

	it('编辑列发 POST 到该桶，表单预填当前值', async () => {
		const board = mockBoard([bucket(1, { title: 'To-Do', limit: 3, tasks: [] })]);
		renderKanban();

		await screen.findByTestId('bucket-column');
		await userEvent.click(screen.getByTestId('bucket-edit-1'));

		expect(screen.getByTestId('bucket-title-input')).toHaveValue('To-Do');
		expect(screen.getByTestId('bucket-limit-input')).toHaveValue(3);

		await userEvent.clear(screen.getByTestId('bucket-title-input'));
		await userEvent.type(screen.getByTestId('bucket-title-input'), '待办');
		await userEvent.click(screen.getByTestId('bucket-form-submit'));

		await waitFor(() => expect(board.writes).toHaveLength(1));
		expect(board.writes[0]!.method).toBe('POST');
		expect(board.writes[0]!.url).toContain('/buckets/1');
		expect(board.writes[0]!.body).toMatchObject({ title: '待办' });
	});

	/** ★ 删桶不删任务（任务回到默认列）。不说清楚用户不敢删。 */
	it('★ 删除确认里说明任务不会被删除，而是移到默认列', async () => {
		mockBoard([bucket(1, { title: 'To-Do', count: 4, tasks: [] }), bucket(2, { tasks: [] })]);
		renderKanban();

		await screen.findAllByTestId('bucket-column');
		await userEvent.click(screen.getByTestId('bucket-delete-1'));

		const dialog = await screen.findByTestId('bucket-delete-dialog');
		expect(dialog).toHaveTextContent('4 个任务不会被删除');
		expect(dialog).toHaveTextContent('移动到该看板的默认列');
	});

	it('确认后发 DELETE 并重新拉板面', async () => {
		const board = mockBoard([bucket(1, { tasks: [] }), bucket(2, { tasks: [] })]);
		renderKanban();

		await screen.findAllByTestId('bucket-column');
		const fetchesBefore = board.fetches();
		await userEvent.click(screen.getByTestId('bucket-delete-1'));
		await userEvent.click(screen.getByTestId('bucket-delete-confirm'));

		await waitFor(() => expect(board.writes).toHaveLength(1));
		expect(board.writes[0]!.method).toBe('DELETE');
		// 写操作后必须失效重取——后端一次调用会连带改别的东西，照请求体猜新状态必然猜漏
		await waitFor(() => expect(board.fetches()).toBeGreaterThan(fetchesBefore));
	});

	/**
	 * ★ 后端删最后一列会 412 + code 10003。前端先拦一道并解释，
	 * 而不是让用户点下去吃一个英文报错。
	 */
	it('★ 只剩一列时不给删，并说明原因', async () => {
		const board = mockBoard([bucket(1, { title: 'To-Do', tasks: [] })]);
		renderKanban();

		await screen.findByTestId('bucket-column');
		await userEvent.click(screen.getByTestId('bucket-delete-1'));

		const dialog = await screen.findByTestId('bucket-delete-dialog');
		// 不按那句话怎么写断言（现在是上游 `project.kanban.deleteLast`）——
		// 真正的被测对象是**删除按钮不给**，以及下面那条"一个写请求都没发"。
		expect(within(dialog).queryByTestId('bucket-delete-confirm')).not.toBeInTheDocument();
		expect(board.writes).toHaveLength(0);
	});

	it('新建成功后重新拉板面', async () => {
		const board = mockBoard([bucket(1, { tasks: [] })]);
		renderKanban();

		await screen.findByTestId('bucket-column');
		const before = board.fetches();
		await userEvent.click(screen.getByTestId('new-bucket'));
		await userEvent.type(screen.getByTestId('bucket-title-input'), '新列');
		await userEvent.click(screen.getByTestId('bucket-form-submit'));

		await waitFor(() => expect(board.fetches()).toBeGreaterThan(before));
	});

	it('写失败时在弹窗里展示后端消息，弹窗不关', async () => {
		mockBoard([bucket(1, { tasks: [] })]);
		server.use(
			http.put(`${API}/projects/:projectId/views/:viewId/buckets`, () =>
				HttpResponse.json({ code: 10002, message: '列标题不合法' }, { status: 400 }),
			),
		);
		renderKanban();

		await screen.findByTestId('bucket-column');
		await userEvent.click(screen.getByTestId('new-bucket'));
		await userEvent.type(screen.getByTestId('bucket-title-input'), 'x');
		await userEvent.click(screen.getByTestId('bucket-form-submit'));

		expect(await screen.findByText('列标题不合法')).toBeInTheDocument();
		expect(screen.getByTestId('bucket-form')).toBeInTheDocument();
	});
});

describe('看板：与容器的接线', () => {
	it('板面请求打的是 kanban view 的 id', async () => {
		mockViews();
		let path = '';
		server.use(
			http.get(`${API}/projects/:projectId/views/:viewId/tasks`, ({ request }) => {
				path = new URL(request.url).pathname;
				return HttpResponse.json([], {
					headers: { 'x-pagination-result-count': '0', 'x-pagination-total-pages': '0' },
				});
			}),
		);
		renderApp(`/projects/${PROJECT_ID}/kanban`);

		await screen.findByTestId('kanban-view');
		await waitFor(() =>
			expect(path).toBe(`/api/v1/projects/${PROJECT_ID}/views/${KANBAN_VIEW_ID}/tasks`),
		);
	});
});
