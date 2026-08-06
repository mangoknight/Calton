import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { apiClient } from '@/api/client';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';

describe('登录闸门', () => {
	it('未登录访问业务页 → 跳登录页并带上来处', async () => {
		const { router } = renderApp('/projects/3/kanban', { token: null });

		await waitFor(() => expect(router.state.location.pathname).toBe('/login'));
		expect(router.state.location.search).toBe(
			`?redirect=${encodeURIComponent('/projects/3/kanban')}`,
		);
	});

	it('已登录正常渲染业务页', async () => {
		renderApp('/labels');
		expect(await screen.findByTestId('labels-page')).toBeInTheDocument();
	});

	it('★ refresh cookie 失效（刷新失败）→ token 被清掉，界面跟着跳登录页', async () => {
		// 场景：token 过期 → 打业务接口 401 → 刷新也 401（refresh cookie 没了）→ 登出
		server.use(
			http.get(`${API}/user`, () =>
				HttpResponse.json({ code: 11, message: 'invalid token' }, { status: 401 }),
			),
			http.post(`${API}/user/token/refresh`, () =>
				HttpResponse.json({ code: 11, message: 'invalid token' }, { status: 401 }),
			),
		);

		// 进页面时带着（已失效的）token，TopBar 的 GET /user 触发上述链路
		const { router } = renderApp('/labels');

		await waitFor(() => expect(apiClient.tokens.get()).toBeNull());
		await waitFor(() => expect(router.state.location.pathname).toBe('/login'));
		// 带上来处，登录后能跳回去
		expect(router.state.location.search).toBe(`?redirect=${encodeURIComponent('/labels')}`);
	});

	it('未登录访问不存在的路径看到 404，而不是被送去登录页', async () => {
		const { router } = renderApp('/no/such/page', { token: null });

		expect(screen.getByTestId('not-found')).toBeInTheDocument();
		expect(router.state.location.pathname).toBe('/no/such/page');
	});

	it('退出登录后清 token 并回到登录页', async () => {
		const { router } = renderApp('/labels');

		// ⚠️ 按 testid 点，不按按钮文案：这条验的是"登出会清 token 并跳登录页"，
		// 与按钮上写什么字、界面是哪国语言无关（F13 规矩，见 AppShell.test.tsx 头注）
		await userEvent.click(await screen.findByTestId('logout'));

		await waitFor(() => expect(apiClient.tokens.get()).toBeNull());
		await waitFor(() => expect(router.state.location.pathname).toBe('/login'));
	});
});
