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
const PROJECT_ID = 3;

/**
 * ⚠️ 本文件不按可见文案定位元素（F13 规矩）。验的是"路径段是数字 id 还是用户名"、
 * "候选来自项目成员"、"只读时入口在不在"——都与语言无关。
 * 用户名（alice）是 mock 里的**数据**不是文案，按它断言没问题。
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
	memberRequests: URL[];
}

/**
 * 候选来自**项目成员**（`GET /projects/{id}/projectusers`），不是全局用户搜索 ——
 * 后者受可发现性限制、要记全名，见 AssigneeSelector 文件头。
 */
function mockAssignees(members: AssignableUser[], assigned: AssignableUser[] = []) {
	const mock: AssigneeMock = { puts: [], deletes: [], memberRequests: [] };

	server.use(
		http.get(`${API}/projects/${PROJECT_ID}/projectusers`, ({ request }) => {
			mock.memberRequests.push(new URL(request.url));
			return listResponse(members);
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

function render(props: { disabled?: boolean; projectId?: number } = {}) {
	// ⚠️ 用 'projectId' in props 而不是 ?? —— 后者会把显式传的 undefined 兜底成 PROJECT_ID
	const projectId = 'projectId' in props ? props.projectId : PROJECT_ID;
	renderWithProviders(
		<AssigneeSelector taskId={TASK_ID} projectId={projectId} disabled={props.disabled} />,
	);
}

describe('★★ 指派用数字 user id，不是用户名', () => {
	it('★★ 指派发的 body 是 {user_id: 数字}，不是用户名', async () => {
		const mock = mockAssignees([user(901, 'alice')]);
		render();

		await userEvent.click(await screen.findByTestId('user-result'));

		await waitFor(() => expect(mock.puts).toHaveLength(1));
		expect(mock.puts[0]).toEqual({ user_id: 901 });
		expect(typeof mock.puts[0]!.user_id).toBe('number');
		expect(mock.puts[0]!.user_id).not.toBe('alice');
	});

	it('★★ 取消指派的路径段是数字 id，不是用户名', async () => {
		const mock = mockAssignees([], [user(901, 'alice')]);
		render();

		await userEvent.click(await screen.findByTestId('assignee-remove-901'));

		await waitFor(() => expect(mock.deletes).toEqual(['901']));
		expect(mock.deletes[0]).toMatch(/^\d+$/);
	});
});

describe('指派选择器：候选来自项目成员', () => {
	it('★ 成员直接列出，不需要先搜索', async () => {
		mockAssignees([user(901, 'alice'), user(902, 'bob')]);
		render();

		const results = await screen.findAllByTestId('user-result');
		expect(results).toHaveLength(2);
	});

	it('搜索框在本地过滤成员（不额外发请求）', async () => {
		const mock = mockAssignees([user(901, 'alice'), user(902, 'bob')]);
		render();

		await screen.findAllByTestId('user-result');
		const before = mock.memberRequests.length;
		await userEvent.type(screen.getByTestId('assignee-search'), 'ali');

		await waitFor(() => expect(screen.getAllByTestId('user-result')).toHaveLength(1));
		expect(screen.getByTestId('user-result')).toHaveAttribute('data-user-id', '901');
		expect(mock.memberRequests.length).toBe(before); // 本地过滤，没再请求
	});

	it('已指派的人不再出现在候选里', async () => {
		mockAssignees([user(901, 'alice'), user(902, 'bob')], [user(901, 'alice')]);
		render();

		const results = await screen.findAllByTestId('user-result');
		expect(results).toHaveLength(1);
		expect(results[0]).toHaveAttribute('data-user-id', '902');
	});

	it('过滤不到时给空态提示', async () => {
		mockAssignees([user(901, 'alice')]);
		render();

		await screen.findByTestId('user-result');
		await userEvent.type(screen.getByTestId('assignee-search'), 'zzz');

		expect(await screen.findByTestId('no-candidates')).toBeInTheDocument();
		expect(screen.queryAllByTestId('user-result')).toHaveLength(0);
	});

	it('没有真实项目（伪项目/缺失）时提示无法指派', async () => {
		mockAssignees([user(901, 'alice')]);
		render({ projectId: undefined });

		expect(await screen.findByTestId('assignee-hint')).toBeInTheDocument();
		expect(screen.queryByTestId('assignee-search')).not.toBeInTheDocument();
	});
});

describe('指派选择器：渲染与只读', () => {
	it('没有指派时给空态', async () => {
		mockAssignees([]);
		render();

		expect(await screen.findByTestId('assignees-empty')).toBeInTheDocument();
	});

	it('写失败时展示后端消息', async () => {
		mockAssignees([user(901, 'alice')]);
		server.use(
			http.put(`${API}/tasks/${TASK_ID}/assignees`, () =>
				HttpResponse.json({ code: 4001, message: '指派失败' }, { status: 500 }),
			),
		);
		render();

		await userEvent.click(await screen.findByTestId('user-result'));

		expect(await screen.findByTestId('assignee-error')).toHaveTextContent('指派失败');
	});

	it('只读时不渲染搜索与取消入口', async () => {
		mockAssignees([], [user(901, 'alice')]);
		render({ disabled: true });

		await waitFor(() => expect(screen.getAllByTestId('assigned-user')).toHaveLength(1));
		expect(screen.queryByTestId('assignee-search')).not.toBeInTheDocument();
		expect(screen.queryByTestId(/^assignee-remove-/)).not.toBeInTheDocument();
	});
});
