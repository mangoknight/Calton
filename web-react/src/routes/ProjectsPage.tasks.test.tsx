import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { Project } from '@/api/projects';
import type { Task } from '@/api/tasks';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';

function project(id: number, parent = 0, extra: Partial<Project> = {}): Project {
	return { id, title: `P${id}`, parent_project_id: parent, ...extra };
}

function mockProjects(items: Project[]) {
	server.use(
		http.get(`${API}/projects`, () =>
			HttpResponse.json(items, {
				headers: {
					'x-pagination-result-count': String(items.length),
					'x-pagination-total-pages': items.length ? '1' : '0',
				},
			}),
		),
	);
}

function mockProjectTasks(projectId: number, tasks: Task[]) {
	server.use(
		http.get(`${API}/projects/${projectId}/tasks`, () =>
			HttpResponse.json(tasks, {
				headers: {
					'x-pagination-result-count': String(tasks.length),
					'x-pagination-total-pages': tasks.length ? '1' : '0',
				},
			}),
		),
	);
}

describe('项目页 · 展开任务/子任务', () => {
	it('点开任务后懒加载出该项目的任务，父任务下嵌套子任务', async () => {
		mockProjects([project(1)]);
		mockProjectTasks(1, [
			{ id: 10, title: '父任务', related_tasks: { subtask: [{ id: 11, title: '子任务' }] } },
			{ id: 11, title: '子任务', related_tasks: { parenttask: [{ id: 10, title: '父任务' }] } },
		]);

		renderApp('/projects');
		expect(await screen.findByRole('tree')).toBeInTheDocument();

		// 未点开之前不发任务请求、不渲染任务
		expect(screen.queryByTestId('project-task-10')).not.toBeInTheDocument();

		await userEvent.click(screen.getByTestId('project-tasks-toggle-1'));

		// 懒加载出父任务，子任务嵌在其下
		expect(await screen.findByTestId('project-task-10')).toHaveTextContent('父任务');
		const childLink = await screen.findByTestId('project-task-11');
		expect(childLink).toHaveTextContent('子任务');

		// 子任务缩进比父任务深（每层 16px）
		const parentRow = screen.getByTestId('project-task-10').closest('div') as HTMLElement;
		const childRow = childLink.closest('div') as HTMLElement;
		const px = (el: HTMLElement) => Number.parseInt(el.style.paddingLeft, 10);
		expect(px(childRow)).toBeGreaterThan(px(parentRow));

		// 折叠子任务后从 DOM 消失
		await userEvent.click(screen.getByTestId('task-toggle-10'));
		expect(screen.queryByTestId('project-task-11')).not.toBeInTheDocument();
	});

	it('空项目展示占位而不是报错', async () => {
		mockProjects([project(1)]);
		mockProjectTasks(1, []);

		renderApp('/projects');
		expect(await screen.findByRole('tree')).toBeInTheDocument();
		await userEvent.click(screen.getByTestId('project-tasks-toggle-1'));

		expect(await screen.findByText('该项目暂无任务')).toBeInTheDocument();
	});

	it('再次点击收起任务', async () => {
		mockProjects([project(1)]);
		mockProjectTasks(1, [{ id: 10, title: '孤立任务' }]);

		renderApp('/projects');
		expect(await screen.findByRole('tree')).toBeInTheDocument();

		const toggle = screen.getByTestId('project-tasks-toggle-1');
		await userEvent.click(toggle);
		expect(await screen.findByTestId('project-task-10')).toBeInTheDocument();

		await userEvent.click(toggle);
		expect(screen.queryByTestId('project-task-10')).not.toBeInTheDocument();
	});
});
