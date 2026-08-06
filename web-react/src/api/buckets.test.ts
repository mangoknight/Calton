import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { CaltonClient } from './client';
import { ContractViolationError } from './errors';
import { isBucketFull, listBucketsWithTasks } from './buckets';
import { createTokenStore } from './token-store';
import { server } from '@/test/msw';

const BASE = 'http://api.test/api/v1';

function makeClient() {
	// createTokenStore 收的是 Storage（传 null = 不落盘），token 用 set 塞
	const tokens = createTokenStore(null);
	tokens.set('jwt');
	return new CaltonClient({ baseUrl: BASE, tokenStore: tokens });
}

function boardResponse(items: unknown[]) {
	return HttpResponse.json(items, {
		headers: {
			'x-pagination-result-count': String(items.length),
			'x-pagination-total-pages': items.length ? '1' : '0',
		},
	});
}

describe('isBucketFull', () => {
	/** ★ limit 为 0 是"不限"，不是"容量为 0 所以永远满"。判反了整块板面都会挂红。 */
	it.each([0, undefined, -1])('limit 为 %s 时永远不算满', (limit) => {
		expect(isBucketFull({ count: 999, limit: limit as number })).toBe(false);
	});

	it.each([
		[2, 3, false],
		[3, 3, true],
		[4, 3, true],
	])('count=%s limit=%s → %s', (count, limit, expected) => {
		expect(isBucketFull({ count, limit })).toBe(expected);
	});
});

describe('listBucketsWithTasks', () => {
	it('返回桶结构', async () => {
		server.use(
			http.get(`${BASE}/projects/12/views/4/tasks`, () =>
				boardResponse([
					{ id: 1, title: 'To-Do', project_view_id: 4, count: 2, limit: 0, tasks: [{ id: 9 }] },
				]),
			),
		);

		const result = await listBucketsWithTasks(12, 4, {}, makeClient());
		expect(result.items[0]).toMatchObject({ id: 1, title: 'To-Do', count: 2 });
	});

	/**
	 * ★ 这个端点是多态的：view 的 bucket_configuration_mode 为 none 时，
	 * 后端返回的是扁平的 Task[]。当成 Bucket[] 渲染会得到一排没有标题的空列 ——
	 * 不报错，只是板面是空的，极难回溯。所以在这里如实拦下。
	 */
	it('★ 拿到扁平任务列表（mode=none）时抛 ContractViolationError 而不是渲染空板面', async () => {
		server.use(
			http.get(`${BASE}/projects/12/views/4/tasks`, () =>
				boardResponse([{ id: 9, title: '任务 9', project_id: 12 }]),
			),
		);

		await expect(listBucketsWithTasks(12, 4, {}, makeClient())).rejects.toBeInstanceOf(
			ContractViolationError,
		);
		await expect(listBucketsWithTasks(12, 4, {}, makeClient())).rejects.toThrow(
			/bucket_configuration_mode/,
		);
	});

	it('空板面（一个桶都没有）不算契约违规', async () => {
		server.use(http.get(`${BASE}/projects/12/views/4/tasks`, () => boardResponse([])));

		await expect(listBucketsWithTasks(12, 4, {}, makeClient())).resolves.toMatchObject({
			items: [],
		});
	});

	/** kanban 分支会把 sortby 整个覆盖成 position asc，发排序参数没有意义。 */
	it('不发 sort_by（后端会覆盖，发了只会误导读代码的人）', async () => {
		let url: URL | null = null;
		server.use(
			http.get(`${BASE}/projects/12/views/4/tasks`, ({ request }) => {
				url = new URL(request.url);
				return boardResponse([]);
			}),
		);

		await listBucketsWithTasks(12, 4, {}, makeClient());
		expect(url!.searchParams.getAll('sort_by')).toEqual([]);
		expect(url!.searchParams.get('per_page')).toBe('50');
	});
});

/**
 * 把几条"看着像常识、其实读了源码才知道"的后端事实钉在 Go 源码上。
 * 它们全都是**静默出错**型的：判反了不会报错，只会让板面显示错的东西。
 */
describe('看板契约事实与 Go 源码对账', () => {
	const kanbanGo = resolve(process.cwd(), '..', 'pkg/models/kanban.go');
	const source = existsSync(kanbanGo) ? readFileSync(kanbanGo, 'utf8') : '';

	it('能读到 kanban.go（读不到则以下对账是假绿）', () => {
		expect(existsSync(kanbanGo)).toBe(true);
	});

	/**
	 * ★ `Bucket.ReadAll`（即 GET .../buckets）不给 Count 赋值，
	 * 所以它返回的 count 恒为 0 —— 这正是板面不能用那个端点的原因。
	 * 哪天后端补上了赋值，这条会红，届时可以考虑改用更轻的 buckets 端点。
	 */
	it('★ GET .../buckets 的 ReadAll 仍然不给 Count 赋值（故板面必须走 tasks 端点）', () => {
		const start = source.indexOf('func (b *Bucket) ReadAll');
		const end = source.indexOf('func GetTasksInBucketsForView');
		expect(start).toBeGreaterThan(-1);
		expect(end).toBeGreaterThan(start);
		expect(source.slice(start, end)).not.toMatch(/\.Count\s*=/);
	});

	/** ★ count 取的是 total（总数），不是本页条数。 */
	it('★ GetTasksInBucketsForView 把 Count 赋成 total 而非本页长度', () => {
		expect(source).toMatch(/bucket\.Count\s*=\s*total/);
	});

	/** ★ 删桶把任务搬到默认桶，不删任务 —— 删除确认文案依赖这条。 */
	it('★ Delete 把任务改挂到默认桶（不是删任务）', () => {
		const start = source.indexOf('func (b *Bucket) Delete');
		const body = source.slice(start, start + 2000);
		expect(body).toContain('defaultBucketID');
		expect(body).toMatch(/Update\(&TaskBucket\{BucketID: defaultBucketID\}\)/);
	});

	/** ★ 删最后一个桶被拒。前端预判禁用，这条锁住"预判仍然成立"。 */
	it('★ Delete 仍然拒绝删除最后一个桶', () => {
		const start = source.indexOf('func (b *Bucket) Delete');
		expect(source.slice(start, start + 800)).toContain('ErrCannotRemoveLastBucket');
	});

	/** ★ Update 只写这三列，回传别的字段不会生效。 */
	it('★ Update 只写 title / limit / position 三列', () => {
		const start = source.indexOf('func (b *Bucket) Update');
		const body = source.slice(start, start + 400);
		expect(body).toContain('"title"');
		expect(body).toContain('"limit"');
		expect(body).toContain('"position"');
	});
});
