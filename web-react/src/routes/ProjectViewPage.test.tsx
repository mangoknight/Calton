import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { ProjectView, ViewKind } from '@/api/views';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';
import { parseRouteId } from '@/lib/route-params';

const API = '*/api/v1';

function view(id: number, view_kind: ViewKind, project_id = 12): ProjectView {
	return { id, project_id, title: view_kind, view_kind };
}

/** 实测：新建项目自动带出四个 view，所以默认 mock 就给四个。 */
function mockViews(
	views: ProjectView[] = [view(1, 'list'), view(2, 'gantt'), view(3, 'table'), view(4, 'kanban')],
) {
	let calls = 0;
	server.use(
		http.get(`${API}/projects/:projectId/views`, () => {
			calls += 1;
			return HttpResponse.json(views, {
				headers: {
					'x-pagination-result-count': String(views.length),
					'x-pagination-total-pages': views.length ? '1' : '0',
				},
			});
		}),
	);
	return () => calls;
}

/** List 视图（F05b）已经是真实现，会去拉任务；不 mock 的话 MSW 会按未处理请求报错。 */
function mockEmptyViewTasks() {
	server.use(
		http.get(`${API}/projects/:projectId/views/:viewId/tasks`, () =>
			HttpResponse.json([], {
				headers: { 'x-pagination-result-count': '0', 'x-pagination-total-pages': '0' },
			}),
		),
	);
}

describe('视图容器：四种 kind 共用一个容器', () => {
	/**
	 * list / table / kanban 都已是真实现（F05b / F06 / F07a），gantt 仍是占位。
	 * 「解析出对应的 view id」这件事由"任务请求打到哪个 view"来证明，比占位符上的
	 * data-view-id 更硬：它证明的是 id 真的被用出去了，而不只是渲染了一下。
	 */
	it.each([
		['list', 'list-view', 1],
		['table', 'table-view', 3],
		['kanban', 'kanban-view', 4],
	])('/projects/12/%s 渲染真实现，并把解析出的 view id 用了出去', async (kind, testId, viewId) => {
		mockViews();
		let requestedPath = '';
		server.use(
			http.get(`${API}/projects/:projectId/views/:viewId/tasks`, ({ request }) => {
				requestedPath = new URL(request.url).pathname;
				return HttpResponse.json([], {
					headers: { 'x-pagination-result-count': '0', 'x-pagination-total-pages': '0' },
				});
			}),
		);

		renderApp(`/projects/12/${kind}`);

		expect(await screen.findByTestId(testId)).toBeInTheDocument();
		await waitFor(() => expect(requestedPath).toBe(`/api/v1/projects/12/views/${viewId}/tasks`));
	});

	it('四种 kind 都有切换入口，当前 kind 标为激活', async () => {
		mockViews();
		renderApp('/projects/12/kanban');

		const nav = await screen.findByRole('navigation', { name: '视图切换' });
		['列表', '甘特图', '表格', '看板'].forEach((label) => {
			expect(within(nav).getByRole('link', { name: label })).toBeInTheDocument();
		});
		expect(nav.querySelector('a[aria-current="page"]')).toHaveTextContent('看板');
	});

	it('切换视图不重新拉 views（同一项目的视图集合应命中缓存）', async () => {
		const calls = mockViews();
		mockEmptyViewTasks();
		renderApp('/projects/12/list');
		expect(await screen.findByTestId('list-view')).toBeInTheDocument();
		expect(calls()).toBe(1);

		// 收窄到「视图切换」导航：侧栏全局看板入口也叫「看板」，整屏查会撞名
		const nav = await screen.findByRole('navigation', { name: '视图切换' });
		await userEvent.click(within(nav).getByRole('link', { name: '看板' }));

		expect(await screen.findByTestId('kanban-view')).toBeInTheDocument();
		expect(calls()).toBe(1);
	});
});

describe('★ Gantt 视图', () => {
	/** gantt 也走 view/tasks 端点，给它一条带起止日期的任务，能铺到时间轴上。 */
	function mockGanttTasks() {
		server.use(
			http.get(`${API}/projects/:projectId/views/:viewId/tasks`, () =>
				HttpResponse.json(
					[
						{
							id: 7,
							title: '甘特任务',
							start_date: '2026-08-01T00:00:00Z',
							end_date: '2026-08-10T00:00:00Z',
						},
					],
					{ headers: { 'x-pagination-result-count': '1', 'x-pagination-total-pages': '1' } },
				),
			),
		);
	}

	it('渲染真实时间轴视图（有日期的任务落到时间条上）', async () => {
		mockViews();
		mockGanttTasks();
		renderApp('/projects/12/gantt');

		expect(await screen.findByTestId('gantt-view')).toBeInTheDocument();
		expect(await screen.findByTestId('gantt-row')).toBeInTheDocument();
		// 这是真实视图不是故障，不该冒出 alert
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
	});

	it('容器本身照常渲染，仍可切回其他视图', async () => {
		mockViews();
		mockEmptyViewTasks();
		renderApp('/projects/12/gantt');

		expect(await screen.findByTestId('view-container')).toBeInTheDocument();
		await userEvent.click(screen.getByRole('link', { name: '列表' }));
		expect(await screen.findByTestId('list-view')).toBeInTheDocument();
	});
});

describe('★ 路由参数校验（动态段会吞掉非法值）', () => {
	it('/projects/new/list 不会拿 "new" 去打接口', async () => {
		// 这条是真会红的：/projects/:projectId/:view 会匹配 /projects/new/list，
		// 不校验就会用 NaN 拼出 /projects/NaN/views 打出去。
		let requested = false;
		server.use(
			http.get(`${API}/projects/:projectId/views`, () => {
				requested = true;
				return HttpResponse.json([], {
					headers: { 'x-pagination-result-count': '0', 'x-pagination-total-pages': '0' },
				});
			}),
		);

		renderApp('/projects/new/list');

		expect(await screen.findByTestId('invalid-view-route')).toHaveTextContent('无效的项目');
		// 等一拍，确认确实没有请求飞出去
		await waitFor(() => expect(requested).toBe(false));
	});

	it.each(['abc', '0', '-1', '1.5', '12abc'])('projectId=%s 被拒绝', (raw) => {
		expect(parseRouteId(raw)).toBeNull();
	});

	it.each([
		['12', 12],
		['1', 1],
	])('projectId=%s 被接受', (raw, expected) => {
		expect(parseRouteId(raw)).toBe(expected);
	});

	it('未知的 view kind 给出可理解的提示而不是崩溃', async () => {
		mockViews();
		renderApp('/projects/12/timeline');

		expect(await screen.findByTestId('invalid-view-route')).toHaveTextContent('未知的视图');
		expect(screen.getByRole('alert')).toHaveTextContent('list / gantt / table / kanban');
	});
});

describe('视图数据异常', () => {
	it('缺少对应 kind 的视图时如实报错，不静默渲染空壳', async () => {
		// 实测：新建项目必定带四个 view，缺了说明数据异常
		mockViews([view(1, 'list')]);
		renderApp('/projects/12/kanban');

		expect(await screen.findByTestId('missing-view')).toHaveTextContent('该项目没有看板视图');
	});

	it('接口报错时展示后端消息', async () => {
		server.use(
			http.get(`${API}/projects/:projectId/views`, () =>
				HttpResponse.json({ code: 3001, message: '项目不存在' }, { status: 404 }),
			),
		);

		renderApp('/projects/12/list');
		expect(await screen.findByRole('alert')).toHaveTextContent('项目不存在');
	});
});
