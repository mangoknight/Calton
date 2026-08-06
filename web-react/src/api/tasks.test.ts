import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { ZERO_TIME } from '@/lib/datetime';
import { buildTaskUpdatePayload, WRITABLE_TASK_COLUMNS, type Task } from './tasks';

const TASKS_GO = resolve(process.cwd(), '..', 'pkg/models/tasks.go');

function task(overrides: Partial<Task> = {}): Task {
	return {
		id: 7,
		title: '原标题',
		description: '原描述',
		done: false,
		due_date: '2026-08-20T09:00:00Z',
		start_date: ZERO_TIME,
		end_date: ZERO_TIME,
		repeat_after: 0,
		repeat_mode: 0,
		priority: 3,
		percent_done: 0.5,
		hex_color: 'ff0000',
		project_id: 12,
		bucket_id: 4,
		cover_image_attachment_id: 0,
		...overrides,
	};
}

/**
 * ★★ 可写列表必须与 Go 的 `colsToUpdate` 逐字一致。
 *
 * 这是 AC-6 在前端的落点，而且是**静默失败**型：少列一个字段，
 * 那一列会被写成零值、接口照样 200。少 `project_id` 的话任务会直接从项目里消失。
 */
describe('可写列表与 Go 的 colsToUpdate 对账', () => {
	const source = existsSync(TASKS_GO) ? readFileSync(TASKS_GO, 'utf8') : '';

	it('能读到 tasks.go（读不到则以下对账是假绿）', () => {
		expect(existsSync(TASKS_GO)).toBe(true);
	});

	function goColumns(): string[] {
		const start = source.indexOf('colsToUpdate := []string{');
		const end = source.indexOf('}', start);
		return [...source.slice(start, end).matchAll(/"([a-z_]+)"/g)].map((m) => m[1]!);
	}

	it('解析出的 Go 列表非空（解析失败会让下面的比较退化成空集相等）', () => {
		expect(goColumns().length).toBeGreaterThan(10);
	});

	it('★★ 前端可写列与 Go 的 colsToUpdate 逐字相同', () => {
		expect([...WRITABLE_TASK_COLUMNS].sort()).toEqual(goColumns().sort());
	});

	/**
	 * ★ 锁住"v1 的 POST 确实是全量替换"这个前提本身。
	 *
	 * `Task.Update` 传 nil ⇒ fieldSet 为空 ⇒ 没有任何一列回落旧值。
	 * 哪天后端改成传具体 fields（PATCH 语义），全量替换就不再必要，
	 * 前端也不该继续回传整个对象 —— 这条会红，提醒有人来重新判断。
	 */
	it('★ Task.Update 仍然以 nil fields 调用 updateSingleTask（= 全量替换）', () => {
		expect(source).toMatch(
			/func \(t \*Task\) Update\([^)]*\)[^{]*\{\s*return t\.updateSingleTask\(s, a, nil\)/,
		);
	});

	it('★ 回落旧值的分支确实以 len(fields) > 0 为条件', () => {
		expect(source).toMatch(/if len\(fields\) > 0 \{/);
	});
});

describe('buildTaskUpdatePayload', () => {
	it('★ 产出的键集合恒等于可写列（外加 id）', () => {
		const payload = buildTaskUpdatePayload(task());
		expect(Object.keys(payload).sort()).toEqual([...WRITABLE_TASK_COLUMNS, 'id'].sort());
	});

	/**
	 * ★ 本任务的验收要点：只改 done，其余字段必须原样回传。
	 * 漏任何一个都会把它清成零值，且不报错。
	 */
	it('★ 只改 done 时，其余字段逐个原样回传', () => {
		const base = task();
		const payload = buildTaskUpdatePayload(base, { done: true });

		expect(payload.done).toBe(true);
		expect(payload.title).toBe('原标题');
		expect(payload.description).toBe('原描述');
		expect(payload.priority).toBe(3);
		expect(payload.percent_done).toBe(0.5);
		expect(payload.hex_color).toBe('ff0000');
		expect(payload.due_date).toBe('2026-08-20T09:00:00Z');
		// ★ 漏了它任务会从项目里消失
		expect(payload.project_id).toBe(12);
		expect(payload.bucket_id).toBe(4);
	});

	it('补丁能改多个字段', () => {
		const payload = buildTaskUpdatePayload(task(), { priority: 5, title: '新标题' });
		expect(payload).toMatchObject({ priority: 5, title: '新标题', project_id: 12 });
	});

	/** ★ false / 0 / '' 是合法取值，不能被兜底逻辑吞掉换成默认值。 */
	it('★ 假值（false / 0 / 空串）原样保留，不被兜底顶替', () => {
		const payload = buildTaskUpdatePayload(
			task({ done: true, priority: 5, percent_done: 0.5, hex_color: 'abc' }),
			{ done: false, priority: 0, percent_done: 0, hex_color: '' },
		);

		expect(payload.done).toBe(false);
		expect(payload.priority).toBe(0);
		expect(payload.percent_done).toBe(0);
		expect(payload.hex_color).toBe('');
	});

	/**
	 * ★ 时间列缺失时兜底成零值字符串，**不是 null** —— 发 null 后端 412。
	 */
	it('★ 服务端没返回的时间列兜底成零值字符串而不是 null', () => {
		const bare = { id: 7, title: 't' } as Task;
		const payload = buildTaskUpdatePayload(bare);

		expect(payload.due_date).toBe(ZERO_TIME);
		expect(payload.start_date).toBe(ZERO_TIME);
		expect(payload.end_date).toBe(ZERO_TIME);
		expect(payload.due_date).not.toBeNull();
	});

	it('★ 任何一列都不会是 undefined（undefined 会被 JSON.stringify 整个丢掉）', () => {
		const bare = { id: 7, title: 't' } as Task;
		const payload = buildTaskUpdatePayload(bare);

		for (const column of WRITABLE_TASK_COLUMNS) {
			expect(payload[column], `${column} 不该是 undefined`).toBeDefined();
		}
		// 真正的判据：序列化一圈之后每一列都还在
		const roundTripped = JSON.parse(JSON.stringify(payload)) as Record<string, unknown>;
		expect(Object.keys(roundTripped).sort()).toEqual(Object.keys(payload).sort());
	});

	it('不把只读字段塞进去（identifier / index / created 等不在可写列里）', () => {
		const payload = buildTaskUpdatePayload(
			task({ identifier: 'PRJ-7', index: 7, created: '2026-01-01T00:00:00Z' }),
		);
		expect(payload).not.toHaveProperty('identifier');
		expect(payload).not.toHaveProperty('index');
		expect(payload).not.toHaveProperty('created');
	});
});
