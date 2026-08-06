import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { ProjectView, ViewKind } from '@/api/views';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render';

const API = '*/api/v1';

function view(id: number, view_kind: ViewKind, project_id = 12): ProjectView {
	return { id, project_id, title: view_kind, view_kind };
}

function mockViews() {
	server.use(
		http.get(`${API}/projects/:projectId/views`, () =>
			HttpResponse.json([view(1, 'list'), view(2, 'gantt'), view(3, 'table'), view(4, 'kanban')], {
				headers: { 'x-pagination-result-count': '4', 'x-pagination-total-pages': '1' },
			}),
		),
	);
}

/** 记录每次任务请求收到的 query，用来断言 filter 是**原样**发出去的。 */
function captureTaskRequests() {
	const urls: URL[] = [];
	server.use(
		http.get(`${API}/projects/:projectId/views/:viewId/tasks`, ({ request }) => {
			urls.push(new URL(request.url));
			return HttpResponse.json([], {
				headers: { 'x-pagination-result-count': '0', 'x-pagination-total-pages': '0' },
			});
		}),
	);
	return urls;
}

/** 让任务端点返回一个指定的错误体。 */
function mockTaskError(status: number, body: { code: number; message: string }) {
	server.use(
		http.get(`${API}/projects/:projectId/views/:viewId/tasks`, () =>
			HttpResponse.json(body, { status }),
		),
	);
}

async function applyFilter(text: string) {
	const input = await screen.findByTestId('filter-input');
	await userEvent.clear(input);
	await userEvent.type(input, text);
	await userEvent.click(screen.getByTestId('filter-apply'));
}

describe('筛选条件提交', () => {
	it('★ filter 原样发给后端，不做 trim/转义/改写', async () => {
		mockViews();
		const urls = captureTaskRequests();

		renderApp('/projects/12/list');
		await screen.findByTestId('filter-bar');

		// ⚠️ **两端刻意留空格**，这是判别式取值，不是手滑。
		//
		// 这条用例名字里写着"不做 trim"，但它原来的数据是
		// `"done = false && assignees = 'bob'"` —— **两端本来就没有空白**，
		// 于是 trim 在它身上是恒等变换：实现里加一个 `.trim()`，这条照样绿。
		// 变异验证时实测确认过（第 4 / 45 条：数据落在两种实现的同解区里）。
		//
		// 引号是另一个维度（防转义/改写），与空白各管各的，两个都要留着。
		const raw = "  done = false && assignees = 'bob'  ";
		await applyFilter(raw);

		await waitFor(() => expect(urls.length).toBeGreaterThan(1));
		expect(urls.at(-1)!.searchParams.get('filter')).toBe(raw);
	});

	it('★ 未输入时不带 filter 参数（而不是带一个空值）', async () => {
		mockViews();
		const urls = captureTaskRequests();

		renderApp('/projects/12/list');
		await waitFor(() => expect(urls.length).toBeGreaterThan(0));

		expect(urls[0].searchParams.has('filter')).toBe(false);
	});

	it('★ 换筛选条件要回到第 1 页', async () => {
		// 新结果集通常更短，留在第 3 页会得到空列表，
		// 而它与"筛选结果为空"在界面上完全无法区分。
		mockViews();
		const urls = captureTaskRequests();

		renderApp('/projects/12/list?page=3');
		await screen.findByTestId('filter-bar');
		await waitFor(() => expect(urls.length).toBeGreaterThan(0));
		expect(urls[0].searchParams.get('page')).toBe('3');

		await applyFilter('done = true');

		await waitFor(() => expect(urls.length).toBeGreaterThan(1));
		// page 总是显式发出（缺省为 1），所以判据是"值回到 1"，不是"这个键消失了"。
		// 没有重置的话这里会是 3 —— 两个值不同，断言才有判别力。
		expect(urls.at(-1)!.searchParams.get('page')).toBe('1');
	});

	it('筛选条件在 URL 上，切换视图不丢', async () => {
		mockViews();
		const urls = captureTaskRequests();

		renderApp('/projects/12/list?filter=done+%3D+true');
		await waitFor(() => expect(urls.length).toBeGreaterThan(0));

		expect(urls[0].searchParams.get('filter')).toBe('done = true');
		expect(await screen.findByTestId('filter-input')).toHaveValue('done = true');
	});
});

describe('编写期提示不拦提交', () => {
	it('★ assignees like 给出提示，但请求照发（后端对它返回 200）', async () => {
		// ☠ 这条同时钉两件事：提示出现，且**没有**被前端拦下。
		//   只断言提示出现的话，一个"提示 + 禁用提交"的实现照样绿 ——
		//   而那正是 F10 立规矩要防的"UI 拦得住、API 拦不住"。
		mockViews();
		const urls = captureTaskRequests();

		renderApp('/projects/12/list');
		await screen.findByTestId('filter-bar');
		await applyFilter("assignees like 'zzz'");

		expect(screen.getByTestId('filter-hint-assignees-like-dropped')).toBeInTheDocument();
		await waitFor(() => expect(urls.length).toBeGreaterThan(1));
		expect(urls.at(-1)!.searchParams.get('filter')).toBe("assignees like 'zzz'");
	});

	it('正常写法不出现提示', async () => {
		mockViews();
		captureTaskRequests();

		renderApp('/projects/12/list');
		await screen.findByTestId('filter-bar');
		await applyFilter("assignees = 'bob'");

		expect(screen.queryByTestId('filter-hints')).not.toBeInTheDocument();
	});
});

describe('错误展示：五个码分层，后端 message 一律原样透出', () => {
	it('★ 4024 表达式非法：原样展示 message（含 parser 原文），并说明引号是预处理加的', async () => {
		// 实测语料 datemath.anchored_datemath_is_rejected 的原样响应体。
		// message 里那对引号是**服务端预处理加的**，用户没打过 —— 不说明的话用户会去找自己没写的引号。
		const message =
			"The filter expression 'due_date > '2026-01-01'||+1M/d' is invalid: unexpected character '+'";
		mockViews();
		mockTaskError(400, { code: 4024, message });

		renderApp('/projects/12/list');

		const shown = await screen.findByTestId('filter-error-message');
		// 逐字原样：这是唯一能告诉用户错在哪的信息，不许改写或截断
		expect(shown).toHaveTextContent(message);
		expect(screen.getByTestId('filter-error-preprocessed-note')).toBeInTheDocument();
	});

	it('★ 4017 比较符非法：必须说明"服务端先校验比较符再校验字段"', async () => {
		// 实现侧注释原文：a filter that is wrong in both ways is 4017。
		// 不说这句，用户改对比较符后又冒出 4016，会以为前端在骗他。
		mockViews();
		mockTaskError(400, {
			code: 4017,
			message: "The task filter comparator '=<' is invalid.",
		});

		renderApp('/projects/12/list');

		expect(await screen.findByTestId('filter-error-message')).toHaveTextContent("'=<' is invalid");
		expect(screen.getByTestId('filter-error-explanation')).toHaveTextContent(
			'先校验比较符再校验字段',
		);
	});

	it('★ 4019 取值非法：要说明日期错在哪服务端给不出来', async () => {
		// datemath 的解析错误被上游丢弃（"Nothing the parser says reaches the client"），
		// 4019 只内插 field 与 value，所以这一格必须由前端补可用语法。
		mockViews();
		mockTaskError(400, {
			code: 4019,
			message: "The task filter value 'nextweek' for field 'due_date' is invalid.",
		});

		renderApp('/projects/12/list');

		expect(await screen.findByTestId('filter-error-message')).toHaveTextContent('nextweek');
		expect(screen.getByTestId('filter-error-explanation')).toHaveTextContent('now+30d');
	});

	it('★ 4018 连接符非法也要被认成筛选错误（任务卡上漏了这个码）', async () => {
		mockViews();
		mockTaskError(400, {
			code: 4018,
			message: "The task filter concatinator 'and' is invalid.",
		});

		renderApp('/projects/12/list');

		expect(await screen.findByTestId('filter-error')).toBeInTheDocument();
		expect(screen.getByTestId('filter-error-explanation')).toHaveTextContent('&&');
	});

	it('4016 未知字段', async () => {
		mockViews();
		mockTaskError(400, { code: 4016, message: "The task field 'nosuchfield' is invalid." });

		renderApp('/projects/12/list');

		expect(await screen.findByTestId('filter-error-message')).toHaveTextContent('nosuchfield');
	});

	it('★ 非筛选类错误不套用筛选文案（403 不是"筛选条件无法执行"）', async () => {
		// ☠ 反例承重：若实现按 status===400 或"有 message 就当筛选错误"分流，这条会红。
		mockViews();
		mockTaskError(403, { code: 4005, message: '你没有权限查看这个项目的任务' });

		renderApp('/projects/12/list');

		expect(await screen.findByText('你没有权限查看这个项目的任务')).toBeInTheDocument();
		expect(screen.queryByTestId('filter-error')).not.toBeInTheDocument();
	});
});
