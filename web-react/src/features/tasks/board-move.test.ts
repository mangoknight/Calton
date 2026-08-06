import { describe, expect, it } from 'vitest';

import type { Bucket } from '@/api/buckets';
import type { Paginated } from '@/api/pagination';
import { POSITION_STEP } from '@/lib/task-position';
import { applyTaskMove, bucketDropId, resolveDrop, taskDragId } from './board-move';

function bucket(id: number, tasks: { id: number; position: number }[]): Bucket {
	return {
		id,
		title: `列 ${id}`,
		project_view_id: 4,
		count: tasks.length,
		limit: 0,
		tasks: tasks.map((t) => ({ ...t, title: `任务 ${t.id}` })),
	};
}

/** 列 1：任务 1(100)、任务 2(200)｜列 2：任务 3(300)｜列 3：空 */
function board(): Paginated<Bucket> {
	return {
		items: [
			bucket(1, [
				{ id: 1, position: 100 },
				{ id: 2, position: 200 },
			]),
			bucket(2, [{ id: 3, position: 300 }]),
			bucket(3, []),
		],
		resultCount: 3,
		totalPages: 1,
	};
}

describe('拖拽 id 前缀', () => {
	/** 任务和桶的数字 id 会撞车，dnd-kit 的 id 必须全局唯一。 */
	it('★ 同一个数字的任务与桶产生不同的拖拽 id', () => {
		expect(taskDragId(5)).not.toBe(bucketDropId(5));
	});
});

describe('resolveDrop', () => {
	it('落在另一列的卡片上：插到那张卡之前', () => {
		const move = resolveDrop(board().items, taskDragId(1), taskDragId(3));
		expect(move).toEqual({
			taskId: 1,
			fromBucketId: 1,
			toBucketId: 2,
			// 目标列只有 300 一张，插到它之前 → 300/2
			position: 150,
		});
	});

	it('落在空列上：放到末尾，用默认间距', () => {
		const move = resolveDrop(board().items, taskDragId(1), bucketDropId(3));
		expect(move).toEqual({
			taskId: 1,
			fromBucketId: 1,
			toBucketId: 3,
			position: POSITION_STEP,
		});
	});

	it('落在有卡的列的空白处：放到该列末尾', () => {
		const move = resolveDrop(board().items, taskDragId(1), bucketDropId(2));
		expect(move?.position).toBe(300 + POSITION_STEP);
		expect(move?.toBucketId).toBe(2);
	});

	/**
	 * ★ "落在卡片上 = 插到它之前"。任务 1 本来就紧邻在任务 2 之前，
	 * 所以这一下没有实际移动，不该发请求。
	 *
	 * 这条曾经真红过：下标原本在**未剔除自己**的数组里算，而 positionForInsert
	 * 会自行剔除，两个坐标系差一位，于是同列拖动会插错位置。
	 */
	it('★ 同列内把第一张拖到紧邻的第二张上：没有实际移动，不发请求', () => {
		expect(resolveDrop(board().items, taskDragId(1), taskDragId(2))).toBeNull();
	});

	/** 同列内真的换位（拖到更后面的卡上/列尾）才发请求。 */
	it('同列内拖到列尾时确实产生移动', () => {
		const move = resolveDrop(board().items, taskDragId(1), bucketDropId(1));
		expect(move).toMatchObject({ taskId: 1, fromBucketId: 1, toBucketId: 1 });
		// 剔除自己后列 1 只剩 200，落到末尾 → 200 + 默认间距
		expect(move?.position).toBe(200 + POSITION_STEP);
	});

	/** ★ 跨列落在某张卡上时，下标同样要在剔除自己之后的数组里算。 */
	it('★ 跨列落在第二张卡上：插到它之前', () => {
		const three = [
			bucket(1, [{ id: 1, position: 100 }]),
			bucket(2, [
				{ id: 3, position: 300 },
				{ id: 4, position: 400 },
			]),
		];
		const move = resolveDrop(three, taskDragId(1), taskDragId(4));
		expect(move?.position).toBe(350);
	});

	it('落回自己身上不产生请求', () => {
		expect(resolveDrop(board().items, taskDragId(1), taskDragId(1))).toBeNull();
	});

	it.each([null, undefined])('over 为 %s（拖到界外松手）不产生请求', (overId) => {
		expect(resolveDrop(board().items, taskDragId(1), overId)).toBeNull();
	});

	it('未知的 id 前缀不产生请求', () => {
		expect(resolveDrop(board().items, 'whatever:1', taskDragId(3))).toBeNull();
		expect(resolveDrop(board().items, taskDragId(1), 'whatever:2')).toBeNull();
	});

	it('拖的任务不在板面上（数据不同步）时不产生请求', () => {
		expect(resolveDrop(board().items, taskDragId(999), bucketDropId(2))).toBeNull();
	});
});

describe('applyTaskMove（本地乐观改板）', () => {
	it('跨列移动：卡片搬家，两列的 count 各自增减', () => {
		const next = applyTaskMove(board(), {
			taskId: 1,
			fromBucketId: 1,
			toBucketId: 2,
			position: 150,
		});

		const from = next.items.find((b) => b.id === 1)!;
		const to = next.items.find((b) => b.id === 2)!;

		expect(from.tasks?.map((t) => t.id)).toEqual([2]);
		expect(from.count).toBe(1);
		expect(to.tasks?.map((t) => t.id)).toEqual([1, 3]);
		expect(to.count).toBe(2);
	});

	/** ★ 落进目标列后要按 position 重排，否则卡片会出现在末尾而不是落点。 */
	it('★ 目标列按 position 重排，不是简单追加到末尾', () => {
		const next = applyTaskMove(board(), {
			taskId: 1,
			fromBucketId: 1,
			toBucketId: 2,
			position: 150, // 小于列 2 里的 300
		});

		const to = next.items.find((b) => b.id === 2)!;
		expect(to.tasks?.map((t) => t.id)).toEqual([1, 3]);
		expect(to.tasks?.[0]?.position).toBe(150);
	});

	/** ★ 同列重排不能把 count 加一遍（from 与 to 是同一个桶）。 */
	it('★ 同列重排时 count 不变', () => {
		const next = applyTaskMove(board(), {
			taskId: 1,
			fromBucketId: 1,
			toBucketId: 1,
			position: 250,
		});

		const target = next.items.find((b) => b.id === 1)!;
		expect(target.count).toBe(2);
		expect(target.tasks?.map((t) => t.id)).toEqual([2, 1]);
	});

	it('不修改传入的 board（回滚要靠这份快照）', () => {
		const original = board();
		applyTaskMove(original, { taskId: 1, fromBucketId: 1, toBucketId: 2, position: 150 });

		expect(original.items[0]!.tasks?.map((t) => t.id)).toEqual([1, 2]);
		expect(original.items[0]!.count).toBe(2);
	});

	it('任务不在板面上时原样返回', () => {
		const original = board();
		expect(
			applyTaskMove(original, {
				taskId: 999,
				fromBucketId: 1,
				toBucketId: 2,
				position: 1,
			}),
		).toBe(original);
	});

	it('count 不会被减成负数', () => {
		const weird: Paginated<Bucket> = {
			items: [{ ...bucket(1, [{ id: 1, position: 100 }]), count: 0 }, bucket(2, [])],
			resultCount: 2,
			totalPages: 1,
		};
		const next = applyTaskMove(weird, {
			taskId: 1,
			fromBucketId: 1,
			toBucketId: 2,
			position: 1,
		});
		expect(next.items[0]!.count).toBe(0);
	});
});
