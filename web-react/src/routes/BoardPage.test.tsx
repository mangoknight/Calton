import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';

function paginated(items: unknown[], totalPages = 1) {
	return HttpResponse.json(items, {
		headers: {
			'x-pagination-result-count': String(items.length),
			'x-pagination-total-pages': String(totalPages),
		},
	});
}

const TASKS = [
	// 甲：无进度未完成 → 待办；多 assignee
	{ id: 1, title: '甲任务', project_id: 1, done: false, percent_done: 0, assignees: [{ id: 10, name: 'Alice' }, { id: 11, name: 'Bob' }] },
	// 乙：有进度未完成 → 进行中
	{ id: 2, title: '乙任务', project_id: 2, done: false, percent_done: 40, assignees: [{ id: 11, name: 'Bob' }] },
	// 无主：已完成 → 已完成；未分配
	{ id: 3, title: '无主任务', project_id: 1, done: true, percent_done: 100, assignees: [] },
];

const PROJECTS = [
	{ id: 1, title: '项目甲' },
	{ id: 2, title: '项目乙' },
	{ id: -3, title: '某筛选器' }, // 伪项目，应被排除
];

function mockBoard(tasks: unknown[] = TASKS, totalPages = 1) {
	server.use(
		http.get(`${API}/tasks`, () => paginated(tasks, totalPages)),
		http.get(`${API}/projects`, () => paginated(PROJECTS)),
	);
}

function column(key: string) {
	return screen
		.getAllByTestId('board-column')
		.find((el) => el.getAttribute('data-column-key') === key);
}

describe('全局按人看板', () => {
	it('列 = assignee，外加「未分配」列', async () => {
		mockBoard();
		renderApp('/board');

		await screen.findByTestId('board-page');
		const keys = screen.getAllByTestId('board-column').map((el) => el.getAttribute('data-column-key'));
		expect(keys).toEqual(['10', '11', 'none']); // Alice, Bob, 未分配
	});

	it('★ 多 assignee 的任务在每个人的列里都出现', async () => {
		mockBoard();
		renderApp('/board');
		await screen.findByTestId('board-page');

		expect(within(column('10')!).getByText('甲任务')).toBeInTheDocument(); // Alice
		expect(within(column('11')!).getByText('甲任务')).toBeInTheDocument(); // Bob 也有
		expect(within(column('11')!).getByText('乙任务')).toBeInTheDocument();
		expect(within(column('none')!).getByText('无主任务')).toBeInTheDocument();
	});

	it('项目过滤：只保留选中项目的任务', async () => {
		mockBoard();
		renderApp('/board');
		await screen.findByTestId('board-page');

		// 选「项目甲」→ 只剩 project 1 的任务（甲任务、无主任务），乙任务消失
		const jia = screen.getAllByTestId('board-project-chip').find((b) => b.textContent === '项目甲')!;
		await userEvent.click(jia);

		await waitFor(() => expect(screen.queryByText('乙任务')).not.toBeInTheDocument());
		// 甲任务是多 assignee，在 Alice / Bob 两列都出现 —— 用 getAllByText
		expect(screen.getAllByText('甲任务').length).toBeGreaterThan(0);
		expect(screen.getByText('无主任务')).toBeInTheDocument();
	});

	it('人过滤：只显示选中的人列', async () => {
		mockBoard();
		renderApp('/board');
		await screen.findByTestId('board-page');

		const alice = screen.getAllByTestId('board-user-chip').find((b) => b.textContent === 'Alice')!;
		await userEvent.click(alice);

		await waitFor(() => {
			const keys = screen
				.getAllByTestId('board-column')
				.map((el) => el.getAttribute('data-column-key'));
			expect(keys).toEqual(['10']); // 只剩 Alice
		});
	});

	it('伪项目（负 id）不进项目过滤器', async () => {
		mockBoard();
		renderApp('/board');
		await screen.findByTestId('board-page');

		const labels = screen.getAllByTestId('board-project-chip').map((b) => b.textContent);
		expect(labels).toEqual(['项目甲', '项目乙']);
		expect(labels).not.toContain('某筛选器');
	});

	it('★ 切「按状态」：列 = 待办/进行中/已完成，任务按 done+percent 落列', async () => {
		mockBoard();
		renderApp('/board');
		await screen.findByTestId('board-page');

		const byStatus = screen
			.getAllByTestId('board-groupby-option')
			.find((b) => b.textContent === '按状态')!;
		await userEvent.click(byStatus);

		await waitFor(() => {
			const keys = screen
				.getAllByTestId('board-column')
				.map((el) => el.getAttribute('data-column-key'));
			expect(keys).toEqual(['todo', 'doing', 'done']);
		});
		// 状态模式下任务只出现一次
		expect(within(column('todo')!).getByText('甲任务')).toBeInTheDocument();
		expect(within(column('doing')!).getByText('乙任务')).toBeInTheDocument();
		expect(within(column('done')!).getByText('无主任务')).toBeInTheDocument();
	});

	it('任务超过护栏页数时提示已截断', async () => {
		mockBoard(TASKS, 99); // total-pages 远超 MAX_PAGES
		renderApp('/board');
		await screen.findByTestId('board-page');

		expect(await screen.findByTestId('board-truncated')).toBeInTheDocument();
	});
});
