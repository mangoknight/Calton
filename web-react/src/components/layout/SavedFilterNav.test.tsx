import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { Project } from '@/api/projects';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';

function project(id: number, title: string): Project {
	return { id, title, parent_project_id: 0 };
}

/**
 * ⚠️ 侧栏的筛选器清单来自 `GET /projects` 里的**负 ID 伪项目**，
 * 不是某个 `/filters` 列表端点（那个不存在）。mock 如实照做。
 */
function mockProjects(items: Project[]) {
	server.use(
		http.get(`${API}/projects`, () =>
			HttpResponse.json(items, {
				headers: {
					// ⚠️ 伪项目不参与分页计数：resultCount 只算真实项目，
					// 所以这里故意让 items.length > resultCount，与后端一致
					'x-pagination-result-count': String(items.filter((p) => p.id > 0).length),
					'x-pagination-total-pages': '1',
				},
			}),
		),
	);
}

describe('★ 侧栏：保存的筛选器', () => {
	it('★ 负 ID 伪项目渲染成筛选器链接，指向正的 filter id', async () => {
		// filter 1 → 伪项目 -2；filter 2 → 伪项目 -3
		mockProjects([project(1, '真实项目'), project(-2, '我的未完成'), project(-3, '本周')]);
		renderApp('/projects');

		const links = await screen.findAllByTestId('saved-filter-link');
		expect(links).toHaveLength(2);
		expect(links[0]).toHaveAttribute('href', '/filters/1');
		expect(links[0]).toHaveAttribute('data-project-id', '-2');
		expect(links[1]).toHaveAttribute('href', '/filters/2');
		expect(links[1]).toHaveTextContent('本周');
	});

	/**
	 * ★★ `-1` 是**收藏夹**，不是 saved filter。
	 * 判据写成 `<= -1` 会让收藏夹也冒充成一条筛选器，链接指向 `/filters/0`
	 * （后端视 filterID 0 为无效）。
	 */
	it('★★ 收藏夹（-1）不出现在筛选器清单里', async () => {
		mockProjects([project(1, '真实项目'), project(-1, '收藏夹'), project(-2, '我的未完成')]);
		renderApp('/projects');

		const links = await screen.findAllByTestId('saved-filter-link');
		expect(links).toHaveLength(1);
		expect(links[0]).toHaveTextContent('我的未完成');
		expect(screen.queryByText('收藏夹')).not.toBeInTheDocument();
	});

	it('没有筛选器时不渲染这一栏', async () => {
		mockProjects([project(1, '真实项目')]);
		renderApp('/projects');

		await screen.findByTestId('app-sidebar');
		await waitFor(() => expect(screen.queryByTestId('saved-filter-nav')).not.toBeInTheDocument());
	});

	/** 真实项目不会跑到筛选器栏里去。 */
	it('真实项目不出现在筛选器清单里', async () => {
		mockProjects([project(1, '真实项目'), project(-2, '我的未完成')]);
		renderApp('/projects');

		const nav = await screen.findByTestId('saved-filter-nav');
		expect(nav).not.toHaveTextContent('真实项目');
	});
});
