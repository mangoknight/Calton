import type { Task } from '@/api/tasks';
import { formatApiDate } from '@/lib/datetime';
import type { SortableTaskField } from '@/lib/table-sort';

/**
 * Table 视图的列定义（F06）。
 *
 * `sortField` 是**后端字段名**，不是列 id —— 两者刻意分开：
 * 「指派人」这类列在后端没有对应的可排序字段，`sortField` 留空即表示列头不可点。
 * 把两者合成一个字段的话，加一列就得先想清楚它能不能排序，很容易顺手写上一个
 * 后端不认的名字，然后在用户点击时才炸。
 */

export interface TaskColumn {
	id: string;
	/**
	 * 列名的 **i18n key**，不是列名本身。
	 *
	 * ⚠️ 这张表是模块级常量、**只算一次** —— 存文字的话切语言时表头不会跟着变，
	 * 而且只有表头不变，正是"大部分翻了、个别没翻"那种最难察觉的形状
	 * （与 `Sidebar.tsx` 的 NAV 表、zod schema 的校验消息同一个坑）。
	 * 翻译发生在渲染时，见 `TableView.tsx`。
	 */
	labelKey: string;
	/** 有值才可排序；必须是 SORTABLE_TASK_FIELDS 里的字段（类型已约束）。 */
	sortField?: SortableTaskField;
	/** 默认是否显示。列多了默认全开会挤，次要列默认关。 */
	defaultVisible: boolean;
	/** 数字/日期右对齐，读起来才对得上位。 */
	align?: 'right';
	render: (task: Task) => string;
}

const PRIORITY_LABELS: Record<number, string> = {
	1: '低',
	2: '中',
	3: '高',
	4: '紧急',
	5: '马上做',
};

/** 所有单元格都返回字符串，空值统一渲染成 "—"（而不是空白，空白分不清是没值还是没渲染）。 */
export const EMPTY_CELL = '—';

function orEmpty(value: string | null | undefined): string {
	return value === null || value === undefined || value === '' ? EMPTY_CELL : value;
}

export const TASK_COLUMNS: readonly TaskColumn[] = [
	{
		id: 'index',
		// ⚠️ 上游**没有**这一列的 key（实测 `task.attributes.index` 在 en.json 里不存在）。
		// `t()` 找不到 key 时原样返回它，所以这里直接放中文原文即可 ——
		// 若真写成 `task.attributes.index`，界面上会显示这串 key 本身。
		labelKey: '编号',
		sortField: 'index',
		defaultVisible: true,
		render: (task) => orEmpty(task.identifier || (task.index ? `#${task.index}` : null)),
	},
	{
		id: 'title',
		labelKey: 'task.attributes.title',
		sortField: 'title',
		defaultVisible: true,
		render: (task) => task.title,
	},
	{
		id: 'done',
		labelKey: 'task.attributes.done',
		sortField: 'done',
		defaultVisible: true,
		render: (task) => (task.done ? '已完成' : '进行中'),
	},
	{
		id: 'priority',
		labelKey: 'task.attributes.priority',
		sortField: 'priority',
		defaultVisible: true,
		// 0 表示"未设置"，不是"优先级为 0"
		render: (task) => orEmpty(task.priority ? PRIORITY_LABELS[task.priority] : null),
	},
	{
		id: 'due_date',
		labelKey: 'task.attributes.dueDate',
		sortField: 'due_date',
		defaultVisible: true,
		// ⚠️ 零值是 "0001-01-01T00:00:00Z"，formatApiDate 会返回 null
		render: (task) => orEmpty(formatApiDate(task.due_date)),
	},
	{
		id: 'start_date',
		labelKey: 'task.attributes.startDate',
		sortField: 'start_date',
		defaultVisible: false,
		render: (task) => orEmpty(formatApiDate(task.start_date)),
	},
	{
		id: 'end_date',
		labelKey: 'task.attributes.endDate',
		sortField: 'end_date',
		defaultVisible: false,
		render: (task) => orEmpty(formatApiDate(task.end_date)),
	},
	{
		id: 'percent_done',
		labelKey: 'task.attributes.percentDone',
		sortField: 'percent_done',
		defaultVisible: false,
		align: 'right',
		// percent_done 是 0..1 的小数，不是百分数
		render: (task) => `${Math.round((task.percent_done ?? 0) * 100)}%`,
	},
	{
		id: 'labels',
		labelKey: 'task.attributes.labels',
		// 后端不支持按标签排序（labels 不在 validateTaskFieldForSorting 里）
		defaultVisible: false,
		render: (task) => orEmpty(task.labels?.map((label) => label.title).join('、')),
	},
	{
		id: 'assignees',
		labelKey: 'task.attributes.assignees',
		// 同上，assignees 不可排序
		defaultVisible: false,
		render: (task) => orEmpty(task.assignees?.map((u) => u.name || u.username).join('、')),
	},
	{
		id: 'updated',
		labelKey: 'task.attributes.updated',
		sortField: 'updated',
		defaultVisible: false,
		render: (task) => orEmpty(formatApiDate(task.updated)),
	},
];

export const DEFAULT_VISIBLE_COLUMNS = TASK_COLUMNS.filter((c) => c.defaultVisible).map(
	(c) => c.id,
);

/**
 * 列显示状态存 localStorage：它是个人偏好，放 URL 会让分享出去的链接
 * 把自己的列设置强加给别人。key 不带 view id —— 用户想要的是"我的表格长这样"，
 * 而不是每个项目各记一份。
 */
export const COLUMNS_STORAGE_KEY = 'calton.table.columns';

/**
 * 读回来的列表要**过滤掉已不存在的列 id**：列定义会随版本变，
 * 存着旧 id 的浏览器不该因此渲染出空列或崩溃。
 * 全部失效时退回默认集合，不要退回空表格。
 */
export function loadVisibleColumns(storage: Pick<Storage, 'getItem'>): string[] {
	let raw: string | null = null;
	try {
		raw = storage.getItem(COLUMNS_STORAGE_KEY);
	} catch {
		// 隐私模式下 localStorage 可能直接抛，列设置不值得为此让页面挂掉
		return DEFAULT_VISIBLE_COLUMNS;
	}
	if (!raw) return DEFAULT_VISIBLE_COLUMNS;

	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch {
		return DEFAULT_VISIBLE_COLUMNS;
	}

	if (!Array.isArray(parsed)) return DEFAULT_VISIBLE_COLUMNS;

	const known = new Set(TASK_COLUMNS.map((c) => c.id));
	const valid = parsed.filter((id): id is string => typeof id === 'string' && known.has(id));

	return valid.length > 0 ? valid : DEFAULT_VISIBLE_COLUMNS;
}

export function saveVisibleColumns(storage: Pick<Storage, 'setItem'>, ids: string[]): void {
	try {
		storage.setItem(COLUMNS_STORAGE_KEY, JSON.stringify(ids));
	} catch {
		// 存不下就算了，本次会话内的状态仍然有效
	}
}
