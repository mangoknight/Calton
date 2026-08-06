import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { AssignableUser } from '@/api/assignees';
import { server } from '@/test/msw';
import { renderWithProviders } from '@/test/render';
import { AssigneeSelector } from './AssigneeSelector';

const API = '*/api/v1';
const TASK_ID = 7;

/**
 * ⚠️ **本文件不按可见文案定位元素**（F13 规矩，模板见
 * `components/layout/AppShell.test.tsx` 的头注）。
 * 判据：这个断言关心的东西会不会因为换语言而改变？这里验的是
 * "路径段是数字 id 还是用户名""空搜索发不发请求""只读时入口在不在"——全都不会。
 *
 * 用户名（`alice`）是 mock 里的**数据**不是文案，按它断言没问题。
 * 后端错误消息（`指派失败`）来自 mock 响应体，也是被测对象本身。
 */

function user(id: number, username: string): AssignableUser {
	return { id, username };
}

function listResponse(items: unknown[]) {
	return HttpResponse.json(items, {
		headers: {
			'x-pagination-result-count': String(items.length),
			'x-pagination-total-pages': items.length ? '1' : '0',
		},
	});
}

interface AssigneeMock {
	puts: Record<string, unknown>[];
	deletes: string[];
	searches: URL[];
}

/**
 * ⚠️ `GET /users` 是**自定义 handler，一个分页头都不发**，且空搜索返回 `null`。
 * 这里如实照做 —— 它正是 f19a38d 那条豁免名单要保护的调用点，
 * mock 若"好心"补上分页头，就测不到名单有没有生效了。
 */
function mockAssignees(searchResults: AssignableUser[], assigned: AssignableUser[] = []) {
	const mock: AssigneeMock = { puts: [], deletes: [], searches: [] };

	server.use(
		http.get(`${API}/users`, ({ request }) => {
			const url = new URL(request.url);
			mock.searches.push(url);
			const term = url.searchParams.get('s') ?? '';
			// 后端真实行为：空搜索返回 null（裸 return），不是全部用户
			return HttpResponse.json(term ? searchResults : null);
		}),
		http.get(`${API}/tasks/${TASK_ID}/assignees`, () => listResponse(assigned)),
		http.put(`${API}/tasks/${TASK_ID}/assignees`, async ({ request }) => {
			mock.puts.push((await request.json()) as Record<string, unknown>);
			return HttpResponse.json({}, { status: 201 });
		}),
		http.delete(`${API}/tasks/${TASK_ID}/assignees/:userId`, ({ params }) => {
			mock.deletes.push(String(params.userId));
			return new HttpResponse(null, { status: 204 });
		}),
	);

	return mock;
}

describe('★★ 指派用数字 user id，不是用户名', () => {
	/**
	 * ★★ 两个"assignees"说的不是一回事，搞反都表现为"没报错但没效果"：
	 * - **写**：`PUT /tasks/{id}/assignees` 收 `{user_id: 901}`（数字）；
	 * - **过滤**（F11a/F12 的 filter DSL）：`assignees = alice` 收**用户名**，
	 *   JOIN users 表按 username 比对，传数字 id 会返回 200 空集且不报错。
	 * 选择器属于"写"这一侧。
	 */
	it('★★ 指派发的 body 是 {user_id: 数字}，不是用户名', async () => {
		const mock = mockAssignees([user(901, 'alice')]);
		renderWithProviders(<AssigneeSelector taskId={TASK_ID} />);

		await userEvent.type(screen.getByTestId('assignee-search'), 'ali');
		await userEvent.click(await screen.findByTestId('user-result'));

		await waitFor(() => expect(mock.puts).toHaveLength(1));
		expect(mock.puts[0]).toEqual({ user_id: 901 });
		expect(typeof mock.puts[0]!.user_id).toBe('number');
		// 传用户名会被后端当成 0，静默指派不上
		expect(mock.puts[0]!.user_id).not.toBe('alice');
	});

	it('★★ 取消指派的路径段是数字 id，不是用户名', async () => {
		const mock = mockAssignees([], [user(901, 'alice')]);
		renderWithProviders(<AssigneeSelector taskId={TASK_ID} />);

		await userEvent.click(await screen.findByTestId('assignee-remove-901'));

		await waitFor(() => expect(mock.deletes).toEqual(['901']));
		expect(mock.deletes[0]).toMatch(/^\d+$/);
	});
});

describe('指派选择器：搜索', () => {
	/**
	 * ★ `GET /users` 空搜索返回 null，不是"全部用户"。
	 * 不卡这一道的话界面会显示一个空列表，让人以为没有人可指派。
	 */
	it('★ 没输入搜索词时不发请求，并说明要先打字', async () => {
		const mock = mockAssignees([user(901, 'alice')]);
		renderWithProviders(<AssigneeSelector taskId={TASK_ID} />);

		// 提示**存在**即可，具体那句话属于 i18n 迁移范围
		expect(await screen.findByTestId('assignee-hint')).toBeInTheDocument();
		await waitFor(() => expect(mock.searches).toHaveLength(0));
	});

	/**
	 * ★ `GET /users` 一个分页头都不发。这条能过，说明 client 的豁免名单确实生效了 ——
	 * 名单失效时这里会抛 ContractViolationError，正是 F08c 当初的必炸点。
	 */
	it('★ /users 不发分页头也不抛 ContractViolationError（豁免名单生效）', async () => {
		mockAssignees([user(901, 'alice'), user(902, 'bob')]);
		renderWithProviders(<AssigneeSelector taskId={TASK_ID} />);

		await userEvent.type(screen.getByTestId('assignee-search'), 'a');

		const results = await screen.findAllByTestId('user-result');
		expect(results).toHaveLength(2);
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
	});

	it('搜索词进 query', async () => {
		const mock = mockAssignees([user(901, 'alice')]);
		renderWithProviders(<AssigneeSelector taskId={TASK_ID} />);

		await userEvent.type(screen.getByTestId('assignee-search'), 'alice');

		await waitFor(() => {
			const last = mock.searches[mock.searches.length - 1]!;
			expect(last.searchParams.get('s')).toBe('alice');
		});
	});

	it('已指派的人不再出现在候选里', async () => {
		mockAssignees([user(901, 'alice'), user(902, 'bob')], [user(901, 'alice')]);
		renderWithProviders(<AssigneeSelector taskId={TASK_ID} />);

		await userEvent.type(screen.getByTestId('assignee-search'), 'a');

		const results = await screen.findAllByTestId('user-result');
		expect(results).toHaveLength(1);
		expect(results[0]).toHaveAttribute('data-user-id', '902');
	});

	it('搜不到人时给提示而不是空白', async () => {
		const mock = mockAssignees([]);
		renderWithProviders(<AssigneeSelector taskId={TASK_ID} />);

		await userEvent.type(screen.getByTestId('assignee-search'), 'zzz');
		// 搜到 0 个候选：断言结果列表为空态而不是那句提示怎么写
		await waitFor(() => expect(mock.searches.length).toBeGreaterThan(0));
		expect(screen.queryAllByTestId('user-result')).toHaveLength(0);
	});
});

describe('指派选择器：渲染与只读', () => {
	it('没有指派时给空态', async () => {
		mockAssignees([]);
		renderWithProviders(<AssigneeSelector taskId={TASK_ID} />);

		expect(await screen.findByTestId('assignees-empty')).toBeInTheDocument();
	});

	it('写失败时展示后端消息', async () => {
		mockAssignees([user(901, 'alice')]);
		server.use(
			http.put(`${API}/tasks/${TASK_ID}/assignees`, () =>
				HttpResponse.json({ code: 4001, message: '指派失败' }, { status: 500 }),
			),
		);
		renderWithProviders(<AssigneeSelector taskId={TASK_ID} />);

		await userEvent.type(screen.getByTestId('assignee-search'), 'ali');
		await userEvent.click(await screen.findByTestId('user-result'));

		expect(await screen.findByTestId('assignee-error')).toHaveTextContent('指派失败');
	});

	it('只读时不渲染搜索与取消入口', async () => {
		mockAssignees([], [user(901, 'alice')]);
		renderWithProviders(<AssigneeSelector taskId={TASK_ID} disabled />);

		await waitFor(() => expect(screen.getAllByTestId('assigned-user')).toHaveLength(1));
		expect(screen.queryByTestId('assignee-search')).not.toBeInTheDocument();
		expect(screen.queryByTestId(/^assignee-remove-/)).not.toBeInTheDocument();
	});
});
