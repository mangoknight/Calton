import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { Label } from '@/api/labels';
import { server } from '@/test/msw';
import { renderWithProviders } from '@/test/render';
import { LabelSelector } from './LabelSelector';

const API = '*/api/v1';

/**
 * ⚠️ **本文件不按可见文案定位元素**（F13 规矩，模板见
 * `components/layout/AppShell.test.tsx` 的头注）。
 * 标签标题（`标签 5`、`别人的`）是 mock 里的**数据**，按它断言可以；
 * 按钮/输入框/空态走 testid。
 */
const TASK_ID = 7;

function label(id: number, overrides: Partial<Label> = {}): Label {
	return { id, title: `标签 ${id}`, ...overrides };
}

function listResponse(items: unknown[]) {
	return HttpResponse.json(items, {
		headers: {
			'x-pagination-result-count': String(items.length),
			'x-pagination-total-pages': items.length ? '1' : '0',
		},
	});
}

interface LabelMock {
	puts: Record<string, unknown>[];
	deletes: string[];
	allLabelsQueries: URL[];
}

function mockLabels(all: Label[], attached: Label[] = []): LabelMock {
	const puts: Record<string, unknown>[] = [];
	const deletes: string[] = [];
	const allLabelsQueries: URL[] = [];

	server.use(
		http.get(`${API}/labels`, ({ request }) => {
			allLabelsQueries.push(new URL(request.url));
			return listResponse(all);
		}),
		http.get(`${API}/tasks/${TASK_ID}/labels`, () => listResponse(attached)),
		http.put(`${API}/tasks/${TASK_ID}/labels`, async ({ request }) => {
			puts.push((await request.json()) as Record<string, unknown>);
			return HttpResponse.json({}, { status: 201 });
		}),
		http.delete(`${API}/tasks/${TASK_ID}/labels/:labelId`, ({ params }) => {
			deletes.push(String(params.labelId));
			return new HttpResponse(null, { status: 204 });
		}),
	);

	return { puts, deletes, allLabelsQueries };
}

describe('标签选择器：打/摘', () => {
	it('渲染已挂标签与可添加标签', async () => {
		mockLabels([label(1), label(2)], [label(1)]);
		renderWithProviders(<LabelSelector taskId={TASK_ID} />);

		await waitFor(() => expect(screen.getAllByTestId('attached-label')).toHaveLength(1));
		// 已挂上的不再出现在候选里（这是去重，不是权限过滤）
		const available = await screen.findAllByTestId('available-label');
		expect(available).toHaveLength(1);
		expect(available[0]).toHaveAttribute('data-label-id', '2');
	});

	/** ⚠️ body 的键是 `label_id`（LabelTask.LabelID 的 json tag）。 */
	it('★ 打标签发 PUT，body 用 label_id', async () => {
		const mock = mockLabels([label(5)], []);
		renderWithProviders(<LabelSelector taskId={TASK_ID} />);

		await userEvent.click(await screen.findByTestId('available-label'));

		await waitFor(() => expect(mock.puts).toHaveLength(1));
		expect(mock.puts[0]).toEqual({ label_id: 5 });
	});

	it('★ 摘标签发 DELETE 到该标签 id', async () => {
		const mock = mockLabels([label(5)], [label(5)]);
		renderWithProviders(<LabelSelector taskId={TASK_ID} />);

		await userEvent.click(await screen.findByTestId('label-remove-5'));

		await waitFor(() => expect(mock.deletes).toEqual(['5']));
	});

	it('没有标签时给空态', async () => {
		mockLabels([], []);
		renderWithProviders(<LabelSelector taskId={TASK_ID} />);

		expect(await screen.findByTestId('labels-empty')).toBeInTheDocument();
	});

	it('写失败时展示后端消息', async () => {
		mockLabels([label(5)], []);
		server.use(
			http.put(`${API}/tasks/${TASK_ID}/labels`, () =>
				HttpResponse.json({ code: 4001, message: '打标签失败' }, { status: 500 }),
			),
		);
		renderWithProviders(<LabelSelector taskId={TASK_ID} />);

		await userEvent.click(await screen.findByTestId('available-label'));
		expect(await screen.findByTestId('label-error')).toHaveTextContent('打标签失败');
	});
});

describe('★★ 标签选择器不做权限过滤', () => {
	/**
	 * ★★ 本任务最容易犯的错：复用 F10 的 `canManageLabel` 来过滤选择器。
	 *
	 * 标签权限是三分的 ——「能看/能用」不需要前端判断（`GET /labels` 返回什么就是能用
	 * 什么，用别人建的标签挂到自己任务上实测 201），「能改/能删」才需要（F10 管理页）。
	 * 合并成一档的后果是共享标签从选择器里消失：用户明明能用却选不到，且无任何提示。
	 * 这是"能选、一点就 403"的**镜像错误**。
	 */
	it('★★ 别人建的标签照样出现在候选里（不按创建者过滤）', async () => {
		const mine = label(1, { title: '我的', created_by: { id: 1, username: 'me' } });
		const others = label(2, { title: '别人的', created_by: { id: 999, username: 'someone' } });
		mockLabels([mine, others], []);

		renderWithProviders(<LabelSelector taskId={TASK_ID} />);

		const available = await screen.findAllByTestId('available-label');
		expect(available).toHaveLength(2);
		expect(screen.getByText('别人的')).toBeInTheDocument();
	});

	it('★★ 别人建的标签能被真的打上去（选择器不该拦）', async () => {
		const others = label(2, { title: '别人的', created_by: { id: 999, username: 'someone' } });
		const mock = mockLabels([others], []);

		renderWithProviders(<LabelSelector taskId={TASK_ID} />);
		await userEvent.click(await screen.findByTestId('available-label'));

		await waitFor(() => expect(mock.puts).toEqual([{ label_id: 2 }]));
	});

	/**
	 * ★ 候选数必须等于 `GET /labels` 返回数减去已挂上的，一个不少。
	 * 任何"顺手加的"过滤都会让这条红。
	 */
	it('★ 候选集恰好是 GET /labels 的返回减去已挂上的，不多不少', async () => {
		const all = [label(1), label(2), label(3), label(4)];
		mockLabels(all, [label(2)]);

		renderWithProviders(<LabelSelector taskId={TASK_ID} />);

		const available = await screen.findAllByTestId('available-label');
		expect(available).toHaveLength(all.length - 1);
		expect(available.map((el) => el.getAttribute('data-label-id'))).toEqual(['1', '3', '4']);
	});
});

describe('标签选择器：搜索与只读', () => {
	it('搜索词进 query', async () => {
		const mock = mockLabels([label(1)], []);
		renderWithProviders(<LabelSelector taskId={TASK_ID} />);

		await screen.findAllByTestId('available-label');
		await userEvent.type(screen.getByTestId('label-search'), 'bug');

		await waitFor(() => {
			const last = mock.allLabelsQueries[mock.allLabelsQueries.length - 1]!;
			expect(last.searchParams.get('s')).toBe('bug');
		});
	});

	it('只读时不渲染添加/移除入口', async () => {
		mockLabels([label(1)], [label(2)]);
		renderWithProviders(<LabelSelector taskId={TASK_ID} disabled />);

		await waitFor(() => expect(screen.getAllByTestId('attached-label')).toHaveLength(1));
		expect(screen.queryByTestId('label-search')).not.toBeInTheDocument();
		expect(screen.queryByTestId(/^label-remove-/)).not.toBeInTheDocument();
	});

	/** hex_color 不带前导 #，渲染时才补；没颜色的标签不渲染色块。 */
	it('hex_color 渲染时补 #，空值不渲染色块', async () => {
		mockLabels([], [label(1, { hex_color: 'ff0000' }), label(2, { hex_color: '' })]);
		renderWithProviders(<LabelSelector taskId={TASK_ID} />);

		await waitFor(() => expect(screen.getAllByTestId('attached-label')).toHaveLength(2));
		const swatches = screen.getAllByTestId('label-swatch');
		expect(swatches).toHaveLength(1);
		expect(swatches[0]).toHaveStyle({ backgroundColor: '#ff0000' });
	});
});
