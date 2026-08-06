import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { apiClient } from '@/api/client';
import { safeRedirect } from '@/lib/redirect';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';

/**
 * ⚠️ **本文件不按可见文案定位元素**（F13 规矩，模板见 `components/layout/AppShell.test.tsx`）。
 *
 * 判据：**这个断言关心的东西，会不会因为换语言而改变？** 不会的，就不该按文案查。
 * 这里验的是"token 存没存下、跳没跳转、请求体带没带 totp、空表单拦没拦住"——
 * 全都与界面写什么字无关。
 *
 * 唯一保留的文案断言是"后端错误消息原样显示"那条：**那句话来自 mock 的响应体、
 * 不是我们的语言包**，它恰恰在验"后端说什么我们就显示什么"，是被测对象本身。
 */
async function fillAndSubmit(username = 'tester', password = 'secret-pass') {
	await userEvent.type(screen.getByTestId('login-username'), username);
	await userEvent.type(screen.getByTestId('login-password'), password);
	await userEvent.click(screen.getByTestId('login-submit'));
}

describe('登录页', () => {
	it('登录成功后存下 token 并跳到首页', async () => {
		server.use(http.post(`${API}/login`, () => HttpResponse.json({ token: 'jwt-fresh' })));

		const { router } = renderApp('/login', { token: null });
		await fillAndSubmit();

		await waitFor(() => expect(router.state.location.pathname).toBe('/'));
		expect(apiClient.tokens.get()).toBe('jwt-fresh');
		expect(localStorage.getItem('calton-token')).toBe('jwt-fresh');
	});

	it('登录成功后跳回 ?redirect 指定的来处', async () => {
		server.use(http.post(`${API}/login`, () => HttpResponse.json({ token: 'jwt-fresh' })));

		const { router } = renderApp('/login?redirect=%2Flabels', { token: null });
		await fillAndSubmit();

		await waitFor(() => expect(router.state.location.pathname).toBe('/labels'));
	});

	it('密码错误时展示后端消息，且不跳转、不写 token', async () => {
		server.use(
			http.post(`${API}/login`, () =>
				HttpResponse.json({ code: 1011, message: '用户名或密码错误' }, { status: 401 }),
			),
		);

		const { router } = renderApp('/login', { token: null });
		await fillAndSubmit();

		// ⚠️ 这句话来自 **mock 的响应体**，不是我们的语言包 ——
		// 验的是"后端说什么我们就显示什么"，文案在这里是被测对象，保留
		expect(await screen.findByRole('alert')).toHaveTextContent('用户名或密码错误');
		expect(router.state.location.pathname).toBe('/login');
		expect(apiClient.tokens.get()).toBeNull();
	});

	it('空表单被前端拦下，不发请求', async () => {
		let calls = 0;
		server.use(
			http.post(`${API}/login`, () => {
				calls += 1;
				return HttpResponse.json({ token: 'x' });
			}),
		);

		renderApp('/login', { token: null });
		await userEvent.click(screen.getByTestId('login-submit'));

		// 按**字段**断言报错，不按错误文案 —— 校验消息属于 i18n 迁移范围
		expect(await screen.findByTestId('username-error')).toBeInTheDocument();
		expect(screen.getByTestId('password-error')).toBeInTheDocument();
		expect(calls).toBe(0);
	});

	it('两步验证码留空时不把空串发给后端（会被当成错误验证码）', async () => {
		let body: Record<string, unknown> = {};
		server.use(
			http.post(`${API}/login`, async ({ request }) => {
				body = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json({ token: 'jwt-fresh' });
			}),
		);

		renderApp('/login', { token: null });
		await fillAndSubmit();

		await waitFor(() => expect(body.username).toBe('tester'));
		expect(body).not.toHaveProperty('totp_passcode');
	});

	it('填了验证码就带上', async () => {
		let body: Record<string, unknown> = {};
		server.use(
			http.post(`${API}/login`, async ({ request }) => {
				body = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json({ token: 'jwt-fresh' });
			}),
		);

		renderApp('/login', { token: null });
		await userEvent.type(screen.getByTestId('login-username'), 'tester');
		await userEvent.type(screen.getByTestId('login-password'), 'secret-pass');
		await userEvent.type(screen.getByTestId('login-totp'), '123456');
		await userEvent.click(screen.getByTestId('login-submit'));

		await waitFor(() => expect(body.totp_passcode).toBe('123456'));
	});
});

describe('safeRedirect', () => {
	it.each([
		['/labels', '/labels'],
		['/projects/1/kanban?x=1', '/projects/1/kanban?x=1'],
		[null, '/'],
		['', '/'],
		// 开放重定向：// 会被浏览器当协议相对 URL 跳出站
		['//evil.com', '/'],
		['https://evil.com', '/'],
		// 跳回登录页会形成来回弹
		['/login', '/'],
		['/register', '/'],
		// 反斜杠变体：部分浏览器把 \\ 规范化成 /，当前消费点是 navigate() 不可利用，先堵上
		['/\\evil.com', '/'],
		['\\\\evil.com', '/'],
		['/labels\\..\\evil', '/'],
	])('%s → %s', (input, expected) => {
		expect(safeRedirect(input)).toBe(expected);
	});
});
