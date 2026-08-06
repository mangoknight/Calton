import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { APIToken, CreatedToken } from '@/api/tokens';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';

/** 默认造一个不过期的 Token。 */
function token(id: number, extra: Partial<APIToken> = {}): APIToken {
	return {
		id,
		title: `Token-${id}`,
		permissions: { tasks: ['read_all'] },
		expires_at: '0001-01-01T00:00:00Z', // 永不过期（Go 零值）
		created: '2026-08-01T00:00:00Z',
		owner_id: 1,
		...extra,
	};
}

function mockTokens(items: APIToken[]) {
	server.use(
		http.get(`${API}/tokens`, () =>
			HttpResponse.json(items, {
				headers: {
					'x-pagination-result-count': String(items.length),
					'x-pagination-total-pages': items.length ? '1' : '0',
				},
			}),
		),
	);
}

describe('TokensPage', () => {
	describe('列表', () => {
		it('显示空态（没有 Token 时）', async () => {
			mockTokens([]);
			renderApp('/tokens');

			expect(await screen.findByTestId('tokens-page')).toBeInTheDocument();
			expect(screen.getByTestId('tokens-empty')).toBeInTheDocument();
			expect(screen.getByText('还没有 API Token')).toBeInTheDocument();
		});

		it('显示 Token 列表', async () => {
			mockTokens([token(1, { title: '我的第一个 Token' })]);
			renderApp('/tokens');

			expect(await screen.findByTestId('token-row-1')).toBeInTheDocument();
			expect(screen.getByTestId('token-title-1')).toHaveTextContent('我的第一个 Token');
		});

		it('显示多个 Token', async () => {
			mockTokens([token(1, { title: 'Token A' }), token(2, { title: 'Token B' })]);
			renderApp('/tokens');

			expect(await screen.findByTestId('token-row-1')).toBeInTheDocument();
			expect(screen.getByTestId('token-row-2')).toBeInTheDocument();
		});

		it('显示权限摘要', async () => {
			mockTokens([
				token(1, {
					title: '只读',
					permissions: { tasks: ['read_all'], projects: ['read_all'] },
				}),
			]);
			renderApp('/tokens');

			expect(await screen.findByTestId('token-permissions-1')).toHaveTextContent(/任务/);
			expect(screen.getByTestId('token-permissions-1')).toHaveTextContent(/项目/);
		});

		it('已过期的 Token 显示过期标签', async () => {
			// 30 天前过期
			const past = new Date(Date.now() - 30 * 86_400_000);
			mockTokens([token(1, { expires_at: past.toISOString() })]);
			renderApp('/tokens');

			expect(await screen.findByTestId('token-expiry-1')).toHaveTextContent('已过期');
		});

		it('加载中状态', async () => {
			// 不让请求回来
			server.use(
				http.get(`${API}/tokens`, () => new Promise(() => {})),
			);
			renderApp('/tokens');

			expect(screen.getByText('加载中…')).toBeInTheDocument();
		});

		it('加载失败显示错误', async () => {
			server.use(
				http.get(`${API}/tokens`, () => HttpResponse.json({ message: '令牌获取失败' }, { status: 500 })),
			);
			renderApp('/tokens');

			expect(await screen.findByText('令牌获取失败')).toBeInTheDocument();
		});
	});

	describe('创建', () => {
		it('点击"新建"打开创建弹窗', async () => {
			mockTokens([]);
			renderApp('/tokens');

			const newBtn = await screen.findByTestId('new-token');
			await userEvent.click(newBtn);

			expect(screen.getByTestId('create-token-dialog')).toBeInTheDocument();
		});

		it('空标题显示校验错误', async () => {
			mockTokens([]);
			renderApp('/tokens');

			await userEvent.click(await screen.findByTestId('new-token'));
			await userEvent.click(screen.getByTestId('token-create-submit'));

			expect(await screen.findByText('需要指定标题')).toBeInTheDocument();
		});

		it('未选权限显示校验错误', async () => {
			mockTokens([]);
			renderApp('/tokens');

			await userEvent.click(await screen.findByTestId('new-token'));
			await userEvent.type(screen.getByTestId('token-title-input'), '测试 Token');
			await userEvent.click(screen.getByTestId('token-create-submit'));

			expect(await screen.findByText('请从列表中选择至少一个权限。')).toBeInTheDocument();
		});

		it('成功创建后弹出 TokenCreatedDialog 展示明文', async () => {
			mockTokens([]);
			const tokenPlaintext = 'tk_abc123def456';
			server.use(
				http.put(`${API}/tokens`, () =>
					HttpResponse.json({
						id: 1,
						title: '测试 Token',
						token: tokenPlaintext,
						permissions: { tasks: ['read_all'] },
						expires_at: new Date(Date.now() + 30 * 86_400_000).toISOString(),
						created: new Date().toISOString(),
						owner_id: 1,
					} satisfies CreatedToken),
				),
			);
			renderApp('/tokens');

			await userEvent.click(await screen.findByTestId('new-token'));
			await userEvent.type(screen.getByTestId('token-title-input'), '测试 Token');

			// 选一个权限
			await userEvent.click(screen.getByTestId('perm-group-toggle-tasks'));
			await userEvent.click(screen.getByTestId('perm-action-tasks-read_all'));

			await userEvent.click(screen.getByTestId('token-create-submit'));

			// 等待创建成功弹窗
			const dialog = await screen.findByTestId('token-created-dialog');
			expect(dialog).toBeInTheDocument();
			expect(screen.getByTestId('token-created-value')).toHaveValue(tokenPlaintext);
		});

		it('创建成功后点"我已复制"关闭弹窗', async () => {
			mockTokens([token(1)]);
			server.use(
				http.put(`${API}/tokens`, () =>
					HttpResponse.json({
						id: 1,
						title: '新 Token',
						token: 'tk_newtoken',
						permissions: { tasks: ['read_all'] },
						expires_at: new Date(Date.now() + 30 * 86_400_000).toISOString(),
						created: new Date().toISOString(),
						owner_id: 1,
					} satisfies CreatedToken),
				),
			);
			renderApp('/tokens');

			await userEvent.click(await screen.findByTestId('new-token'));
			await userEvent.type(screen.getByTestId('token-title-input'), '新 Token');
			await userEvent.click(screen.getByTestId('perm-group-toggle-tasks'));
			await userEvent.click(screen.getByTestId('perm-action-tasks-read_all'));
			await userEvent.click(screen.getByTestId('token-create-submit'));

			expect(await screen.findByTestId('token-created-dialog')).toBeInTheDocument();

			await userEvent.click(screen.getByTestId('token-created-close'));

			// 关闭后 TokenCreatedDialog 消失
			expect(screen.queryByTestId('token-created-dialog')).not.toBeInTheDocument();
			// 创建弹窗也应关闭
			expect(screen.queryByTestId('create-token-dialog')).not.toBeInTheDocument();
		});
	});

	describe('删除', () => {
		it('点击删除按钮弹出确认弹窗', async () => {
			mockTokens([token(1, { title: '待删除' })]);
			renderApp('/tokens');

			expect(await screen.findByTestId('token-row-1')).toBeInTheDocument();

			await userEvent.click(screen.getByTestId('token-delete-1'));
			expect(screen.getByTestId('token-delete-dialog')).toBeInTheDocument();
		});

		it('确认删除后 Token 消失', async () => {
			mockTokens([token(1, { title: '待删除' })]);
			server.use(
				http.delete(`${API}/tokens/1`, () =>
					HttpResponse.json({ message: 'Successfully deleted.' }),
				),
			);
			renderApp('/tokens');

			expect(await screen.findByTestId('token-row-1')).toBeInTheDocument();

			await userEvent.click(screen.getByTestId('token-delete-1'));
			await userEvent.click(screen.getByTestId('token-delete-confirm'));

			// 删除后列表应刷新（mock 返回空列表）
			mockTokens([]);
			await waitFor(() => {
				expect(screen.queryByTestId('token-row-1')).not.toBeInTheDocument();
			});
		});

		it('取消删除不执行任何操作', async () => {
			mockTokens([token(1, { title: '保留' })]);
			renderApp('/tokens');

			expect(await screen.findByTestId('token-row-1')).toBeInTheDocument();

			await userEvent.click(screen.getByTestId('token-delete-1'));
			await userEvent.click(screen.getByTestId('token-delete-cancel'));

			expect(screen.queryByTestId('token-delete-dialog')).not.toBeInTheDocument();
			expect(screen.getByTestId('token-row-1')).toBeInTheDocument();
		});
	});
});
