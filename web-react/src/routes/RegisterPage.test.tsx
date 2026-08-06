import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { apiClient } from '@/api/client';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';

async function fill({
	username = 'newbie',
	email = 'newbie@example.com',
	password = 'secret-pass',
} = {}) {
	await userEvent.type(screen.getByTestId('register-username'), username);
	await userEvent.type(screen.getByTestId('register-email'), email);
	await userEvent.type(screen.getByTestId('register-password'), password);
	await userEvent.click(screen.getByTestId('register-submit'));
}

describe('注册页', () => {
	it('注册成功后自动登录并进首页（注册接口只回 user，不回 token）', async () => {
		let registerBody: Record<string, unknown> = {};
		server.use(
			http.post(`${API}/register`, async ({ request }) => {
				registerBody = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json({ id: 2, username: 'newbie' });
			}),
			http.post(`${API}/login`, () => HttpResponse.json({ token: 'jwt-new-user' })),
		);

		const { router } = renderApp('/register', { token: null });
		await fill();

		await waitFor(() => expect(router.state.location.pathname).toBe('/'));
		expect(apiClient.tokens.get()).toBe('jwt-new-user');
		expect(registerBody).toEqual({
			username: 'newbie',
			email: 'newbie@example.com',
			password: 'secret-pass',
		});
	});

	it('用户名被占用时展示后端消息，不跳转', async () => {
		server.use(
			http.post(`${API}/register`, () =>
				HttpResponse.json({ code: 1002, message: '用户名已被占用' }, { status: 400 }),
			),
		);

		const { router } = renderApp('/register', { token: null });
		await fill();

		// 来自 mock 响应体，不是语言包 —— 保留（同 LoginPage.test.tsx 头注）
		expect(await screen.findByRole('alert')).toHaveTextContent('用户名已被占用');
		expect(router.state.location.pathname).toBe('/register');
		expect(apiClient.tokens.get()).toBeNull();
	});

	it('前端按契约拦下过短的用户名与密码，不发请求', async () => {
		let calls = 0;
		server.use(
			http.post(`${API}/register`, () => {
				calls += 1;
				return HttpResponse.json({ id: 2 });
			}),
		);

		renderApp('/register', { token: null });
		await fill({ username: 'ab', password: 'short' });

		// 按字段断言，不按校验文案（同 LoginPage.test.tsx 头注）
		expect(await screen.findByTestId('username-error')).toBeInTheDocument();
		expect(screen.getByTestId('password-error')).toBeInTheDocument();
		expect(calls).toBe(0);
	});

	it('邮箱格式不合法时拦下', async () => {
		renderApp('/register', { token: null });
		await fill({ email: 'not-an-email' });

		expect(await screen.findByTestId('email-error')).toBeInTheDocument();
	});
});
