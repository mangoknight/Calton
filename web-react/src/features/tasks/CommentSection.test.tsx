import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { TaskComment } from '@/api/comments';
import { apiClient } from '@/api/client';
import { server } from '@/test/msw';
import { currentUserFixture } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { CommentSection } from './CommentSection';

const API = '*/api/v1';
const TASK_ID = 7;
/** 默认 mock 里的当前用户 id。 */
const ME = currentUserFixture.id;
const SOMEONE_ELSE = 999;

function comment(id: number, authorId: number | null, text = `评论 ${id}`): TaskComment {
	return {
		id,
		comment: text,
		author: authorId === null ? null : { id: authorId, username: `u${authorId}` },
		created: '2026-08-01T10:00:00Z',
	};
}

interface CommentMock {
	puts: Record<string, unknown>[];
	posts: { url: string; body: Record<string, unknown> }[];
	deletes: string[];
}

function mockComments(items: TaskComment[]): CommentMock {
	const mock: CommentMock = { puts: [], posts: [], deletes: [] };

	server.use(
		http.get(`${API}/tasks/${TASK_ID}/comments`, () =>
			HttpResponse.json(items, {
				headers: {
					'x-pagination-result-count': String(items.length),
					'x-pagination-total-pages': items.length ? '1' : '0',
				},
			}),
		),
		http.put(`${API}/tasks/${TASK_ID}/comments`, async ({ request }) => {
			const body = (await request.json()) as Record<string, unknown>;
			mock.puts.push(body);
			const created = comment(99, ME, String(body.comment));
			items.push(created);
			return HttpResponse.json(created, { status: 201 });
		}),
		http.post(`${API}/tasks/${TASK_ID}/comments/:commentId`, async ({ request, params }) => {
			const body = (await request.json()) as Record<string, unknown>;
			mock.posts.push({ url: request.url, body });
			const target = items.find((item) => item.id === Number(params.commentId));
			if (target) target.comment = String(body.comment);
			return HttpResponse.json(target ?? {});
		}),
		http.delete(`${API}/tasks/${TASK_ID}/comments/:commentId`, ({ params }) => {
			mock.deletes.push(String(params.commentId));
			const index = items.findIndex((item) => item.id === Number(params.commentId));
			if (index >= 0) items.splice(index, 1);
			return new HttpResponse(null, { status: 204 });
		}),
	);

	return mock;
}

/**
 * ⚠️ 必须先塞 token。
 *
 * `useCurrentUser` 是 `enabled: token !== null` —— 没登录就不发请求，
 * 于是 `canModifyComment` 判不出当前用户、**失败关闭**，改删按钮一个都不显示。
 * 那是正确行为，但测"作者能改"时必须还原登录态，
 * 否则整组用例会因为"根本没登录"而绿/红，测的不是权限逻辑。
 */
function render(items: TaskComment[], canWriteTask = true) {
	apiClient.tokens.set('test-jwt');
	const mock = mockComments(items);
	renderWithProviders(<CommentSection taskId={TASK_ID} canWriteTask={canWriteTask} />);
	return mock;
}

describe('评论区：发表', () => {
	it('★ 发评论后即时出现在列表里', async () => {
		const mock = render([]);

		await screen.findByTestId('comments-empty');
		await userEvent.type(screen.getByTestId('comment-draft'), '第一条评论');
		await userEvent.click(screen.getByTestId('comment-submit'));

		await waitFor(() => expect(mock.puts).toEqual([{ comment: '第一条评论' }]));
		expect(await screen.findByText('第一条评论')).toBeInTheDocument();
	});

	it('发表成功后清空输入框', async () => {
		render([]);

		await screen.findByTestId('comments-empty');
		await userEvent.type(screen.getByTestId('comment-draft'), 'abc');
		await userEvent.click(screen.getByTestId('comment-submit'));

		await waitFor(() => expect(screen.getByTestId('comment-draft')).toHaveValue(''));
	});

	/**
	 * ★ 验收要求"空评论提交被前端拦截"。后端也会拦（412 + code 2002 + invalid_fields），
	 * 前端拦一道是省一次来回，口径必须一致：**只有空白算空**。
	 */
	it.each(['', '   ', '\n\t'])('★ 空评论「%s」被前端拦下，不发请求', async (text) => {
		const mock = render([]);

		await screen.findByTestId('comments-empty');
		if (text) await userEvent.type(screen.getByTestId('comment-draft'), text);
		await userEvent.click(screen.getByTestId('comment-submit'));

		expect(await screen.findByTestId('comment-draft-error')).toHaveTextContent('不能为空');
		expect(mock.puts).toHaveLength(0);
	});

	it('发表失败时展示后端消息', async () => {
		render([]);
		server.use(
			http.put(`${API}/tasks/${TASK_ID}/comments`, () =>
				HttpResponse.json({ code: 2002, message: '评论内容不合法' }, { status: 412 }),
			),
		);

		await screen.findByTestId('comments-empty');
		await userEvent.type(screen.getByTestId('comment-draft'), 'x');
		await userEvent.click(screen.getByTestId('comment-submit'));

		expect(await screen.findByTestId('comment-create-error')).toHaveTextContent('评论内容不合法');
	});
});

describe('★★ 评论区：改/删的可见性', () => {
	/** ★★ 非作者看不到删除按钮（验收要求）。 */
	it('★★ 别人的评论没有编辑/删除入口', async () => {
		render([comment(1, SOMEONE_ELSE)]);

		const item = await screen.findByTestId('comment-item');
		expect(within(item).queryByTestId(/^comment-delete-/)).not.toBeInTheDocument();
		expect(within(item).queryByTestId(/^comment-edit-/)).not.toBeInTheDocument();
	});

	it('自己的评论有编辑/删除入口', async () => {
		render([comment(1, ME)]);

		const item = await screen.findByTestId('comment-item');
		expect(within(item).getByTestId('comment-delete-1')).toBeInTheDocument();
		expect(within(item).getByTestId('comment-edit-1')).toBeInTheDocument();
	});

	/**
	 * ★★ 只读时连自己的评论也不能改 —— 后端规则是"任务可写 **且** 是作者"的与。
	 * 只记住"仅作者"的实现会在这里显示按钮，点下去 403。
	 */
	it('★★ 只读权限下，连自己的评论也不显示改/删入口', async () => {
		render([comment(1, ME)], false);

		const item = await screen.findByTestId('comment-item');
		expect(within(item).queryByTestId(/^comment-delete-/)).not.toBeInTheDocument();
	});

	it('只读时不渲染发表表单', async () => {
		render([comment(1, ME)], false);

		await screen.findByTestId('comment-item');
		expect(screen.queryByTestId('comment-form')).not.toBeInTheDocument();
	});

	/** author 为 null 时失败关闭：不给改，也不崩。 */
	it('★ author 为 null 的评论不显示改/删入口，且能正常渲染', async () => {
		render([comment(1, null)]);

		const item = await screen.findByTestId('comment-item');
		expect(within(item).getByTestId('comment-author')).toHaveTextContent('未知用户');
		expect(within(item).queryByTestId(/^comment-delete-/)).not.toBeInTheDocument();
	});
});

describe('评论区：编辑与删除', () => {
	it('★ 编辑后即时反映，且 POST 打到带 taskID 的路径', async () => {
		const mock = render([comment(1, ME, '原文')]);

		await userEvent.click(await screen.findByTestId('comment-edit-1'));
		const box = screen.getByTestId('comment-edit-box-1');
		await userEvent.clear(box);
		await userEvent.type(box, '改过了');
		await userEvent.click(screen.getByTestId('comment-edit-save'));

		await waitFor(() => expect(mock.posts).toHaveLength(1));
		expect(mock.posts[0]!.body).toEqual({ comment: '改过了' });
		// ⚠️ 单条评论端点的 taskID 参与校验，不能只带 commentID
		expect(mock.posts[0]!.url).toContain(`/tasks/${TASK_ID}/comments/1`);
		expect(await screen.findByText('改过了')).toBeInTheDocument();
	});

	it('★ 编辑成空内容被前端拦下，不发请求', async () => {
		const mock = render([comment(1, ME, '原文')]);

		await userEvent.click(await screen.findByTestId('comment-edit-1'));
		await userEvent.clear(screen.getByTestId('comment-edit-box-1'));
		await userEvent.click(screen.getByTestId('comment-edit-save'));

		expect(await screen.findByTestId('comment-edit-error')).toHaveTextContent('不能为空');
		expect(mock.posts).toHaveLength(0);
	});

	it('取消编辑不发请求，且恢复展示', async () => {
		const mock = render([comment(1, ME, '原文')]);

		await userEvent.click(await screen.findByTestId('comment-edit-1'));
		await userEvent.click(screen.getByTestId('comment-edit-cancel'));

		expect(await screen.findByTestId('comment-body')).toHaveTextContent('原文');
		expect(mock.posts).toHaveLength(0);
	});

	it('★ 删除后即时从列表消失，DELETE 带 taskID', async () => {
		const mock = render([comment(1, ME), comment(2, ME)]);

		await waitFor(() => expect(screen.getAllByTestId('comment-item')).toHaveLength(2));
		await userEvent.click(screen.getByTestId('comment-delete-1'));

		await waitFor(() => expect(mock.deletes).toEqual(['1']));
		await waitFor(() => expect(screen.getAllByTestId('comment-item')).toHaveLength(1));
	});

	it('删除失败时展示后端消息', async () => {
		render([comment(1, ME)]);
		server.use(
			http.delete(`${API}/tasks/${TASK_ID}/comments/:commentId`, () =>
				HttpResponse.json({ code: 4001, message: '删不掉' }, { status: 500 }),
			),
		);

		await userEvent.click(await screen.findByTestId('comment-delete-1'));
		expect(await screen.findByText('删不掉')).toBeInTheDocument();
	});
});

describe('评论区：读取', () => {
	it('列表为空时给空态', async () => {
		render([]);
		expect(await screen.findByTestId('comments-empty')).toBeInTheDocument();
	});

	it('接口报错时展示后端消息', async () => {
		server.use(
			http.get(`${API}/tasks/${TASK_ID}/comments`, () =>
				HttpResponse.json({ code: 4001, message: '评论取不到' }, { status: 500 }),
			),
		);
		renderWithProviders(<CommentSection taskId={TASK_ID} canWriteTask />);

		expect(await screen.findByRole('alert')).toHaveTextContent('评论取不到');
	});
});
