import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';
const DAY = 24 * 60 * 60 * 1000;
const iso = (offsetDays: number) => new Date(Date.now() + offsetDays * DAY).toISOString();

function paginated(items: unknown[]) {
	return HttpResponse.json(items, {
		headers: { 'x-pagination-result-count': String(items.length), 'x-pagination-total-pages': '1' },
	});
}

const TASKS = [
	{ id: 1, title: '过期的活', project_id: 1, done: false, percent_done: 0, due_date: iso(-3), assignees: [{ id: 10, name: 'Alice' }] },
	{ id: 2, title: '快到期的活', project_id: 1, done: false, percent_done: 50, due_date: iso(2), assignees: [{ id: 10, name: 'Alice' }, { id: 11, name: 'Bob' }] },
	{ id: 3, title: '已完成', project_id: 2, done: true, percent_done: 100, assignees: [{ id: 11, name: 'Bob' }] },
	{ id: 4, title: '无主待办', project_id: 2, done: false, percent_done: 0, assignees: [] },
];
const PROJECTS = [
	{ id: 1, title: '项目甲' },
	{ id: 2, title: '项目乙' },
	{ id: -3, title: '伪项目' },
];

function mock() {
	server.use(
		http.get(`${API}/tasks`, () => paginated(TASKS)),
		http.get(`${API}/projects`, () => paginated(PROJECTS)),
	);
}

describe('管理面板', () => {
	it('指标卡汇总总数/状态/逾期/即将到期', async () => {
		mock();
		renderApp('/dashboard');
		await screen.findByTestId('dashboard-page');

		const metrics = screen.getByTestId('dashboard-metrics');
		const nums = within(metrics)
			.getAllByTestId('dashboard-metric')
			.map((c) => c.querySelector('div:last-child')?.textContent);
		// 顺序：总任务 待办 进行中 已完成 逾期 7天内
		expect(nums).toEqual(['4', '2', '1', '1', '1', '1']);
	});

	it('人员负载：多指派各记一次 + 未分配行', async () => {
		mock();
		renderApp('/dashboard');
		await screen.findByTestId('dashboard-page');

		const rows = screen.getAllByTestId('workload-row');
		const ids = rows.map((r) => r.getAttribute('data-user-id'));
		expect(ids).toContain('10'); // Alice
		expect(ids).toContain('11'); // Bob
		expect(ids).toContain('0'); // 未分配
		// Alice 有 2 个未完成（逾期 + 快到期）
		const alice = rows.find((r) => r.getAttribute('data-user-id') === '10')!;
		expect(within(alice).getByText('2')).toBeInTheDocument();
	});

	it('项目进度：只列真实项目，含完成率', async () => {
		mock();
		renderApp('/dashboard');
		await screen.findByTestId('dashboard-page');

		const rows = screen.getAllByTestId('project-row');
		const ids = rows.map((r) => r.getAttribute('data-project-id'));
		expect(ids).toEqual(expect.arrayContaining(['1', '2']));
		expect(ids).not.toContain('-3'); // 伪项目排除
	});

	it('逾期与即将到期清单', async () => {
		mock();
		renderApp('/dashboard');
		await screen.findByTestId('dashboard-page');

		const dueLists = screen.getAllByTestId('due-list');
		expect(within(dueLists[0]).getByText('过期的活')).toBeInTheDocument(); // 逾期列表
		expect(within(dueLists[1]).getByText('快到期的活')).toBeInTheDocument(); // 即将到期列表
	});
});
