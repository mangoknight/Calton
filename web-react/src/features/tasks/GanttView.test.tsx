import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { Task } from '@/api/tasks';
import type { ProjectView, ViewKind } from '@/api/views';
import { ZERO_TIME } from '@/lib/datetime';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';
const PROJECT_ID = 12;
const GANTT_VIEW_ID = 2;

function view(id: number, view_kind: ViewKind): ProjectView {
	return { id, project_id: PROJECT_ID, title: view_kind, view_kind };
}

function mockViews() {
	server.use(
		http.get(`${API}/projects/:projectId/views`, () => {
			const views = [
				view(1, 'list'),
				view(GANTT_VIEW_ID, 'gantt'),
				view(3, 'table'),
				view(4, 'kanban'),
			];
			return HttpResponse.json(views, {
				headers: { 'x-pagination-result-count': '4', 'x-pagination-total-pages': '1' },
			});
		}),
	);
}

function task(id: number, overrides: Partial<Task> = {}): Task {
	return { id, title: `任务 ${id}`, ...overrides };
}

function mockGanttTasks(all: Task[]) {
	server.use(
		http.get(`${API}/projects/:projectId/views/:viewId/tasks`, () =>
			HttpResponse.json(all, {
				headers: {
					'x-pagination-result-count': String(all.length),
					'x-pagination-total-pages': all.length ? '1' : '0',
				},
			}),
		),
	);
}

function renderGantt() {
	mockViews();
	return renderApp(`/projects/${PROJECT_ID}/gantt`);
}

describe('Gantt 视图：时间轴', () => {
	it('有起止日期的任务渲染成一行，时间条带合理的位置与宽度', async () => {
		// 两条任务共同定义 [8/01, 8/20] 的全局区间；第二条占后半段
		mockGanttTasks([
			task(7, {
				identifier: 'PRJ-7',
				start_date: '2026-08-01T00:00:00Z',
				end_date: '2026-08-20T00:00:00Z',
			}),
			task(8, { start_date: '2026-08-11T00:00:00Z', end_date: '2026-08-20T00:00:00Z' }),
		]);
		renderGantt();

		const rows = await screen.findAllByTestId('gantt-row');
		expect(rows).toHaveLength(2);
		expect(rows[0]).toHaveAttribute('data-task-id', '7');
		expect(within(rows[0]!).getByRole('link', { name: /任务 7/ })).toHaveAttribute(
			'href',
			'/tasks/7',
		);
		expect(rows[0]).toHaveTextContent('PRJ-7');

		// 跨满全区间的任务：左 0、宽 100%
		const fullBar = within(rows[0]!).getByTestId('gantt-bar');
		expect(fullBar).toHaveAttribute('data-kind', 'bar');
		expect(fullBar.style.left).toBe('0%');
		expect(fullBar.style.width).toBe('100%');

		// 后半段任务：left 与 width 都在 (0,100) 之间，位置靠右
		const partialBar = within(rows[1]!).getByTestId('gantt-bar');
		const left = parseFloat(partialBar.style.left);
		const width = parseFloat(partialBar.style.width);
		expect(left).toBeGreaterThan(0);
		expect(width).toBeGreaterThan(0);
		expect(left + width).toBeLessThanOrEqual(100.01);
	});

	it('只有到期日、没有区间的任务画成当天标记而不是时间条', async () => {
		mockGanttTasks([
			task(7, {
				start_date: '2026-08-01T00:00:00Z',
				end_date: '2026-08-20T00:00:00Z',
			}),
			// 只有 due_date：单个日期 → 菱形标记
			task(9, { due_date: '2026-08-10T00:00:00Z', start_date: ZERO_TIME, end_date: ZERO_TIME }),
		]);
		renderGantt();

		await screen.findAllByTestId('gantt-row');
		// 用行的 data-task-id 定位只有到期日的第 9 行
		const row9 = screen
			.getAllByTestId('gantt-row')
			.find((el) => el.getAttribute('data-task-id') === '9');
		expect(row9).toBeDefined();
		expect(within(row9!).getByTestId('gantt-bar')).toHaveAttribute('data-kind', 'marker');
	});
});

describe('Gantt 视图：分区与空态', () => {
	it('没有任何日期的任务归入"未排期"分区，仍可点进详情', async () => {
		mockGanttTasks([
			task(7, { start_date: '2026-08-01T00:00:00Z', end_date: '2026-08-05T00:00:00Z' }),
			// 三个日期字段全是零值 → 未排期
			task(3, { start_date: ZERO_TIME, end_date: ZERO_TIME, due_date: ZERO_TIME }),
		]);
		renderGantt();

		const section = await screen.findByTestId('gantt-unscheduled');
		const row = within(section).getByTestId('gantt-unscheduled-row');
		expect(row).toHaveAttribute('data-task-id', '3');
		expect(within(row).getByRole('link', { name: /任务 3/ })).toHaveAttribute('href', '/tasks/3');
	});

	it('全部任务都没日期时不渲染时间轴，只渲染未排期分区', async () => {
		mockGanttTasks([task(1), task(2)]);
		renderGantt();

		expect(await screen.findByTestId('gantt-unscheduled')).toBeInTheDocument();
		expect(screen.queryByTestId('gantt-row')).not.toBeInTheDocument();
		expect(screen.queryByTestId('gantt-empty')).not.toBeInTheDocument();
	});

	it('一条任务都没有时给空态', async () => {
		mockGanttTasks([]);
		renderGantt();

		expect(await screen.findByTestId('gantt-empty')).toHaveTextContent('这个项目还没有任务');
		expect(screen.queryByTestId('gantt-row')).not.toBeInTheDocument();
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
	});

	it('任务接口报错时展示后端消息，不是白屏', async () => {
		mockViews();
		server.use(
			http.get(`${API}/projects/:projectId/views/:viewId/tasks`, () =>
				HttpResponse.json({ code: 4001, message: '任务列表取不到' }, { status: 500 }),
			),
		);
		renderApp(`/projects/${PROJECT_ID}/gantt`);

		expect(await screen.findByRole('alert')).toHaveTextContent('任务列表取不到');
	});
});
