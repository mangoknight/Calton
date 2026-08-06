import type { Project } from '@/api/projects';

/**
 * ★ `parent_project_id` 的编码 —— **全项目唯一的编码点**。
 *
 * 线上口径已由 tester 拿 Go 参考服务实测定案：
 * 该字段**恒为 int，顶层 = 0，从不 null、从不缺键**。
 *
 * 更新时只有**两态**，不是三态：
 *   - **不传**（或传 null）→ 不改动父级。Go 侧是 `*int64`，两者都得到 nil 指针，更新时跳过。
 *   - **传数字**（含 0）    → 设为该值，`0` 就是顶层。
 * 所以"用户没碰控件"和"用户主动选空"发出去的效果一样，前端不需要区分，
 * 也就不需要 model_fields_set 那套三态判断。
 *
 * ⚠️ **这个字段是 AC-6「POST 全量替换」的显式例外**（后端 T16 单独写测试）。
 * 千万不要为了迎合全量替换而"总是回传当前的 parent_project_id" ——
 * 表面上没问题，实际会在并发编辑时用陈旧值覆盖掉别人刚做的移动操作。
 * 正因如此 keep 分支必须**主动把这个键从 payload 里删掉**：
 * 更新走的是 `{...project}` 全量回传，不删就等于把服务端读来的旧值又发了回去。
 */

export const PARENT_KEEP = 'keep';
/** 顶层就是 0（实测定案，不是猜测）。 */
export const TOP_LEVEL_PARENT_ID = 0;

/** 两态：不改，或设为某个具体值（0 = 顶层）。 */
export type ParentSelection = typeof PARENT_KEEP | number;

/** 表单下拉的值（字符串）→ 两态。 */
export function parseParentSelection(value: string): ParentSelection {
	return value === PARENT_KEEP ? PARENT_KEEP : Number(value);
}

/**
 * 把选择并进要提交的 payload。
 * keep 会**删掉** `parent_project_id` 键 —— 省略即"不改"，同时避开并发覆盖。
 */
export function applyParentSelection<T extends Record<string, unknown>>(
	payload: T,
	selection: ParentSelection,
): T {
	if (selection === PARENT_KEEP) {
		// 更新时 payload 是 {...project} 全量回传，这里不删就会把旧值发回去
		const { parent_project_id: _omitted, ...rest } = payload;
		// 删掉的是可选字段，对调用方的类型没有实质变化
		return rest as T;
	}

	return { ...payload, parent_project_id: selection };
}

/** 表单打开时下拉框的初始值：默认"不改"。 */
export function initialParentValue(): string {
	return PARENT_KEEP;
}

/** 编辑时可选的上级项目：排除自己（否则一键造出自环）。 */
export function selectableParents(candidates: Project[], self?: Project): Project[] {
	return candidates.filter((candidate) => candidate.id !== self?.id);
}
