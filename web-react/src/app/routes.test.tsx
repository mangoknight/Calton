import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

describe('Phase 1 路由骨架', () => {
	/*
	 * 按 **testid 认页面**，不按标题文字。
	 *
	 * ⚠️ 这里原来是 `['/', '首页']` 这样的文案表。F13 迁移 HomePage 时我第一反应是
	 * 把 `'首页'` 改成上游译文 `'概览'` —— **那只是把脆弱性从一套文案搬到另一套**，
	 * 下次换 key 照样红。这条用例验的是"路由落在 AppShell 里、挂了侧边栏和顶栏"，
	 * 与页面标题写什么无关。
	 */
	it.each([
		['/', 'home-page'],
		['/projects', 'projects-page'],
		['/tasks/by/upcoming', 'placeholder'],
		['/labels', 'labels-page'],
	])('%s 渲染在 AppShell 内', (path, testId) => {
		renderApp(path);
		expect(screen.getByTestId(testId)).toBeInTheDocument();
		expect(screen.getByTestId('app-sidebar')).toBeInTheDocument();
		expect(screen.getByTestId('app-topbar')).toBeInTheDocument();
	});

	// 按 testid 认页面，不按标题文字 —— 标题属于 i18n 迁移范围（F13），
	// 而这条验的是"落在 AuthLayout 里、且没有侧边栏"，与标题写什么无关
	it.each([
		['/login', 'login-page'],
		['/register', 'register-page'],
	])('%s 走 AuthLayout，不带侧边栏', (path, testId) => {
		renderApp(path);
		expect(screen.getByTestId(testId)).toBeInTheDocument();
		expect(screen.getByTestId('auth-layout')).toBeInTheDocument();
		expect(screen.queryByTestId('app-sidebar')).not.toBeInTheDocument();
	});

	/** 任务详情已由 F08a 接管，不再是占位页（内容由 TaskDetailPage.test.tsx 覆盖）。 */
	it('/tasks/:taskId 落到任务详情页', async () => {
		server.use(http.get('*/api/v1/tasks/42', () => HttpResponse.json({ id: 42, title: '某任务' })));
		renderApp('/tasks/42');

		expect(await screen.findByTestId('task-detail')).toBeInTheDocument();
		expect(screen.getByTestId('app-sidebar')).toBeInTheDocument();
		expect(screen.getByTestId('app-topbar')).toBeInTheDocument();
	});

	/**
	 * 保存的筛选器页已由 F11b 接管，不再是占位页（内容由 FilterPage.test.tsx 覆盖）。
	 *
	 * ⚠️ 这里断言的是**路由挂对了页面**，不是页面内容。用 `filter-page` 这个
	 * testid 而不是标题文字 —— 标题是接口返回的数据，F13 之后文案还会再变，
	 * 用文案钉在这里等于让路由测试跟着文案一起红。
	 */
	it('/filters/:filterId 落到筛选器页（内容由 FilterPage.test.tsx 覆盖）', async () => {
		server.use(
			http.get('*/api/v1/filters/7', () =>
				HttpResponse.json({ id: 7, title: '我的未完成', filters: { filter: 'done = false' } }),
			),
		);
		renderApp('/filters/7');

		expect(await screen.findByTestId('filter-page')).toBeInTheDocument();
		expect(screen.getByTestId('app-sidebar')).toBeInTheDocument();
		expect(screen.getByTestId('app-topbar')).toBeInTheDocument();
	});

	it('/projects/:id/:view 落到视图容器（内容由 ProjectViewPage.test.tsx 覆盖）', async () => {
		renderApp('/projects/12/kanban');
		expect(await screen.findByTestId('view-container')).toBeInTheDocument();
		expect(screen.getByTestId('app-sidebar')).toBeInTheDocument();
	});

	it('未知路径渲染 404 而不是抛错', () => {
		renderApp('/no/such/page');
		expect(screen.getByTestId('not-found')).toBeInTheDocument();
	});

	/**
	 * ⚠️ 如实说明：**这条目前不可能失败**，不计入有效覆盖。
	 * React Router v7 按路径特异性排序而非声明顺序，静态段天然赢过动态段，
	 * tester 用三种变异（含 splat）都没能弄红它。保留作为未来的绊线 ——
	 * 真正会出事的形状是 F05a 的 /projects/:projectId/:view 落地后
	 * /projects/new 被动态段吞掉，到时候补能红的真测试。
	 */
	it('/tasks/by/upcoming 不被 /tasks/:taskId 抢走', () => {
		renderApp('/tasks/by/upcoming');
		expect(screen.getByRole('heading', { name: '即将到期' })).toBeInTheDocument();
	});
});
