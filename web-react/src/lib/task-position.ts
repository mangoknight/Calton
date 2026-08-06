/**
 * 拖拽落点的 position 计算（F07b）。
 *
 * v1 的 position 是 **float64**，按视图存。插入用"取前后中值"而不是整列重排 ——
 * 后者要为一次拖拽发 N 个请求，而中值只需要改动被拖的那一个。
 *
 * 默认间距是 `2^16`（`calculateDefaultPosition`，tasks.go:867-869：
 * 新任务的 position = index × 2^16），留足了对半分的空间。
 */

/** 与后端 `calculateDefaultPosition` 一致：相邻任务默认差 2^16。 */
export const POSITION_STEP = 2 ** 16;

/**
 * 后端的 `MinPositionSpacing`（task_position.go:36）。
 *
 * ⚠️ 判据是**绝对值**不是间距：`tp.Position < MinPositionSpacing` 成立时，
 * 后端会锁住该视图并**重算整个视图的所有 position**（task_position.go:191-202），
 * 于是**实际存下来的值和你发过去的不一样**。
 * 前端不需要（也无法）复现这套重算，但必须知道"成功 ≠ 我发的值生效"。
 */
export const MIN_POSITION_SPACING = 0.01;

/**
 * 落在 `before` 与 `after` 之间时应该取的 position。
 *
 * - 两侧都有 → 中值
 * - 只有下方邻居（拖到最顶上）→ 邻居的一半
 * - 只有上方邻居（拖到最底下）→ 邻居 + 一个默认间距
 * - 一个都没有（空列）→ 一个默认间距
 *
 * `before` 指落点**上方**那张卡的 position，`after` 指**下方**那张。
 */
export function positionBetween(before?: number, after?: number): number {
	const hasBefore = typeof before === 'number' && Number.isFinite(before);
	const hasAfter = typeof after === 'number' && Number.isFinite(after);

	if (hasBefore && hasAfter) return (before + after) / 2;
	if (hasAfter) return after / 2;
	if (hasBefore) return before + POSITION_STEP;
	return POSITION_STEP;
}

/**
 * 这次插入是否会触发后端重算整个视图。
 *
 * 用途**不是**去规避它 —— 重算是后端正常且正确的行为，前端拦不住也不该拦。
 * 用途是让调用方知道"这次成功之后本地那个 position 已经不可信了，必须重取"。
 */
export function willTriggerRecalculation(position: number): boolean {
	return position < MIN_POSITION_SPACING;
}

export interface Positioned {
	id: number;
	position?: number;
}

/**
 * 在一个已按 position 升序排列的列表里，把某张卡插到 `targetIndex` 位置时的 position。
 *
 * `movingId` 会先从列表里剔除再算邻居 —— 不剔除的话，在同一列内往下拖时
 * 会把"自己"当成上邻居，算出来的中值落在自己和下一张之间，视觉上等于没动。
 */
export function positionForInsert(
	items: readonly Positioned[],
	movingId: number,
	targetIndex: number,
): number {
	const others = items.filter((item) => item.id !== movingId);
	const clamped = Math.max(0, Math.min(targetIndex, others.length));

	return positionBetween(others[clamped - 1]?.position, others[clamped]?.position);
}
