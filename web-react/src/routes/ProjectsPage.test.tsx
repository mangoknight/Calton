import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { Project } from '@/api/projects';
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

/** 树里的项目链接按 DOM 顺序取，用来断言层级与排序。 */
function renderedTitles() {
	return within(screen.getByRole('tree'))
		.getAllByRole('link')
		.map((a) => a.textContent);
}

describe('项目页', () => {
	it('三层嵌套渲染出来，缩进随层级递增', async () => {
		mockProjects([project(1), project(2, 1), project(3, 2)]);

		renderApp('/projects');
		expect(await screen.findByRole('tree')).toBeInTheDocument();

		expect(renderedTitles()).toEqual(['P1', 'P2', 'P3']);

		const items = within(screen.getByRole('tree')).getAllByRole('treeitem');
		expect(items).toHaveLength(3);
		// 每层缩进 16px
		const paddings = items.map((li) => (li.firstElementChild as HTMLElement).style.paddingLeft);
		expect(paddings).toEqual(['0px', '16px', '32px']);
	});

	it('折叠父节点后子节点从 DOM 消失，再展开回来', async () => {
		mockProjects([project(1), project(2, 1)]);

		renderApp('/projects');
		expect(await screen.findByRole('tree')).toBeInTheDocument();
		expect(renderedTitles()).toEqual(['P1', 'P2']);

		await userEvent.click(screen.getByTestId('project-toggle-1'));
		expect(renderedTitles()).toEqual(['P1']);

		await userEvent.click(screen.getByTestId('project-toggle-1'));
		expect(renderedTitles()).toEqual(['P1', 'P2']);
	});

	it('★ parent 成环时不死循环：环外项目正常渲染，环内项目告警并平铺', async () => {
		mockProjects([project(1), project(2, 1), project(10, 11), project(11, 10)]);

		renderApp('/projects');

		const warning = await screen.findByTestId('cycle-warning');
		expect(warning).toHaveTextContent('有 2 个项目的上级项目形成了循环');
		// 环内项目仍可点击，用户能自己去改上级项目
		expect(within(warning).getByRole('link', { name: 'P10' })).toHaveAttribute(
			'href',
			'/projects/10/list',
		);

		// 环没有连累正常的树
		expect(renderedTitles()).toEqual(['P1', 'P2']);
	});

	it('全部项目都在环里时，树为空但项目不会凭空消失', async () => {
		mockProjects([project(1, 2), project(2, 1)]);

		renderApp('/projects');

		const warning = await screen.findByTestId('cycle-warning');
		expect(within(warning).getAllByRole('link')).toHaveLength(2);
		expect(screen.queryByRole('tree')).not.toBeInTheDocument();
	});

	it('空项目列表渲染空态而不是空白', async () => {
		mockProjects([]);

		renderApp('/projects');
		// 空态：断言"树没有渲染出任何 treeitem"，不按空态那句话
		await screen.findByTestId('projects-page');
		expect(screen.queryByRole('treeitem')).not.toBeInTheDocument();
		expect(screen.queryByTestId('cycle-warning')).not.toBeInTheDocument();
	});

	it('接口报错时展示后端消息', async () => {
		server.use(
			http.get(`${API}/projects`, () =>
				HttpResponse.json({ code: 3001, message: '出错了' }, { status: 500 }),
			),
		);

		renderApp('/projects');
		expect(await screen.findByRole('alert')).toHaveTextContent('出错了');
	});

	it('项目链接指向视图容器路由', async () => {
		mockProjects([project(7)]);

		renderApp('/projects');
		expect(await screen.findByRole('link', { name: 'P7' })).toHaveAttribute(
			'href',
			'/projects/7/list',
		);
	});
});
