import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';
import { renderWithProviders } from '@/test/render';
import { QuickAddTask } from './QuickAddTask';

const API = '*/api/v1';
const PROJECT_ID = 12;

interface CreateMock {
	/** 每次 PUT 的请求体，用来断言标题真的发出去了。 */
	bodies: unknown[];
}

/** ⚠️ 新建走 PUT（v1 里 PUT 才是新建），mock 也拦 PUT。 */
function mockCreate(): CreateMock {
	const bodies: unknown[] = [];
	server.use(
		http.put(`${API}/projects/${PROJECT_ID}/tasks`, async ({ request }) => {
			bodies.push(await request.json());
			return HttpResponse.json({ id: 1, title: '新任务' });
		}),
	);
	return { bodies };
}

describe('QuickAddTask：新建任务', () => {
	it('输入标题并提交，发 PUT 且成功后清空输入', async () => {
		const mock = mockCreate();
		renderWithProviders(<QuickAddTask projectId={PROJECT_ID} />);

		const input = screen.getByTestId('quick-add-input');
		await userEvent.type(input, '写测试');
		await userEvent.click(screen.getByTestId('quick-add-submit'));

		await waitFor(() => expect(mock.bodies).toHaveLength(1));
		expect(mock.bodies[0]).toMatchObject({ title: '写测试' });
		// 成功后输入框清空，便于连续建多条
		await waitFor(() => expect(input).toHaveValue(''));
	});

	it('回车即提交（trim 后的标题）', async () => {
		const mock = mockCreate();
		renderWithProviders(<QuickAddTask projectId={PROJECT_ID} />);

		await userEvent.type(screen.getByTestId('quick-add-input'), '  带空格的标题  {Enter}');

		await waitFor(() => expect(mock.bodies).toHaveLength(1));
		expect(mock.bodies[0]).toMatchObject({ title: '带空格的标题' });
	});

	it('空标题（或全是空白）不发请求', async () => {
		const mock = mockCreate();
		renderWithProviders(<QuickAddTask projectId={PROJECT_ID} />);

		// 全空白也应被 trim 后拦下
		await userEvent.type(screen.getByTestId('quick-add-input'), '   ');
		await userEvent.click(screen.getByTestId('quick-add-submit'));

		expect(mock.bodies).toHaveLength(0);
		expect(screen.queryByTestId('quick-add-error')).not.toBeInTheDocument();
	});

	it('后端报错时内联展示消息，输入不清空', async () => {
		server.use(
			http.put(`${API}/projects/${PROJECT_ID}/tasks`, () =>
				HttpResponse.json({ code: 4001, message: '标题不合法' }, { status: 400 }),
			),
		);
		renderWithProviders(<QuickAddTask projectId={PROJECT_ID} />);

		const input = screen.getByTestId('quick-add-input');
		await userEvent.type(input, 'x');
		await userEvent.click(screen.getByTestId('quick-add-submit'));

		expect(await screen.findByTestId('quick-add-error')).toHaveTextContent('标题不合法');
		expect(screen.getByTestId('quick-add-error')).toHaveAttribute('role', 'alert');
		// 失败时不清空，用户能改了重试
		expect(input).toHaveValue('x');
	});
});
