import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { Label } from '@/api/labels';
import { currentUserFixture } from '@/test/handlers';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';
const ME = currentUserFixture.id;
const OTHER = 999;

/** 默认造自己建的标签；`created_by` 显式给全，因为改/删权限完全靠它判。 */
function label(id: number, extra: Partial<Label> = {}): Label {
	return {
		id,
		title: `L${id}`,
		description: '',
		hex_color: '',
		created_by: { id: ME, username: 'tester' },
		...extra,
	};
}

function mockLabels(items: Label[]) {
	let calls = 0;
	server.use(
		http.get(`${API}/labels`, () => {
			calls += 1;
			return HttpResponse.json(items, {
				headers: {
					'x-pagination-result-count': String(items.length),
					'x-pagination-total-pages': items.length ? '1' : '0',
				},
			});
		}),
	);
	return () => calls;
}

/**
 * ⚠️ **本文件不按可见文案定位元素**（F13 规矩，模板见
 * `components/layout/AppShell.test.tsx` 的头注）。
 * 标签标题（`L1`、`我的标签`）**是 mock 里的数据不是文案**，按它定位是可以的；
 * 按钮、输入框、校验错误一律走 testid。
 */
async function openCreateDialog() {
	await userEvent.click(await screen.findByTestId('new-label'));
	return screen.findByTestId('label-form');
}

/**
 * 按标签标题找到那一行，再点行内的编辑/删除。
 *
 * ⚠️ 这里的行内前缀正则是安全的，因为**标签列表是平铺的**（每个 `<li>` 一行、
 * 互不嵌套）。项目树那边不能这么写 —— treeitem 是嵌套的，父行的 DOM 里
 * 包含所有子行，前缀正则会匹配到多个（见 `ProjectsPage.crud.test.tsx`）。
 */
async function openRowDialog(title: string, action: 'edit' | 'delete') {
	const row = (await screen.findByText(title)).closest('li')!;
	await userEvent.click(within(row).getByTestId(new RegExp(`^label-${action}-`)));
	return screen.findByTestId(action === 'edit' ? 'label-form' : 'label-delete-dialog');
}

describe('标签列表与三分权限', () => {
	it('★ 别人建的标签**照样列出来**（可见即可用，过滤掉会让共享标签凭空消失）', async () => {
		mockLabels([
			label(950, { title: '我的标签' }),
			label(954, { title: '别人的标签', created_by: { id: OTHER, username: 'bob' } }),
		]);

		renderApp('/labels');

		expect(await screen.findByText('我的标签')).toBeInTheDocument();
		// 若实现把列表按 created_by 过滤（"只显示我能改的"），这条会红
		expect(screen.getByText('别人的标签')).toBeInTheDocument();
	});

	it('★ 改/删入口只给创建者 —— 自己的有、别人的没有', async () => {
		// ☠ 两条断言必须成对：
		//   只断言"别人的没按钮" → "谁的都不给按钮"的实现能蒙混过去；
		//   只断言"自己的有按钮" → "谁的都给按钮"的实现能蒙混过去。
		// 且 fixture 必须同时含两类标签，否则其中一条是空断言。
		mockLabels([
			label(950, { title: '我的标签' }),
			label(954, { title: '别人的标签', created_by: { id: OTHER, username: 'bob' } }),
		]);

		renderApp('/labels');
		await screen.findByText('别人的标签');

		const mine = screen.getByText('我的标签').closest('li')!;
		expect(within(mine).getByTestId(/^label-edit-/)).toBeInTheDocument();
		expect(within(mine).getByTestId(/^label-delete-/)).toBeInTheDocument();
		expect(screen.queryByLabelText('编辑 别人的标签')).not.toBeInTheDocument();
		expect(screen.queryByLabelText('删除 别人的标签')).not.toBeInTheDocument();
	});

	it('别人建的标签注明创建者，免得用户以为是自己权限没配好', async () => {
		mockLabels([label(954, { created_by: { id: OTHER, username: 'bob' } })]);

		renderApp('/labels');

		expect(await screen.findByTestId('label-foreign-954')).toHaveTextContent('bob');
	});

	it('★ 没设颜色的标签不渲染色块（而不是渲染一个黑点冒充）', async () => {
		mockLabels([label(950, { hex_color: '' }), label(951, { hex_color: 'ff0000' })]);

		renderApp('/labels');
		await screen.findByTestId('label-swatch-951');

		expect(screen.queryByTestId('label-swatch-950')).not.toBeInTheDocument();
	});
});

describe('新建标签', () => {
	it('★ 空标题照发 PUT —— 后端不校验，前端不许替它补', async () => {
		// 语料 label.create.empty_title_is_accepted：空标题实测 201。
		// ☠ 这条断言防的是"顺手加个必填校验"——那个改动看起来像在修 bug，
		//   review 很难拦下，只有这条会红。
		let body: unknown = null;
		mockLabels([]);
		server.use(
			http.put(`${API}/labels`, async ({ request }) => {
				body = await request.json();
				return HttpResponse.json({ id: 9, title: '' }, { status: 201 });
			}),
		);

		renderApp('/labels');
		const dialog = await openCreateDialog();
		await userEvent.click(within(dialog).getByTestId('label-form-submit'));

		await waitFor(() => expect(body).not.toBeNull());
		expect(body).toEqual({ title: '', description: '', hex_color: '' });
	});

	it('PUT 才是新建；成功后关弹窗并重新拉列表', async () => {
		const listCalls = mockLabels([]);
		server.use(http.put(`${API}/labels`, () => HttpResponse.json({ id: 9 }, { status: 201 })));

		renderApp('/labels');
		const dialog = await openCreateDialog();
		await userEvent.type(within(dialog).getByTestId('label-title-input'), '新标签');
		await userEvent.click(within(dialog).getByTestId('label-form-submit'));

		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
		// 不重取的话新标签不会出现在列表里
		await waitFor(() => expect(listCalls()).toBeGreaterThan(1));
	});
});

describe('编辑标签（POST 是全量替换）', () => {
	it('★ 只改标题时，description 与 hex_color 必须原样回传', async () => {
		// ☠ fixture 的两个可选字段**必须非空**：都为空串的话，
		//   "只发 title"与"发全量"产生的请求体完全相同（空串是该变换下的不动点），
		//   这条用例就退化成永远绿。语料选 951 而不是 950 也是这个原因。
		let body: Record<string, unknown> | null = null;
		mockLabels([label(951, { title: 'X-beta', description: '原描述', hex_color: 'ff0000' })]);
		server.use(
			http.post(`${API}/labels/951`, async ({ request }) => {
				body = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json({ id: 951 });
			}),
		);

		renderApp('/labels');
		const dialog = await openRowDialog('X-beta', 'edit');

		const titleInput = within(dialog).getByTestId('label-title-input');
		await userEvent.clear(titleInput);
		await userEvent.type(titleInput, '改名后');
		await userEvent.click(within(dialog).getByTestId('label-form-submit'));

		await waitFor(() => expect(body).not.toBeNull());
		// 漏任何一列 = 那列被静默清空，接口照样 200
		expect(body).toEqual({ title: '改名后', description: '原描述', hex_color: 'ff0000' });
	});

	it('★ 只改标题不会给原本没有颜色的标签染上颜色', async () => {
		// ☠ <input type="color"> 没有空值这一档，空串会被浏览器回落成 #000000。
		//   若把输入框的当前值当作用户的选择，这里会发出 "000000"（或回落色 "cccccc"），
		//   而 POST 是全量替换 —— 于是"改个名字"真的把标签染黑了，且无任何报错。
		let body: Record<string, unknown> | null = null;
		mockLabels([label(950, { title: '无色标签', hex_color: '' })]);
		server.use(
			http.post(`${API}/labels/950`, async ({ request }) => {
				body = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json({ id: 950 });
			}),
		);

		renderApp('/labels');
		const dialog = await openRowDialog('无色标签', 'edit');

		const titleInput = within(dialog).getByTestId('label-title-input');
		await userEvent.clear(titleInput);
		await userEvent.type(titleInput, '改了名');
		await userEvent.click(within(dialog).getByTestId('label-form-submit'));

		await waitFor(() => expect(body).not.toBeNull());
		expect(body!.hex_color).toBe('');
	});

	it('★ 选了颜色后，发出去的 hex_color 不带前导 #', async () => {
		let body: Record<string, unknown> | null = null;
		mockLabels([label(950, { title: '要上色' })]);
		server.use(
			http.post(`${API}/labels/950`, async ({ request }) => {
				body = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json({ id: 950 });
			}),
		);

		renderApp('/labels');
		const dialog = await openRowDialog('要上色', 'edit');

		// userEvent 不支持 type=color，直接派发 change
		fireEvent.change(within(dialog).getByTestId('label-color-input'), {
			target: { value: '#ff8400' },
		});
		await userEvent.click(within(dialog).getByTestId('label-form-submit'));

		await waitFor(() => expect(body).not.toBeNull());
		// 带 # 存进去的话，之后所有按 `#${hex_color}` 拼色的地方都会得到 "##ff8400"
		expect(body!.hex_color).toBe('ff8400');
	});
});

describe('删除标签', () => {
	it('二次确认后才发 DELETE，且说明任务不会被删', async () => {
		let deleted = false;
		mockLabels([label(950, { title: '待删' })]);
		server.use(
			http.delete(`${API}/labels/950`, () => {
				deleted = true;
				return HttpResponse.json({ message: 'Successfully deleted.' });
			}),
		);

		renderApp('/labels');
		const dialog = await openRowDialog('待删', 'delete');

		expect(dialog).toHaveTextContent('任务本身不会被删除');
		// 打开弹窗本身不许发请求
		expect(deleted).toBe(false);

		await userEvent.click(within(dialog).getByTestId('label-delete-confirm'));
		await waitFor(() => expect(deleted).toBe(true));
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
	});

	it('取消不发请求', async () => {
		let deleted = false;
		mockLabels([label(950, { title: '待删' })]);
		server.use(
			http.delete(`${API}/labels/950`, () => {
				deleted = true;
				return HttpResponse.json({ message: 'ok' });
			}),
		);

		renderApp('/labels');
		const dialog = await openRowDialog('待删', 'delete');
		await userEvent.click(within(dialog).getByTestId('label-delete-cancel'));

		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
		expect(deleted).toBe(false);
	});
});

describe('写路径的错误文案（读写对"不存在"的口径相反）', () => {
	it('★ 404/8002 → 说"已不存在"，并提示刷新', async () => {
		mockLabels([label(950, { title: '幽灵' })]);
		server.use(
			http.post(`${API}/labels/950`, () =>
				HttpResponse.json({ code: 8002, message: 'This label does not exist.' }, { status: 404 }),
			),
		);

		renderApp('/labels');
		const dialog = await openRowDialog('幽灵', 'edit');
		await userEvent.click(within(dialog).getByTestId('label-form-submit'));

		const alert = await within(dialog).findByRole('alert');
		expect(alert).toHaveTextContent('不存在');
		// 弹窗不关，用户的输入不能凭空丢
		expect(screen.getByRole('dialog')).toBeInTheDocument();
	});

	it('★ 403 → 说"仅创建者可改"，文案与 404 那支不同', async () => {
		// 列表是自己的标签（所以有编辑按钮），但服务端说 403 —— 典型的本地列表过期。
		mockLabels([label(950, { title: '抢先改了' })]);
		server.use(
			http.post(`${API}/labels/950`, () =>
				HttpResponse.json({ code: 0, message: 'Forbidden' }, { status: 403 }),
			),
		);

		renderApp('/labels');
		const dialog = await openRowDialog('抢先改了', 'edit');
		await userEvent.click(within(dialog).getByTestId('label-form-submit'));

		const alert = await within(dialog).findByRole('alert');
		expect(alert).toHaveTextContent('创建者');
		// 后端英文原文不许直接甩给用户
		expect(alert).not.toHaveTextContent('Forbidden');
	});
});
