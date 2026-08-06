import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { Project } from '@/api/projects';
import { PARENT_KEEP, TOP_LEVEL_PARENT_ID } from '@/features/projects/parent-field';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';

// 默认给 Admin：大多数用例不关心权限，只有权限相关用例显式降权
function project(id: number, extra: Partial<Project> = {}): Project {
	return { id, title: `P${id}`, parent_project_id: 0, max_permission: 2, ...extra };
}

function mockProjects(items: Project[]) {
	server.use(
		http.get(`${API}/projects`, () =>
			HttpResponse.json(items, {
				headers: {
					'x-pagination-result-count': String(items.length),
					'x-pagination-total-pages': items.length ? '1' : '0',
				},
			}),
		),
	);
}

/**
 * ⚠️ **本文件不按可见文案定位元素、也不按文案断言**（F13 规矩，
 * 模板见 `components/layout/AppShell.test.tsx` 的头注）。
 *
 * 判据：**这个断言关心的东西，会不会因为换语言而改变？**
 * 这里验的是"打哪个方法、请求体什么形状、哪些选项可选、自己在不在候选里"——
 * 全都不会。所以按钮/输入框走 testid，下拉选项按 **value + disabled** 断言，
 * 不按选项文字（`'无（顶层项目）'`、`'P7（需要管理员权限）'` 都是文案）。
 *
 * 项目标题（`P3`、`P7`）**是 mock 里的数据不是文案**，按它定位是可以的。
 */
async function openCreateDialog() {
	await userEvent.click(await screen.findByTestId('new-project'));
	return screen.findByTestId('project-form');
}

/**
 * 点某个项目行内的编辑/删除。
 *
 * ⚠️ **按 id 精确取，不要在行内用前缀正则** —— treeitem 是嵌套的，
 * 父项目那一行的 DOM 里**包含它所有子项目的行**，前缀正则会匹配到多个
 * （第一版就是这么写的，四条用例报 "Found multiple elements"）。
 *
 * 本文件的 mock 约定是 `project(id)` 生成标题 `P{id}`，所以调用方直接传 id。
 */
async function openRowDialog(id: number, action: 'edit' | 'delete') {
	await userEvent.click(await screen.findByTestId(`project-${action}-${id}`));
	return screen.findByTestId(action === 'edit' ? 'project-form' : 'project-delete-dialog');
}

/** 下拉选项的结构：value + 是否禁用。文字是文案，不进断言。 */
function parentOptions(dialog: HTMLElement) {
	return within(within(dialog).getByTestId('project-parent-select'))
		.getAllByRole('option')
		.map((o) => ({
			value: (o as HTMLOptionElement).value,
			disabled: (o as HTMLOptionElement).disabled,
		}));
}

describe('新建项目', () => {
	it('PUT /projects 建项目（v1 里 PUT 才是新建），成功后关闭弹窗并刷新列表', async () => {
		let body: Record<string, unknown> | null = null;
		let listCalls = 0;

		server.use(
			http.get(`${API}/projects`, () => {
				listCalls += 1;
				return HttpResponse.json([], {
					headers: { 'x-pagination-result-count': '0', 'x-pagination-total-pages': '0' },
				});
			}),
			http.put(`${API}/projects`, async ({ request }) => {
				body = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json({ id: 9, title: '新项目' }, { status: 201 });
			}),
		);

		renderApp('/projects');
		const dialog = await openCreateDialog();

		await userEvent.type(within(dialog).getByTestId('project-title-input'), '新项目');
		await userEvent.click(within(dialog).getByTestId('project-form-submit'));

		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
		expect(body).toEqual({ title: '新项目', description: '' });
		// 建完要重新拉列表，否则新项目不出现
		await waitFor(() => expect(listCalls).toBeGreaterThan(1));
	});

	it('名称必填，空名称不发请求', async () => {
		let calls = 0;
		mockProjects([]);
		server.use(
			http.put(`${API}/projects`, () => {
				calls += 1;
				return HttpResponse.json({ id: 9 });
			}),
		);

		renderApp('/projects');
		const dialog = await openCreateDialog();
		await userEvent.click(within(dialog).getByTestId('project-form-submit'));

		// 按字段断言，不按校验文案
		expect(await within(dialog).findByTestId('project-title-error')).toBeInTheDocument();
		expect(calls).toBe(0);
	});

	it('后端报错时展示原文且不关弹窗', async () => {
		mockProjects([]);
		server.use(
			http.put(`${API}/projects`, () =>
				HttpResponse.json({ code: 3005, message: '项目名称已存在' }, { status: 400 }),
			),
		);

		renderApp('/projects');
		const dialog = await openCreateDialog();
		await userEvent.type(within(dialog).getByTestId('project-title-input'), '重名');
		await userEvent.click(within(dialog).getByTestId('project-form-submit'));

		expect(await within(dialog).findByRole('alert')).toHaveTextContent('项目名称已存在');
		expect(screen.getByRole('dialog')).toBeInTheDocument();
	});
});

describe('编辑项目', () => {
	it('POST /projects/{id} 更新（v1 里 POST 是全量替换），必须回传完整对象', async () => {
		let body: Record<string, unknown> | null = null;
		const existing = project(3, {
			title: 'P3',
			description: '原描述',
			hex_color: 'ff0000',
			parent_project_id: 0,
			identifier: 'P3ID',
		});
		mockProjects([existing]);
		server.use(
			http.post(`${API}/projects/3`, async ({ request }) => {
				body = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json({ ...existing, title: '改名后' });
			}),
		);

		renderApp('/projects');
		const dialog = await openRowDialog(3, 'edit');

		const titleInput = within(dialog).getByTestId('project-title-input');
		await userEvent.clear(titleInput);
		await userEvent.type(titleInput, '改名后');
		await userEvent.click(within(dialog).getByTestId('project-form-submit'));

		await waitFor(() => expect(body).not.toBeNull());
		// 全量替换：没在表单里出现的字段也要原样回传，否则会被清空
		expect(body).toMatchObject({
			id: 3,
			title: '改名后',
			hex_color: 'ff0000',
			identifier: 'P3ID',
		});
		// 唯一的例外：parent_project_id 省略即"不改"
		expect('parent_project_id' in body!).toBe(false);
	});

	it('★ 默认「不修改」时把 parent_project_id 从请求体里省略（不是回传旧值）', async () => {
		let body: Record<string, unknown> | null = null;
		const existing = project(3, { parent_project_id: 7 });
		mockProjects([existing, project(7)]);
		server.use(
			http.post(`${API}/projects/3`, async ({ request }) => {
				body = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json(existing);
			}),
		);

		renderApp('/projects');
		const dialog = await openRowDialog(3, 'edit');

		// 不碰上级项目下拉，直接保存
		await userEvent.click(within(dialog).getByTestId('project-form-submit'));

		await waitFor(() => expect(body).not.toBeNull());
		// 省略 = 不改（后端 *int64 得到 nil 指针）。回传旧值会在并发编辑时
		// 覆盖掉别人刚做的移动操作 —— 该字段是 AC-6 全量替换的显式例外。
		expect('parent_project_id' in body!).toBe(false);
		// 其余字段仍然全量回传
		expect(body).toMatchObject({ id: 3, title: 'P3' });
	});

	it('★ 选「移到顶层」与「不修改」在请求体上可区分', async () => {
		let body: Record<string, unknown> | null = null;
		const existing = project(3, { parent_project_id: 7 });
		mockProjects([existing, project(7)]);
		server.use(
			http.post(`${API}/projects/3`, async ({ request }) => {
				body = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json(existing);
			}),
		);

		renderApp('/projects');
		const dialog = await openRowDialog(3, 'edit');

		await userEvent.selectOptions(
			within(dialog).getByTestId('project-parent-select'),
			String(TOP_LEVEL_PARENT_ID),
		);
		await userEvent.click(within(dialog).getByTestId('project-form-submit'));

		await waitFor(() => expect(body).not.toBeNull());
		// 顶层 = 0，tester 实测定案
		expect(body!.parent_project_id).toBe(0);
	});

	it('★ 选具体项目时挂到该项目下', async () => {
		let body: Record<string, unknown> | null = null;
		const existing = project(3, { parent_project_id: 0 });
		mockProjects([existing, project(7)]);
		server.use(
			http.post(`${API}/projects/3`, async ({ request }) => {
				body = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json(existing);
			}),
		);

		renderApp('/projects');
		const dialog = await openRowDialog(3, 'edit');

		await userEvent.selectOptions(within(dialog).getByTestId('project-parent-select'), '7');
		await userEvent.click(within(dialog).getByTestId('project-form-submit'));

		await waitFor(() => expect(body).not.toBeNull());
		expect(body!.parent_project_id).toBe(7);
	});

	it('上级项目下拉里不能选自己（否则直接造出自环）', async () => {
		mockProjects([project(3), project(7)]);

		renderApp('/projects');
		const dialog = await openRowDialog(3, 'edit');

		const values = parentOptions(dialog).map((o) => o.value);

		// 判别式数据：候选里必须**同时**有别人（P7）和自己（P3），
		// 否则"排除了自己"与"候选本来就只有一个"同解，这条分辨不出来
		expect(values).toContain('7');
		expect(values).not.toContain('3');
	});

	it('★ 只有 write 权限时，上级项目选择器整个禁用并说明原因（第 2 道闸）', async () => {
		// 实测：write 非 owner 能改标题（200），但 detach 到顶层 403
		mockProjects([project(3, { max_permission: 1 }), project(7)]);

		renderApp('/projects');
		const dialog = await openRowDialog(3, 'edit');

		expect(within(dialog).getByTestId('project-parent-select')).toBeDisabled();
		expect(dialog).toHaveTextContent('没有管理员权限，无法调整它的上级项目');
		// 其余字段仍可改
		expect(within(dialog).getByTestId('project-title-input')).toBeEnabled();
	});

	it('★ 对目标父级只有 write 时，该选项被禁用并注明原因（第 3 道闸）', async () => {
		// 实测：owner 对新父级只有 write → 挂进去 403
		mockProjects([project(3), project(7, { max_permission: 1 }), project(8)]);

		renderApp('/projects');
		const dialog = await openRowDialog(3, 'edit');

		const options = parentOptions(dialog);

		// 关键是**禁用与否**，不是那句"（需要管理员权限）"怎么写
		expect(options).toContainEqual({ value: '7', disabled: true });
		expect(options).toContainEqual({ value: '8', disabled: false });
	});

	it('新建时没有「不修改」这个选项，只有「无（顶层项目）」', async () => {
		mockProjects([project(7)]);

		renderApp('/projects');
		const dialog = await openCreateDialog();

		// 新建时只有 PARENT_KEEP（显示为"无（顶层项目）"）+ 候选项，
		// **没有** TOP_LEVEL_PARENT_ID 那个"移到顶层" —— 按 value 断言，与文案无关
		expect(parentOptions(dialog).map((o) => o.value)).toEqual([PARENT_KEEP, '7']);
	});
});

describe('删除项目', () => {
	it('二次确认后才发 DELETE，并提示会连带删除任务', async () => {
		let deleted = false;
		mockProjects([project(3)]);
		server.use(
			http.delete(`${API}/projects/3`, () => {
				deleted = true;
				return new HttpResponse(null, { status: 204 });
			}),
		);

		renderApp('/projects');
		const dialog = await openRowDialog(3, 'delete');

		expect(dialog).toHaveTextContent('不可恢复');
		expect(deleted).toBe(false);

		await userEvent.click(within(dialog).getByTestId('project-delete-confirm'));
		await waitFor(() => expect(deleted).toBe(true));
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
	});

	it('取消不发请求', async () => {
		let deleted = false;
		mockProjects([project(3)]);
		server.use(
			http.delete(`${API}/projects/3`, () => {
				deleted = true;
				return new HttpResponse(null, { status: 204 });
			}),
		);

		renderApp('/projects');
		const dialog = await openRowDialog(3, 'delete');
		await userEvent.click(within(dialog).getByTestId('project-delete-cancel'));

		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
		expect(deleted).toBe(false);
	});

	it('★ 按整棵子树告知影响面，不只数直接子项目', async () => {
		// 实测：删除是完全递归硬删，P→C1→C2 三层会全部消失
		mockProjects([
			project(3),
			project(4, { parent_project_id: 3 }),
			project(5, { parent_project_id: 4 }),
			project(9),
		]);

		renderApp('/projects');
		const dialog = await openRowDialog(3, 'delete');

		expect(dialog).toHaveTextContent('2 个子项目');
		expect(dialog).toHaveTextContent('不可恢复');
	});

	it('★ 子树里有别人的项目时显著警示（API 不拦也不提示，只能靠 UI 说）', async () => {
		// 实测：alice 删自己的项目会把挂在下面、bob 拥有的项目和任务一起硬删，200 无提示
		mockProjects([
			{ ...project(3), owner: { id: 1, username: 'tester' } },
			{ ...project(4, { parent_project_id: 3 }), owner: { id: 99, username: 'bob' } },
		]);

		renderApp('/projects');
		const dialog = await openRowDialog(3, 'delete');

		const warning = await within(dialog).findByTestId('foreign-owner-warning');
		expect(warning).toHaveTextContent('1 个项目属于其他成员，将一并永久删除');
		expect(warning).toHaveTextContent('P4');
		expect(warning).toHaveTextContent('bob');
	});

	it('子树全是自己的项目时不出现跨所有者警示', async () => {
		mockProjects([
			{ ...project(3), owner: { id: 1, username: 'tester' } },
			{ ...project(4, { parent_project_id: 3 }), owner: { id: 1, username: 'tester' } },
		]);

		renderApp('/projects');
		const dialog = await openRowDialog(3, 'delete');

		expect(within(dialog).queryByTestId('foreign-owner-warning')).not.toBeInTheDocument();
	});
});
