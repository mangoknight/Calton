/**
 * 多列排序状态（F06）。
 *
 * ## 为什么单独一个模块
 *
 * v1 的排序是 `sort_by` 与 `order_by` 两个**可重复**参数，
 * 它们**按下标配对，不是按名字**（`task_collection.go:117-126`：
 * 取 `tf.OrderBy[i]` 与 `tf.SortBy[i]` 对齐，`OrderBy` 短了就补 asc）。
 * 也就是说这两个数组的**顺序本身携带语义** —— 一旦哪个环节把它们各自排序、
 * 去重、或用 Set/对象存一遍，配对就错位了，而且表现出来只是"排序结果不对"，
 * 没有任何报错。所以状态从头到尾用**有序数组**表示，序列化/反序列化都保序。
 *
 * ## URL 表示
 *
 * `?sort=due_date:asc,priority:desc` —— 点击顺序即数组顺序，可分享可回退。
 */

export type SortDirection = 'asc' | 'desc';

export interface SortSpec {
	field: string;
	direction: SortDirection;
}

/**
 * 后端允许排序的字段（`validateTaskFieldForSorting`，task_collection_sort.go:97-119）。
 * 传不在表里的字段后端会报错，所以列头能不能点由这张表决定，而不是由"有没有这一列"决定。
 *
 * ⚠️ 不含 `relevance`：它只在带 `s` 搜索词时有意义（其余情况后端静默忽略），
 * 由搜索功能自己接，不进列头。
 */
export const SORTABLE_TASK_FIELDS = [
	'id',
	'title',
	'description',
	'done',
	'done_at',
	'due_date',
	'created_by_id',
	'project_id',
	'repeat_after',
	'priority',
	'start_date',
	'end_date',
	'hex_color',
	'percent_done',
	'uid',
	'created',
	'updated',
	'position',
	'bucket_id',
	'index',
] as const;

export type SortableTaskField = (typeof SORTABLE_TASK_FIELDS)[number];

export function isSortableField(field: string): field is SortableTaskField {
	return (SORTABLE_TASK_FIELDS as readonly string[]).includes(field);
}

/**
 * 解析 `?sort=`。非法片段**整条丢掉**而不是纠正成默认值：
 * 保留一半的排序会让用户看到一个他没要过的顺序，还以为是数据问题。
 */
export function parseSortParam(raw: string | null | undefined): SortSpec[] {
	if (!raw) return [];

	const specs: SortSpec[] = [];
	const seen = new Set<string>();

	for (const chunk of raw.split(',')) {
		const [field = '', direction = 'asc'] = chunk.split(':');
		if (!isSortableField(field)) continue;
		if (direction !== 'asc' && direction !== 'desc') continue;
		// 同一字段出现两次时只认第一次：后端会按下标各排一遍，重复字段是无意义的开销
		if (seen.has(field)) continue;
		seen.add(field);
		specs.push({ field, direction });
	}

	return specs;
}

export function serializeSortParam(specs: SortSpec[]): string {
	return specs.map((spec) => `${spec.field}:${spec.direction}`).join(',');
}

/**
 * 点击列头的三态循环：未排序 → asc → desc → 移出排序。
 *
 * **新字段追加到末尾**，这正是"点两列排序时顺序正确"的含义 ——
 * 先点的列是主序，后点的列是次序。插到开头会让用户的第二次点击悄悄夺走主序。
 */
export function toggleSort(specs: SortSpec[], field: string): SortSpec[] {
	const index = specs.findIndex((spec) => spec.field === field);

	if (index === -1) return [...specs, { field, direction: 'asc' }];

	const current = specs[index]!;
	if (current.direction === 'asc') {
		const next = [...specs];
		next[index] = { field, direction: 'desc' };
		return next;
	}

	return specs.filter((spec) => spec.field !== field);
}

/**
 * 转成请求参数。两个数组**等长且同序** —— 后端按下标取，
 * 长度不等时它会给短的那侧补 asc，于是错位不会报错，只会静默排错。
 */
export function toSortQuery(specs: SortSpec[]): {
	sort_by: string[];
	order_by: SortDirection[];
} {
	return {
		sort_by: specs.map((spec) => spec.field),
		order_by: specs.map((spec) => spec.direction),
	};
}
