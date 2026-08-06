import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';

import type { Bucket } from '@/api/buckets';
import { apiClient } from '@/api/client';
import type { Paginated } from '@/api/pagination';
import { createQueryClient } from '@/app/query-client';
import { server } from '@/test/msw';
import { applyTaskMove } from './board-move';
import { bucketKeys, useBoard } from './bucket-queries';
import { useMoveTask } from './useMoveTask';

const API = '*/api/v1';
const PROJECT_ID = 12;
const VIEW_ID = 4;

function bucket(id: number, tasks: { id: number; position: number }[]): Bucket {
	return {
		id,
		title: `列 ${id}`,
		project_view_id: VIEW_ID,
		count: tasks.length,
		limit: 0,
		tasks: tasks.map((t) => ({ ...t, title: `任务 ${t.id}` })),
	};
}

function seedBoard(): Paginated<Bucket> {
	return {
		items: [bucket(1, [{ id: 1, position: 100 }]), bucket(2, [{ id: 3, position: 300 }])],
		resultCount: 2,
		totalPages: 1,
	};
}

const MOVE = { taskId: 1, fromBucketId: 1, toBucketId: 2, position: 150 };

interface Calls {
	bucketMoves: unknown[];
	positions: unknown[];
	boardFetches: number;
}

/**
 * 两个写端点默认都成功。`fail` 指定哪一个改成失败——
 * 注意**另一个仍然会被发出去**，这正是"部分生效"的来源。
 */
/**
 * 一道闸：让写请求停在原地，好在"变更还在飞"的那一刻做断言。
 *
 * 没有它的话乐观中间态测不出来 —— mock 响应太快，
 * 乐观更新 → 成功 → 重取 整个周期在 waitFor 第一次轮询前就走完了，
 * 断言看到的永远是收敛后的状态，于是"有没有乐观更新"这件事根本没被测到。
 */
function gate() {
	let release!: () => void;
	const opened = new Promise<void>((resolve) => {
		release = resolve;
	});
	return { opened, release };
}

interface MockOptions {
	fail?: 'bucket' | 'position';
	/** 闸住两个写请求，用来观察"变更还在飞"时的乐观中间态。 */
	holdWrites?: Promise<void>;
	/**
	 * 闸住**第二次及以后**的板面 GET（即 onSettled 触发的那次重取）。
	 *
	 * 这条是测回滚的关键：不闸住的话，失败后状态会被重取拉回原样，
	 * 于是"回滚"这件事根本没被测到 —— 把回滚整段删掉测试照样全绿。
	 * （变异验证实测踩过。）
	 */
	holdBoardRefetch?: Promise<void>;
}

function mockEndpoints({ fail, holdWrites, holdBoardRefetch }: MockOptions = {}): Calls {
	const calls: Calls = { bucketMoves: [], positions: [], boardFetches: 0 };

	server.use(
		http.get(`${API}/projects/:p/views/:v/tasks`, async () => {
			calls.boardFetches += 1;
			if (holdBoardRefetch && calls.boardFetches > 1) await holdBoardRefetch;
			return HttpResponse.json(seedBoard().items, {
				headers: { 'x-pagination-result-count': '2', 'x-pagination-total-pages': '1' },
			});
		}),
		http.post(`${API}/projects/:p/views/:v/buckets/:b/tasks`, async ({ request }) => {
			calls.bucketMoves.push(await request.json());
			if (holdWrites) await holdWrites;
			if (fail === 'bucket') {
				return HttpResponse.json({ code: 10004, message: '这一列已满' }, { status: 412 });
			}
			return HttpResponse.json({});
		}),
		http.post(`${API}/tasks/:id/position`, async ({ request }) => {
			calls.positions.push(await request.json());
			if (holdWrites) await holdWrites;
			if (fail === 'position') {
				return HttpResponse.json({ code: 4001, message: '位置写入失败' }, { status: 500 });
			}
			return HttpResponse.json({});
		}),
	);

	return calls;
}

/**
 * ⚠️ 必须同时挂上 `useBoard`。
 *
 * `invalidateQueries` 只会重取**有活跃 observer** 的 query —— 只用 setQueryData
 * 铺一份数据、不挂订阅者的话，失效标记是打上了，但不会真的发请求，
 * 于是"成功后重取"那几条会假红。真实页面里 KanbanView 一直挂着 useBoard，
 * 测试脚手架要还原这一点，否则测的不是同一个东西。
 */
function setup() {
	apiClient.tokens.set('test-jwt');
	const queryClient = createQueryClient();
	queryClient.setDefaultOptions({ queries: { retry: false }, mutations: { retry: false } });
	queryClient.setQueryData(bucketKeys.board(PROJECT_ID, VIEW_ID), seedBoard());

	const wrapper = ({ children }: { children: ReactNode }) => (
		<QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
	);

	const { result } = renderHook(
		() => ({ board: useBoard(PROJECT_ID, VIEW_ID), move: useMoveTask(PROJECT_ID, VIEW_ID) }),
		{ wrapper },
	);
	const readBoard = () =>
		queryClient.getQueryData<Paginated<Bucket>>(bucketKeys.board(PROJECT_ID, VIEW_ID))!;

	return { result, readBoard, queryClient };
}

/** 把板面压成 "桶id:任务id,任务id" 的形式，断言读起来才不费劲。 */
function shape(board: Paginated<Bucket>): string[] {
	return board.items.map((b) => `${b.id}:${(b.tasks ?? []).map((t) => t.id).join(',')}`);
}

describe('★ 拖拽写入：两个请求并发发出', () => {
	it('★ 同时发 bucket 与 position 两个请求，且参数正确', async () => {
		const calls = mockEndpoints({});
		const { result } = setup();

		result.current.move.mutate(MOVE);

		await waitFor(() => expect(result.current.move.isSuccess).toBe(true));

		expect(calls.bucketMoves).toEqual([{ task_id: 1, bucket_id: 2, project_view_id: VIEW_ID }]);
		expect(calls.positions).toEqual([{ project_view_id: VIEW_ID, position: 150 }]);
	});

	/**
	 * ★ 并发而非串行：串行会让用户先看到卡片跳到目标列末尾、再跳到正确位置。
	 * 判据是"第一个请求还没返回时第二个已经发出" —— 用一个卡住的 bucket 请求来验。
	 */
	it('★ 第一个请求尚未返回时第二个已经发出（确实是并发不是串行）', async () => {
		const calls: { positions: number } = { positions: 0 };
		let releaseBucket: (() => void) | null = null;
		const bucketBlocked = new Promise<void>((resolve) => {
			releaseBucket = resolve;
		});

		server.use(
			http.get(`${API}/projects/:p/views/:v/tasks`, () =>
				HttpResponse.json([], {
					headers: { 'x-pagination-result-count': '0', 'x-pagination-total-pages': '0' },
				}),
			),
			http.post(`${API}/projects/:p/views/:v/buckets/:b/tasks`, async () => {
				await bucketBlocked;
				return HttpResponse.json({});
			}),
			http.post(`${API}/tasks/:id/position`, () => {
				calls.positions += 1;
				return HttpResponse.json({});
			}),
		);

		const { result } = setup();
		result.current.move.mutate(MOVE);

		// bucket 请求还堵着，position 请求必须已经出去了
		await waitFor(() => expect(calls.positions).toBe(1));
		releaseBucket!();
		await waitFor(() => expect(result.current.move.isSuccess).toBe(true));
	});
});

describe('★ 乐观更新与一致回滚', () => {
	it('★ 发起后、服务端返回前，本地就已经把卡片搬过去了', async () => {
		const { opened, release } = gate();
		mockEndpoints({ holdWrites: opened });
		const { result, readBoard } = setup();

		result.current.move.mutate(MOVE);

		// 写请求还堵在闸门后，此刻界面必须已经是搬完的样子
		await waitFor(() => expect(shape(readBoard())).toEqual(['1:', '2:1,3']));
		expect(result.current.move.isPending).toBe(true);

		release();
		await waitFor(() => expect(result.current.move.isSuccess).toBe(true));
	});

	/**
	 * ★ 这条要真的测到"回滚"，得同时满足两个条件，缺一条就变成假绿：
	 *
	 * 1. **先看到乐观态**再看回滚态。只断言"最终等于拖拽前"是没用的 ——
	 *    初始状态本来就等于拖拽前，断言在 t=0 就能通过。
	 * 2. **闸住 onSettled 的重取**。不闸的话，失败后重取会把板面拉回原样，
	 *    于是把 onError 里的回滚整段删掉测试照样全绿 —— 那测的是"最终会收敛"，
	 *    不是"回滚了"。
	 *
	 * 两条都是变异验证实测踩出来的（删掉回滚 → 原本 9 条全绿）。
	 *
	 * 注：react-query 会等 onSettled 的 promise，重取被闸住时 mutation 不会
	 * 进入 isError，所以这里不能等 isError，只能直接观察缓存。
	 */
	it.each(['bucket', 'position'] as const)(
		'★ %s 请求失败时，列归属与位置一起回滚到拖拽前',
		async (which) => {
			const writes = gate();
			const refetch = gate();
			mockEndpoints({ fail: which, holdWrites: writes.opened, holdBoardRefetch: refetch.opened });
			const { result, readBoard } = setup();
			const before = shape(readBoard());

			result.current.move.mutate(MOVE);

			// ① 先确认乐观更新真的生效了，否则后面的"回到原样"毫无意义
			await waitFor(() => expect(shape(readBoard())).toEqual(['1:', '2:1,3']));

			// ② 放行写请求让它失败；此时重取仍被闸住，能恢复状态的只剩回滚
			writes.release();
			await waitFor(() => expect(shape(readBoard())).toEqual(before));

			// 回滚是整块快照换回去的，所以列归属与位置天然一致，不存在"列回滚了位置没回滚"
			expect(readBoard().items[0]!.count).toBe(1);
			expect(readBoard().items[1]!.count).toBe(1);

			refetch.release();
			await waitFor(() => expect(result.current.move.isError).toBe(true));
		},
	);

	it('失败时把后端消息暴露出来（"这一列已满"要能显示给用户）', async () => {
		mockEndpoints({ fail: 'bucket' });
		const { result } = setup();

		result.current.move.mutate(MOVE);
		await waitFor(() => expect(result.current.move.isError).toBe(true));
		expect(result.current.move.error?.message).toBe('这一列已满');
	});

	/**
	 * ★ Promise.all 在第一个 reject 时就 reject，但**不取消另一个请求**。
	 * 所以失败时服务端可能是"改了列没改位置"的半截状态 ——
	 * 回滚只保证 UI 自洽，真正对齐要靠随后的重取。
	 */
	it('★ 一个失败时另一个请求仍然发了出去（半截状态确实存在，故必须重取）', async () => {
		const calls = mockEndpoints({ fail: 'position' });
		const { result } = setup();
		await waitFor(() => expect(calls.boardFetches).toBeGreaterThan(0));
		const baseline = calls.boardFetches;

		result.current.move.mutate(MOVE);
		await waitFor(() => expect(result.current.move.isError).toBe(true));

		expect(calls.bucketMoves).toHaveLength(1);
		expect(calls.positions).toHaveLength(1);
		await waitFor(() => expect(calls.boardFetches).toBeGreaterThan(baseline));
	});
});

describe('★ 成功之后仍然重取', () => {
	/**
	 * 这条不是"保险起见"。服务端在同一次调用里可能：
	 * 改 done / 把重复任务改送到默认列 / position 太小时重算整个视图。
	 * 乐观状态在这些情况下都是错的。
	 */
	it('★ 成功后重新拉板面（一次调用不止改一处，乐观状态不可信）', async () => {
		const calls = mockEndpoints({});
		const { result } = setup();

		// ⚠️ 基线要在 useBoard 挂载那次 GET 之后取。
		// 直接断言 boardFetches > 0 是假绿：挂载本身就已经拉过一次，
		// 把 onSettled 删掉这条照样过。变异验证实测踩过。
		await waitFor(() => expect(calls.boardFetches).toBeGreaterThan(0));
		const baseline = calls.boardFetches;

		result.current.move.mutate(MOVE);
		await waitFor(() => expect(result.current.move.isSuccess).toBe(true));
		await waitFor(() => expect(calls.boardFetches).toBeGreaterThan(baseline));
	});

	/**
	 * ★ 重取回来的服务端结果覆盖乐观状态 —— 哪怕服务端把任务放到了别处。
	 * 这一条正是"重复任务被改送到默认列"那种情况在前端的表现：
	 * 用户拖到 A 列，服务端放进了 B 列，界面最终必须显示 B。
	 */
	it('★ 服务端把任务放回原处时，界面跟服务端走而不是跟乐观状态走', async () => {
		const { opened, release } = gate();
		// GET 始终返回原始板面 = 模拟"服务端并没有按我们乐观的那样放"
		mockEndpoints({ holdWrites: opened });
		const { result, readBoard } = setup();

		result.current.move.mutate(MOVE);
		await waitFor(() => expect(shape(readBoard())).toEqual(['1:', '2:1,3']));

		release();
		await waitFor(() => expect(result.current.move.isSuccess).toBe(true));
		await waitFor(() => expect(shape(readBoard())).toEqual(['1:1', '2:3']));
	});
});

describe('★ 服务端改送落点（重复任务）', () => {
	/**
	 * coder-b 实测：重复任务被移进 done 列时，服务端把它改送到默认列，
	 * 但响应里嵌套的 `bucket.id` 仍回显你请求的 done 列，只有顶层 `bucket_id` 是真值。
	 *
	 * 光靠 onSettled 重取，用户会看到卡片先停在 done 列再跳走（像 bug）；
	 * 这里按顶层 bucket_id 提前纠正，把闪烁窗口压掉。
	 */
	/**
	 * ⚠️⚠️ 这组用例踩过一次**双重**假绿，两处都记下来：
	 *
	 * **一**：`onSettled` 的重取本身就会把板面拉成正确终态，所以任何朴素的
	 * "卡片最终出现在默认列"断言，把 `onSuccess` 整段删掉照样绿 —— 它验的是
	 * "最终会收敛"，不是"提前纠正了"。所以下面必须**闸住重取**，让 `onSuccess`
	 * 成为唯一能产生正确终态的路径。
	 *
	 * **二**：更隐蔽。这组原先的 mock 把板面 GET 注册在 `.../buckets`，
	 * 而板面实际拉的是 `.../views/{v}/tasks`（多态端点）。路径不匹配 ⇒ 重取一直失败
	 * ⇒ 断言"碰巧"承重了。删掉 onSuccess 确实会红，但红的原因不是断言设计得好，
	 * 而是重取坏了。把路径修对之后，同一条断言在删掉 onSuccess 后**照样绿**——
	 * 实测确认过。**一个靠别处的 bug 才成立的断言，等于没有断言。**
	 */
	function mockRedirect(
		landedBucketId: number,
		holds: { writes?: Promise<void>; refetch?: Promise<void> } = {},
	) {
		let fetches = 0;
		server.use(
			// ⚠️ 板面走的是多态的 tasks 端点，不是 /buckets
			http.get(`${API}/projects/:p/views/:v/tasks`, async () => {
				fetches += 1;
				if (holds.refetch && fetches > 1) await holds.refetch;
				return HttpResponse.json(seedBoard().items, {
					headers: { 'x-pagination-result-count': '2', 'x-pagination-total-pages': '1' },
				});
			}),
			http.post(`${API}/projects/:p/views/:v/buckets/:b/tasks`, async () => {
				if (holds.writes) await holds.writes;
				// 顶层是真实落点，嵌套的是请求回显 —— 两者故意不一致
				return HttpResponse.json({ task_id: 1, bucket_id: landedBucketId, bucket: { id: 2 } });
			}),
			http.post(`${API}/tasks/:id/position`, async () => {
				if (holds.writes) await holds.writes;
				return HttpResponse.json({});
			}),
		);
	}

	const MOVE_TO_DONE = { taskId: 1, fromBucketId: 1, toBucketId: 2, position: 100 };

	/**
	 * 这一条同时承担两件事，所以不再单开"别读嵌套 bucket.id"的用例：
	 * mock 里顶层 `bucket_id`=1（真值）、嵌套 `bucket.id`=2（请求回显），
	 * 读错字段就等于没纠正，卡片会停在列 2 —— 本条会红。
	 * （实测：把实现改成读 `moved?.bucket?.id`，本条确实红。）
	 */
	it('★ 重取被闸住时，onSuccess 仍把卡片纠正到顶层 bucket_id 指的那一列', async () => {
		const writes = gate();
		const refetch = gate();
		// 服务端说：任务实际落在列 1（重复任务被顺延回默认列），不是请求的列 2
		mockRedirect(1, { writes: writes.opened, refetch: refetch.opened });
		const { result, readBoard } = setup();

		result.current.move.mutate(MOVE_TO_DONE);

		// ① 先看到乐观态：卡片在用户拖到的列 2 —— 否则后面的"回到列 1"可能只是从没动过
		await waitFor(() => expect(shape(readBoard())).toEqual(['1:', '2:1,3']));

		// ② 放行写请求；重取仍闸着，能改变状态的只剩 onSuccess。
		//    正向断言落点（在列 1），不是"不在列 2"
		writes.release();
		await waitFor(() => expect(shape(readBoard())).toEqual(['1:1', '2:3']));

		refetch.release();
		await waitFor(() => expect(result.current.move.isSuccess).toBe(true));
	});

	it('落点与请求一致时不做多余纠正（卡片留在用户拖到的列）', async () => {
		const refetch = gate();
		mockRedirect(2, { refetch: refetch.opened });
		const { result, readBoard } = setup();

		result.current.move.mutate(MOVE_TO_DONE);
		await waitFor(() => expect(shape(readBoard())).toEqual(['1:', '2:1,3']));

		// 重取闸着，若 onSuccess 误纠正，这里会变成 ['1:1','2:3']
		await new Promise((resolve) => setTimeout(resolve, 20));
		expect(shape(readBoard())).toEqual(['1:', '2:1,3']);

		refetch.release();
		await waitFor(() => expect(result.current.move.isSuccess).toBe(true));
	});

	/** 响应里没有 bucket_id（契约不符）时不猜，交给 onSettled 重取。 */
	it('响应缺 bucket_id 时不做纠正，也不崩', async () => {
		const refetch = gate();
		server.use(
			http.get(`${API}/projects/:p/views/:v/tasks`, async () => {
				return HttpResponse.json(seedBoard().items, {
					headers: { 'x-pagination-result-count': '2', 'x-pagination-total-pages': '1' },
				});
			}),
			http.post(`${API}/projects/:p/views/:v/buckets/:b/tasks`, () => HttpResponse.json({})),
			http.post(`${API}/tasks/:id/position`, () => HttpResponse.json({})),
		);
		const { result, readBoard } = setup();

		result.current.move.mutate(MOVE_TO_DONE);
		await waitFor(() => expect(result.current.move.isSuccess).toBe(true));
		expect(() => shape(readBoard())).not.toThrow();

		refetch.release();
	});
});

/**
 * ★ 回归防线（coder-b 实测口径）：**"被顺延回默认列"不等于"完成了"**。
 *
 * 服务端在重复任务这条路径上会同时动 `done` 与所属列，前端很容易顺手从
 * "它在 done 列/不在 done 列"去反推 `done`。现在没有这么做 —— `applyTaskMove`
 * 全程不碰 `done` 字段。这条锁住它，免得日后有人"顺手补上"。
 */
describe('★ 移动不得改写 done', () => {
	it('★ applyTaskMove 只搬位置，不碰 done', () => {
		const before = seedBoard();
		const moved = applyTaskMove(before, MOVE_DONE_PROBE);

		const task = moved.items.flatMap((bucket) => bucket.tasks ?? []).find((item) => item.id === 1);

		// 搬进"done 列"（列 2）之后，done 仍是原值
		expect(task?.done).toBe(before.items[0]!.tasks![0]!.done);
	});
});

const MOVE_DONE_PROBE = { taskId: 1, fromBucketId: 1, toBucketId: 2, position: 100 };
