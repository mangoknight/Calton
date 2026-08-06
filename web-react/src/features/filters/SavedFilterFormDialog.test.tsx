import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { SavedFilter } from '@/api/saved-filters';
import type { ProjectView, ViewKind } from '@/api/views';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';

/**
 * ⚠️ **这个文件不按可见文案定位元素**，一律走 testid（F13 规矩，
 * 详见 `components/layout/AppShell.test.tsx` 的头注）。
 *
 * 这里验的是**行为**：请求打到哪个方法、请求体长什么样、跳到哪个路由、
 * 缓存失效了没有。这几件事与弹窗上写什么字、界面是哪国语言全都无关 ——
 * 按文案查的话，F13 换一次措辞它们就整批红，而那批红什么也不说明。
 *
 * 唯一保留的文案断言是"空标题就地报错"那条，且**只断言有没有 alert 出现**，
 * 不断言那句话具体怎么写。
 */

function view(id: number, view_kind: ViewKind, project_id = 12): ProjectView {
	return { id, project_id, title: view_kind, view_kind };
}

function mockViews() {
	server.use(
		http.get(`${API}/projects/:projectId/views`, () =>
			HttpResponse.json([view(1, 'list')], {
				headers: { 'x-pagination-result-count': '1', 'x-pagination-total-pages': '1' },
			}),
		),
		http.get(`${API}/projects/:projectId/views/:viewId/tasks`, () =>
			HttpResponse.json([], {
				headers: { 'x-pagination-result-count': '0', 'x-pagination-total-pages': '0' },
			}),
		),
	);
}

interface CreateMock {
	/** PUT /filters 收到的请求体，按发生顺序。 */
	bodies: Record<string, unknown>[];
	/** GET /projects 被打了几次 —— 用来验证侧栏清单会被刷新。 */
	projectListHits: number;
}

/**
 * ⚠️ 新建走的是 **`PUT /filters`**，不是 POST。
 * v1 里 PUT 是新建、POST 是更新（与多数 REST 约定相反）。
 * 这个 mock **只登记 PUT** —— 如果实现改用了 POST，请求会落到默认 handler 上，
 * `bodies` 保持为空，用例随之变红。
 */
function mockCreate(
	// ⚠️ 故意用 Partial 而不是 SavedFilter：其中一条用例要发一个**缺 id** 的响应，
	// 而"响应形状与类型声明不一致"正是本项目反复出事的地方，测试得能表达它。
	response: Partial<SavedFilter> = { id: 7, title: '我的未完成' },
): CreateMock {
	const mock: CreateMock = { bodies: [], projectListHits: 0 };

	server.use(
		http.put(`${API}/filters`, async ({ request }) => {
			mock.bodies.push((await request.json()) as Record<string, unknown>);
			return HttpResponse.json(response);
		}),
		http.get(`${API}/projects`, () => {
			mock.projectListHits += 1;
			return HttpResponse.json([], {
				headers: { 'x-pagination-result-count': '0', 'x-pagination-total-pages': '0' },
			});
		}),
	);

	return mock;
}

/** 打开筛选条件输入框旁边的「保存筛选器」弹窗。 */
async function openSaveDialog(path = '/projects/12/list') {
	renderApp(path);
	await screen.findByTestId('filter-bar');
	await userEvent.click(screen.getByTestId('save-filter-button'));
	return screen.findByTestId('saved-filter-form');
}

describe('新建保存的筛选器', () => {
	it('★ 新建走 PUT /filters，带上标题与筛选表达式', async () => {
		mockViews();
		const mock = mockCreate();

		await openSaveDialog('/projects/12/list?filter=done+%3D+false');
		await userEvent.type(screen.getByTestId('filter-title-input'), '我的未完成');
		await userEvent.click(screen.getByTestId('filter-save-submit'));

		await waitFor(() => expect(mock.bodies).toHaveLength(1));
		expect(mock.bodies[0]).toEqual({
			title: '我的未完成',
			filters: { filter: 'done = false' },
		});
	});

	/**
	 * ★★ 表达式**原样发出**，不 trim。
	 *
	 * 与 `filter-param.ts` 同一条规矩：纯空白在后端不等于空筛选
	 * （`parse_task_filter` 只对**恰好为空串**短路），前端顺手 trim
	 * 就把一个后端会报错的输入悄悄变成了"没有筛选"。
	 *
	 * ⚠️ 判别式数据：表达式两端**必须真的带空白**，否则 trim 是恒等变换，
	 * 这条断言对"实现里加了 trim"零防护能力（实践第 4 条）。
	 */
	it('★★ 筛选表达式两端的空白原样保留，不 trim', async () => {
		mockViews();
		const mock = mockCreate();

		// 非不动点：前后各有一个空格，trim 会改变它
		const raw = '  done = false  ';
		await openSaveDialog(`/projects/12/list?filter=${encodeURIComponent(raw)}`);
		await userEvent.type(screen.getByTestId('filter-title-input'), '带空白的');
		await userEvent.click(screen.getByTestId('filter-save-submit'));

		await waitFor(() => expect(mock.bodies).toHaveLength(1));
		expect((mock.bodies[0].filters as { filter: string }).filter).toBe(raw);
	});

	/** 标题反过来**要** trim —— 它是给人看的名字，不是要发给 parser 的表达式。 */
	it('标题两端空白被 trim 掉', async () => {
		mockViews();
		const mock = mockCreate();

		await openSaveDialog();
		await userEvent.type(screen.getByTestId('filter-title-input'), '  我的未完成  ');
		await userEvent.click(screen.getByTestId('filter-save-submit'));

		await waitFor(() => expect(mock.bodies).toHaveLength(1));
		expect(mock.bodies[0].title).toBe('我的未完成');
	});

	it('★ 空标题不发请求，就地报错', async () => {
		mockViews();
		const mock = mockCreate();

		await openSaveDialog();
		// 只有空白，trim 后为空
		await userEvent.type(screen.getByTestId('filter-title-input'), '   ');
		await userEvent.click(screen.getByTestId('filter-save-submit'));

		// 只断言"报了错"，不断言那句话怎么写 —— 文案是 filters.create.titleRequired，
		// 会随语言变；"空标题不发请求"这个行为不会
		expect(
			await within(screen.getByTestId('saved-filter-form')).findByRole('alert'),
		).toBeInTheDocument();
		expect(mock.bodies).toHaveLength(0);
	});

	/**
	 * ★★ 新建成功后跳到 **`/filters/{正的 filter id}`**。
	 *
	 * 不是 `/projects/-8/list`。负数只在调接口时出现、不进 URL ——
	 * 否则 `parseRouteId` 就得为了容纳它而放宽，
	 * 而那道守卫真正在挡的是 `/projects/new/list`。
	 */
	it('★★ 成功后跳到 /filters/{正的 id}，URL 里不出现负数', async () => {
		mockViews();
		mockCreate({ id: 7, title: '我的未完成' });
		server.use(
			http.get(`${API}/filters/7`, () => HttpResponse.json({ id: 7, title: '我的未完成' })),
		);

		const { router } = renderApp('/projects/12/list');
		await screen.findByTestId('filter-bar');
		await userEvent.click(screen.getByTestId('save-filter-button'));
		await screen.findByTestId('saved-filter-form');
		await userEvent.type(screen.getByTestId('filter-title-input'), '我的未完成');
		await userEvent.click(screen.getByTestId('filter-save-submit'));

		await waitFor(() => expect(router.state.location.pathname).toBe('/filters/7'));
		expect(router.state.location.pathname).not.toContain('-');
	});

	/**
	 * ★★ 响应里没有可用 id 时**不跳转**，而不是跳去 `/filters/undefined`。
	 *
	 * 创建本身是成功的，把它显示成一个坏页面比不跳更糟。
	 * 这条同时钉住"不假定响应形状"——列表与单条形状不同在本项目已出现多次。
	 */
	it('★★ 响应缺 id 时留在原地，不跳到 /filters/undefined', async () => {
		mockViews();
		const mock = mockCreate({ title: '我的未完成' }); // 故意不给 id

		const { router } = renderApp('/projects/12/list');
		await screen.findByTestId('filter-bar');
		await userEvent.click(screen.getByTestId('save-filter-button'));
		await screen.findByTestId('saved-filter-form');
		await userEvent.type(screen.getByTestId('filter-title-input'), '我的未完成');
		await userEvent.click(screen.getByTestId('filter-save-submit'));

		await waitFor(() => expect(mock.bodies).toHaveLength(1));
		// 弹窗关掉（说明走的是成功分支），但路径没动
		await waitFor(() => expect(screen.queryByTestId('saved-filter-form')).not.toBeInTheDocument());
		expect(router.state.location.pathname).toBe('/projects/12/list');
	});

	/**
	 * ★★ 新建后必须**同时失效 `GET /projects`**。
	 *
	 * 侧栏的筛选器清单是从项目列表里的负 ID 伪项目派生的
	 * （v1 没有 `GET /filters` 列表端点）。只失效 saved-filter 自己的 key，
	 * 新筛选器不会出现在侧栏——这是"没有列表端点"在缓存层的直接后果。
	 */
	it('★★ 新建后重新拉取 GET /projects（侧栏清单的唯一数据源）', async () => {
		mockViews();
		const mock = mockCreate();

		await openSaveDialog();
		const before = mock.projectListHits;

		await userEvent.type(screen.getByTestId('filter-title-input'), '我的未完成');
		await userEvent.click(screen.getByTestId('filter-save-submit'));

		await waitFor(() => expect(mock.projectListHits).toBeGreaterThan(before));
	});

	it('后端报错时弹窗不关，就地显示错误', async () => {
		mockViews();
		server.use(
			http.put(`${API}/filters`, () =>
				HttpResponse.json({ code: 4001, message: '标题不能为空' }, { status: 412 }),
			),
		);

		await openSaveDialog();
		await userEvent.type(screen.getByTestId('filter-title-input'), '我的未完成');
		await userEvent.click(screen.getByTestId('filter-save-submit'));

		expect(await screen.findByTestId('saved-filter-form-error')).toHaveTextContent('标题不能为空');
		expect(screen.getByTestId('saved-filter-form')).toBeInTheDocument();
	});

	/**
	 * ★ 重开弹窗要取**当时**的表达式，不能留着上次那份。
	 *
	 * ⚠️ 判别式数据：两次的表达式必须**不同**，否则"沿用旧值"与"取新值"同解，
	 * 这条用例什么也验不了（实践第 4 条）。
	 *
	 * ⚠️ 弹窗里的输入框与 FilterBar 的输入框标签同名（都是"筛选条件"），
	 * 所以弹窗内的查询一律走 `within(dialog)` —— 直接 `screen.getByLabelText`
	 * 会命中两个，或者更糟：命中 FilterBar 那个，于是断言测的是它自己刚打的字。
	 */
	it('★ 重新打开时取当前的筛选表达式，不是上次那份', async () => {
		mockViews();
		mockCreate();

		const dialog = await openSaveDialog('/projects/12/list?filter=done+%3D+false');
		expect(within(dialog).getByTestId('filter-expression-input')).toHaveValue('done = false');

		await userEvent.click(within(dialog).getByTestId('filter-save-cancel'));
		await waitFor(() => expect(screen.queryByTestId('saved-filter-form')).not.toBeInTheDocument());

		// 改掉 FilterBar 里的条件（不点"应用"），再打开一次
		const bar = within(screen.getByTestId('filter-bar')).getByRole('textbox');
		await userEvent.clear(bar);
		await userEvent.type(bar, 'priority >= 3');
		await userEvent.click(screen.getByTestId('save-filter-button'));

		const reopened = await screen.findByTestId('saved-filter-form');
		expect(within(reopened).getByTestId('filter-expression-input')).toHaveValue('priority >= 3');
	});
});
