import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { WRITABLE_TASK_COLUMNS, type Task } from '@/api/tasks';
import { ZERO_TIME } from '@/lib/datetime';
import { useUIStore } from '@/store/ui';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';
const TASK_ID = 7;

function task(overrides: Partial<Task> = {}): Task {
	return {
		id: TASK_ID,
		title: '写文档',
		description: '原描述',
		done: false,
		identifier: 'PRJ-7',
		due_date: '2026-08-20T00:00:00Z',
		start_date: ZERO_TIME,
		end_date: ZERO_TIME,
		priority: 3,
		percent_done: 0.5,
		hex_color: 'ff0000',
		project_id: 12,
		bucket_id: 4,
		repeat_after: 0,
		repeat_mode: 0,
		cover_image_attachment_id: 0,
		...overrides,
	};
}

interface DetailMock {
	posts: Record<string, unknown>[];
}

/**
 * ⚠️ mock 必须**保有服务端状态**：写进去的要能被随后的 GET 读回来。
 *
 * 不保有的话，`onSettled` 的重取会把界面拉回旧值，于是"乐观更新生效了吗"
 * 这类断言全部假红/假绿 —— 测的其实是 mock 的返回值常量，不是应用行为。
 */
function mockTask(initial: Task = task(), options: { maxPermission?: string } = {}): DetailMock {
	const posts: Record<string, unknown>[] = [];
	let current = initial;

	server.use(
		// 详情页现在还挂着评论区（F09）、标签与指派选择器（F08c）。
		// 不 mock 的话它们的查询会失败并渲染 role="alert"，
		// 于是"占位不是告警"那条会因为一个无关的错误而红。
		http.get(`${API}/tasks/${TASK_ID}/comments`, () =>
			HttpResponse.json([], {
				headers: { 'x-pagination-result-count': '0', 'x-pagination-total-pages': '0' },
			}),
		),
		http.get(`${API}/tasks/${TASK_ID}`, () =>
			HttpResponse.json(current, {
				headers: options.maxPermission ? { 'x-max-permission': options.maxPermission } : {},
			}),
		),
		http.post(`${API}/tasks/${TASK_ID}`, async ({ request }) => {
			const body = (await request.json()) as Record<string, unknown>;
			posts.push(body);
			// 全量替换：服务端此后返回的就是这个 body（叠在 id 等只读字段上）
			current = { ...current, ...body } as Task;
			return HttpResponse.json(current);
		}),
	);

	return { posts };
}

describe('任务详情：渲染', () => {
	it('渲染标题、编号与基础字段', async () => {
		mockTask();
		renderApp(`/tasks/${TASK_ID}`);

		expect(await screen.findByRole('heading', { name: '写文档' })).toBeInTheDocument();
		expect(screen.getByText('PRJ-7')).toBeInTheDocument();
		expect(screen.getByTestId('detail-done')).toHaveTextContent('进行中');
		expect(screen.getByTestId('detail-due-date')).toHaveTextContent('2026-08-20');
	});

	/** ★ 到期日零值在详情页同样不能变成公元 1 年。 */
	it('★ 到期日零值显示"未设置"而不是 0001-01-01', async () => {
		mockTask(task({ due_date: ZERO_TIME }));
		renderApp(`/tasks/${TASK_ID}`);

		expect(await screen.findByTestId('detail-due-date')).toHaveTextContent('未设置');
		expect(screen.queryByText(/0001-01-01/)).not.toBeInTheDocument();
	});

	it('非法任务 id 给提示而不是拿 NaN 打接口', async () => {
		let requested = false;
		server.use(
			http.get(`${API}/tasks/:id`, () => {
				requested = true;
				return HttpResponse.json(task());
			}),
		);

		renderApp('/tasks/abc');

		expect(await screen.findByTestId('invalid-task-route')).toHaveTextContent('无效的任务');
		await waitFor(() => expect(requested).toBe(false));
	});

	it('接口报错时展示后端消息', async () => {
		server.use(
			http.get(`${API}/tasks/${TASK_ID}`, () =>
				HttpResponse.json({ code: 4001, message: '任务不存在' }, { status: 404 }),
			),
		);
		renderApp(`/tasks/${TASK_ID}`);

		expect(await screen.findByRole('alert')).toHaveTextContent('任务不存在');
	});

	/**
	 * ★ 编辑器是懒加载的（TipTap 占主包一大半，切成独立 chunk）。
	 * 占位**不能用 `role="alert"`** —— 这是"还没到"，不是"坏了"；
	 * 读屏把它播成告警会让人以为出错（与 F05a 的 Gantt 占位同一条区分）。
	 */
	it('★ 编辑器懒加载的占位不是告警', async () => {
		mockTask();
		renderApp(`/tasks/${TASK_ID}`);

		await screen.findByRole('heading', { name: '写文档' });
		// 加载完成后编辑器就位，且整个过程没有把占位当成错误播出去
		await screen.findByTestId('description-editor');
		expect(screen.queryByTestId('description-loading')).not.toBeInTheDocument();
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
	});

	/** x-max-permission=0 是只读：不给编辑控件，而不是让用户改完吃 403。 */
	it('只读权限时不渲染编辑控件', async () => {
		mockTask(task(), { maxPermission: '0' });
		renderApp(`/tasks/${TASK_ID}`);

		expect(await screen.findByTestId('read-only-notice')).toBeInTheDocument();
		expect(screen.queryByTestId('task-priority')).not.toBeInTheDocument();
		expect(screen.getByTestId('toggle-done')).toBeDisabled();
	});
});

describe('★ 任务详情：AC-6 全量替换', () => {
	/**
	 * ★ 本任务最核心的一条。POST 是全量替换（tasks.go:1251-1253 传 nil fields），
	 * 只发改动字段会把其余 14 列写成零值，接口照样 200。
	 */
	it('★ 只勾选 done，请求体仍带上全部可写列', async () => {
		const mock = mockTask();
		renderApp(`/tasks/${TASK_ID}`);

		await screen.findByRole('heading', { name: '写文档' });
		await userEvent.click(screen.getByTestId('toggle-done'));

		await waitFor(() => expect(mock.posts).toHaveLength(1));
		const body = mock.posts[0]!;

		for (const column of WRITABLE_TASK_COLUMNS) {
			expect(body, `缺列 ${column} 会把它静默清成零值`).toHaveProperty(column);
		}
	});

	/** ★ 其余字段必须是**原值**，不是"存在但为零值"。 */
	it('★ 只勾选 done 时，其余字段的值原样回传（不是零值）', async () => {
		const mock = mockTask();
		renderApp(`/tasks/${TASK_ID}`);

		await screen.findByRole('heading', { name: '写文档' });
		await userEvent.click(screen.getByTestId('toggle-done'));

		await waitFor(() => expect(mock.posts).toHaveLength(1));
		expect(mock.posts[0]).toMatchObject({
			done: true,
			title: '写文档',
			description: '原描述',
			priority: 3,
			percent_done: 0.5,
			hex_color: 'ff0000',
			// ★ 漏了它任务会从项目里消失
			project_id: 12,
			bucket_id: 4,
			due_date: '2026-08-20T00:00:00Z',
		});
	});

	it('★ 改优先级时同样回传完整对象，done 不被清掉', async () => {
		const mock = mockTask(task({ done: true }));
		renderApp(`/tasks/${TASK_ID}`);

		await screen.findByRole('heading', { name: '写文档' });
		await userEvent.selectOptions(screen.getByTestId('task-priority'), '5');

		await waitFor(() => expect(mock.posts).toHaveLength(1));
		expect(mock.posts[0]).toMatchObject({ priority: 5, done: true, title: '写文档' });
	});

	/** ★ 清空到期日发零值字符串，不是 null（发 null 后端 412）。 */
	it('★ 清空到期日发零值字符串而不是 null', async () => {
		const mock = mockTask();
		renderApp(`/tasks/${TASK_ID}`);

		await screen.findByRole('heading', { name: '写文档' });
		await userEvent.click(screen.getByTestId('clear-due-date'));

		await waitFor(() => expect(mock.posts).toHaveLength(1));
		expect(mock.posts[0]!.due_date).toBe(ZERO_TIME);
		expect(mock.posts[0]!.due_date).not.toBeNull();
	});

	/**
	 * ★ 描述走的是同一条全量替换路径（F08b）。富文本编辑器很容易被写成
	 * "只 POST description 一个字段"，那会把标题、优先级、project_id 一起清掉。
	 */
	it('★ 保存描述时同样回传全部可写列', async () => {
		const mock = mockTask();
		renderApp(`/tasks/${TASK_ID}`);

		await screen.findByRole('heading', { name: '写文档' });
		const editor = await screen.findByTestId('description-editor');
		await userEvent.click(editor);
		await userEvent.type(editor, '补充说明');
		await userEvent.tab();

		await waitFor(() => expect(mock.posts).toHaveLength(1));
		const body = mock.posts[0]!;

		for (const column of WRITABLE_TASK_COLUMNS) {
			expect(body, `缺列 ${column} 会把它静默清成零值`).toHaveProperty(column);
		}
		expect(body.description).toContain('补充说明');
		expect(body).toMatchObject({ title: '写文档', priority: 3, project_id: 12 });
	});

	it('只读字段不进请求体', async () => {
		const mock = mockTask();
		renderApp(`/tasks/${TASK_ID}`);

		await screen.findByRole('heading', { name: '写文档' });
		await userEvent.click(screen.getByTestId('toggle-done'));

		await waitFor(() => expect(mock.posts).toHaveLength(1));
		expect(mock.posts[0]).not.toHaveProperty('identifier');
		expect(mock.posts[0]).not.toHaveProperty('index');
	});
});

describe('任务详情：乐观更新与错误', () => {
	it('勾选后立刻反映在界面上', async () => {
		mockTask();
		renderApp(`/tasks/${TASK_ID}`);

		await screen.findByRole('heading', { name: '写文档' });
		await userEvent.click(screen.getByTestId('toggle-done'));

		await waitFor(() => expect(screen.getByTestId('detail-done')).toHaveTextContent('已完成'));
	});

	it('保存失败时回滚并展示后端消息', async () => {
		server.use(
			http.get(`${API}/tasks/${TASK_ID}`, () => HttpResponse.json(task())),
			http.post(`${API}/tasks/${TASK_ID}`, () =>
				HttpResponse.json({ code: 4001, message: '保存失败了' }, { status: 500 }),
			),
		);
		renderApp(`/tasks/${TASK_ID}`);

		await screen.findByRole('heading', { name: '写文档' });
		await userEvent.click(screen.getByTestId('toggle-done'));

		expect(await screen.findByTestId('save-error')).toHaveTextContent('保存失败了');
		await waitFor(() => expect(screen.getByTestId('detail-done')).toHaveTextContent('进行中'));
	});

	/**
	 * ★ 校验失败是 412 + code 2002 + invalid_fields，可以做字段级提示。
	 */
	it('★ 412 校验错误时列出 invalid_fields', async () => {
		server.use(
			http.get(`${API}/tasks/${TASK_ID}`, () => HttpResponse.json(task())),
			http.post(`${API}/tasks/${TASK_ID}`, () =>
				HttpResponse.json(
					{ code: 2002, message: '字段校验失败', invalid_fields: ['priority', 'due_date'] },
					{ status: 412 },
				),
			),
		);
		renderApp(`/tasks/${TASK_ID}`);

		await screen.findByRole('heading', { name: '写文档' });
		await userEvent.click(screen.getByTestId('toggle-done'));

		expect(await screen.findByTestId('invalid-fields')).toHaveTextContent('priority、due_date');
	});

	/**
	 * ★ 但**不是所有 412 都带 invalid_fields** —— 业务规则类的（如桶满 10004）就没有。
	 * 按 status===412 推断"一定有 invalid_fields"会在这里崩或渲染出空清单。
	 */
	it('★ 不带 invalid_fields 的 412（业务规则类）只显示消息，不渲染空的字段清单', async () => {
		server.use(
			http.get(`${API}/tasks/${TASK_ID}`, () => HttpResponse.json(task())),
			http.post(`${API}/tasks/${TASK_ID}`, () =>
				HttpResponse.json({ code: 10004, message: '这一列已满' }, { status: 412 }),
			),
		);
		renderApp(`/tasks/${TASK_ID}`);

		await screen.findByRole('heading', { name: '写文档' });
		await userEvent.click(screen.getByTestId('toggle-done'));

		expect(await screen.findByTestId('save-error')).toHaveTextContent('这一列已满');
		expect(screen.queryByTestId('invalid-fields')).not.toBeInTheDocument();
	});
});

describe('任务详情：优先级选项走 i18n', () => {
	/**
	 * ★★★ 切到英文后**优先级选项真的变英文**。
	 *
	 * ## 同一个坑的第三次
	 *
	 * `PRIORITIES` 与 `TASK_COLUMNS`、zod schema 的校验消息一样是**模块级常量、
	 * 只算一次**，所以必须存 key、渲染时才 `t()`。三处的共同点是：
	 * **用户可见文字被放进了一个只算一次的常量里**。
	 *
	 * 三次我都是靠变异验证才发现"改回写死文字全绿"——**说明这一族缺的不是某条断言，
	 * 而是一条规矩**：凡是模块级常量里存用户可见文字，就得配一条切语言的断言。
	 *
	 * 判别式：locale 设 en，断言选项是英文。存文字的实现在这里仍显示中文。
	 */
	it('★★★ 切到英文后优先级选项是英文（不是写死的中文）', async () => {
		useUIStore.setState({ locale: 'en' });
		mockTask(task());
		renderApp(`/tasks/${TASK_ID}`);

		const select = await screen.findByTestId('task-priority');
		// task.priority.doNow 的 en 译文
		expect(select).toHaveTextContent('DO NOW');
		expect(select).not.toHaveTextContent('马上做');
	});
});
