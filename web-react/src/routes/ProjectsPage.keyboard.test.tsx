import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { Project } from '@/api/projects';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';

/**
 * B1/B2：焦点管理与键盘可达性。
 *
 * ## 为什么这组是单测而不是 E2E
 *
 * 判据与 A3 那组相反：A3 里"有没有溢出""列头粘没粘住"在 jsdom 里**没有可读的量**
 * （几何量恒 0），所以只能进 E2E。而焦点与键盘**在 jsdom 里是完备的**——
 * `document.activeElement` 是真的，`userEvent.tab()` 走真实的可聚焦元素序列，
 * `{Escape}` / `{Enter}` 走真实的事件派发。**这一块是"还没做"，不是"做不到"。**
 * 能用单测覆盖的就不要占 E2E 的成本（E2E 每条都要重新 build + 起浏览器）。
 *
 * ## 这里**不**测什么
 *
 * ⛔ 不测 Radix 自己的实现（焦点陷阱怎么实现的、Portal 挂在哪）——那是库的测试。
 * 测的是**我们的接线**：
 *   - 打开弹窗后焦点落在**第一个该打字的字段**上（注意：守的不是 `autoFocus`，见那条的注释）
 *   - `<form onSubmit>` + `type="submit"` 这对有没有断（断了回车就不提交）
 *   - Esc 关闭之后**有没有顺手把数据写出去**
 *   - 破坏性按钮有没有抢到初始焦点
 *   - Tab 能不能走到每一个字段（含那个容易被 `tabIndex={-1}` 摘掉的下拉）
 *
 * 判据：**这条断言如果红了，要改的是我们的代码，还是去提 Radix 的 issue？**
 * 答案是后者的，不写。
 *
 * 全部 6 条都做过变异验证（K1/K2/K3/K5/K6），逐条红且红的是目标用例。
 * ⚠️ 其中 **K1 需要两处同时改**才红 —— 单摘 `autoFocus` 或单插一个可聚焦元素都不够，
 * 两个单看"不承重"的改动合起来才构成那个真实失败模式。
 */

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

/** 记下所有写请求。Esc 那几条要断言的是"**什么都没发出去**"。 */
function recordWrites() {
	const writes: string[] = [];
	server.use(
		http.put(`${API}/projects`, () => {
			writes.push('PUT /projects');
			return HttpResponse.json(project(99));
		}),
		http.post(`${API}/projects/:id`, ({ params }) => {
			writes.push(`POST /projects/${params.id}`);
			return HttpResponse.json(project(Number(params.id)));
		}),
		http.delete(`${API}/projects/:id`, ({ params }) => {
			writes.push(`DELETE /projects/${params.id}`);
			return HttpResponse.json({ message: 'ok' });
		}),
	);
	return writes;
}

async function openCreateDialog() {
	await userEvent.click(await screen.findByTestId('new-project'));
	return screen.findByTestId('project-form');
}

describe('B1 焦点管理：弹窗打开时焦点去哪', () => {
	/**
	 * ★ 打开后能直接打字。
	 *
	 * ⚠️ **这条守的不是 `autoFocus`。** 变异实测（K1：把 `autoFocus` 整个摘掉）——**照样绿**：
	 * Radix 打开弹窗时本来就会聚焦**第一个可聚焦元素**，而那正好是标题输入框。
	 * 也就是说 `autoFocus` 在这里是**冗余**的，删了行为不变（第 20 条：终态有第二条路径）。
	 *
	 * 它真正守得住的是**另一个**失败模式，也是实际会发生的那个：
	 * **有人在标题字段之前插了一个可聚焦元素**（一个按钮、一个链接、一个折叠开关）。
	 * 那时 Radix 的"第一个可聚焦"就不再是标题了，用户打开弹窗直接打字会打进别处。
	 * 已实测：在 `<form>` 开头插一个 `<button>` 并摘掉 `autoFocus` → **本条红**。
	 */
	it('★ 新建弹窗打开后，焦点落在标题输入框上', async () => {
		mockProjects([]);
		renderApp('/projects');

		const dialog = await openCreateDialog();

		await waitFor(() => expect(within(dialog).getByTestId('project-title-input')).toHaveFocus());
	});

	/**
	 * ★★★ 破坏性弹窗**不许**把初始焦点放在"删除"上。
	 *
	 * 删除项目是**递归硬删、跨所有权、且不可恢复**（见 DeleteProjectDialog 的文件头）。
	 * 焦点一旦默认落在确认按钮上，"打开弹窗顺手敲回车"就等于删掉整棵子树 ——
	 * 而习惯了回车确认的用户几乎一定会这么做。
	 */
	it('★★★ 删除弹窗的初始焦点不在"删除"按钮上', async () => {
		mockProjects([project(3)]);
		renderApp('/projects');

		await userEvent.click(await screen.findByTestId('project-delete-3'));
		const dialog = await screen.findByTestId('project-delete-dialog');
		const confirm = within(dialog).getByTestId('project-delete-confirm');

		// 等焦点稳定下来再断言：Radix 打开弹窗后会异步移动焦点，
		// 立刻断言的话量到的是移动**之前**的状态 —— 那样这条用例对任何实现都绿。
		await waitFor(() =>
			expect(dialog).toContainElement(document.activeElement as HTMLElement | null),
		);
		expect(confirm).not.toHaveFocus();
	});
});

describe('B2 键盘可达性', () => {
	/**
	 * ★★ 回车提交。
	 *
	 * 这条守的是 `<form onSubmit>` 与 `<button type="submit">` **这一对**。
	 * 把提交按钮改成 `type="button"`（一个很容易在重构里发生的改动），
	 * 鼠标点击照常工作、**只有键盘用户的回车会静默失效** —— 没有任何报错。
	 */
	it('★★ 在标题输入框里按回车 = 提交表单', async () => {
		mockProjects([]);
		const writes = recordWrites();
		renderApp('/projects');

		const dialog = await openCreateDialog();
		const title = within(dialog).getByTestId('project-title-input');

		await userEvent.type(title, '键盘建的项目{Enter}');

		await waitFor(() => expect(writes).toEqual(['PUT /projects']));
	});

	/**
	 * ★★ Esc 关弹窗，**而且什么都不写**。
	 *
	 * "关掉了"只是一半。另一半是取消要真的取消 —— 如果哪天有人给弹窗加了
	 * "关闭时自动保存草稿"，这条会红，而它应该红。
	 */
	it('★★ Esc 关掉新建弹窗，且不产生任何写请求', async () => {
		mockProjects([]);
		const writes = recordWrites();
		renderApp('/projects');

		const dialog = await openCreateDialog();
		// 先真的输入点东西：空表单被丢弃是"没数据可写"，验不出"取消了写"（第 45 条：
		// 判别值不能让两种实现同解）
		await userEvent.type(within(dialog).getByTestId('project-title-input'), '不该被保存');

		await userEvent.keyboard('{Escape}');

		await waitFor(() => expect(screen.queryByTestId('project-form')).not.toBeInTheDocument());
		expect(writes).toEqual([]);
	});

	it('★★★ Esc 关掉删除确认，不发 DELETE', async () => {
		mockProjects([project(3)]);
		const writes = recordWrites();
		renderApp('/projects');

		await userEvent.click(await screen.findByTestId('project-delete-3'));
		await screen.findByTestId('project-delete-dialog');

		await userEvent.keyboard('{Escape}');

		await waitFor(() =>
			expect(screen.queryByTestId('project-delete-dialog')).not.toBeInTheDocument(),
		);
		expect(writes).toEqual([]);
	});

	/**
	 * ★★ Tab 顺序沿 DOM 顺序走完整张表单。
	 *
	 * ⚠️ 断言的是**相对顺序**，不是"第 N 次 Tab 落在谁身上"：
	 * 中间插一个新字段会让后者整体错位、红一片，而顺序本身并没有坏。
	 */
	it('★★ Tab 依次经过 标题 → 描述 → 上级 → 取消 → 保存', async () => {
		mockProjects([]);
		renderApp('/projects');

		const dialog = await openCreateDialog();
		const expected = [
			within(dialog).getByTestId('project-title-input'),
			within(dialog).getByTestId('project-description-input'),
			within(dialog).getByTestId('project-parent-select'),
			within(dialog).getByTestId('project-form-cancel'),
			within(dialog).getByTestId('project-form-submit'),
		];

		await waitFor(() => expect(expected[0]).toHaveFocus());

		// 从标题出发，一路 Tab，记下**这五个目标各自在第几步被聚焦**
		const seenAt = new Map<Element, number>([[expected[0]!, 0]]);
		for (let step = 1; step <= 12; step += 1) {
			await userEvent.tab();
			const active = document.activeElement;
			if (active && expected.includes(active as HTMLElement) && !seenAt.has(active)) {
				seenAt.set(active, step);
			}
		}

		const missing = expected.filter((el) => !seenAt.has(el));
		expect(missing).toHaveLength(0);

		const order = expected.map((el) => seenAt.get(el)!);
		expect(order).toEqual([...order].sort((a, b) => a - b));
	});
});

/**
 * ## ⚠️ 已登记的覆盖缺口：**焦点陷阱在 jsdom 里验不了**
 *
 * 本来写了一条「Tab 一圈之后焦点仍在弹窗内，不会漏到背后的页面」。**已删掉**，
 * 因为**找不到任何变异能让它红** —— 它是一条恒绿断言，而恒绿断言比没有断言更糟：
 * 它占着"这块已经有人管了"的位置（第 3 条）。
 *
 * 实测过程：
 *  - 变异 `modal={false}`（Radix 据此把 `trapFocus` 关掉，见其 `DialogContentNonModal`）
 *    → 断言**照样绿**。
 *  - 打了 tab 轨迹出来看：12 次 Tab 依次落在
 *    描述 → 上级 → 取消 → 保存 → 关闭 → 标题 → …**原地循环，一次都没有出去过**。
 *
 * 也就是说在 jsdom 里，焦点**无论如何**都出不了那个 Portal —— 与我们有没有开启陷阱无关。
 * 于是"陷阱有效"与"陷阱被关掉"**同解**，这条用例什么也分辨不出来。
 *
 * **要真正守住它得进 E2E**（真浏览器里 Tab 会真的走到 Portal 之外）。
 * 但焦点陷阱是 Radix 的职责、不是我们的接线，按本文件开头那条判据
 * （"红了要改的是我们的代码还是去提 Radix 的 issue"）——**暂不补**。
 * 登记在此，不假装它被覆盖了。
 */
