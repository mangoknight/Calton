/**
 * API 与 `<input type="color">` 之间的颜色格式转换。
 *
 * 两边差一个 `#`，且这个差异是**单向静默**的：
 *   API 存的是 `"e8e8e8"`（不带 `#`，tester 实测）
 *   `<input type="color">` 收发的是 `"#e8e8e8"`（带 `#`，HTML 规范强制）
 *
 * 把带 `#` 的值原样发给后端不会报错 —— 后端照单全收存进去，
 * 然后这个标签在所有按 `#${hex_color}` 拼色的地方渲染成 `##e8e8e8`（无效颜色，静默变黑/透明）。
 * 所以两个方向都必须显式转换，不能靠"反正后端不校验"糊过去。
 */

/**
 * `<input type="color">` 的值没有"空"这一档：规范要求它必须是合法的 `#rrggbb`，
 * 给空串浏览器会自己回落到 `#000000`。
 *
 * ⚠️ 所以**不能**拿输入框的当前值反推"用户有没有设过颜色" ——
 * 那会让"没有颜色"的标签在用户只改了标题时被写上黑色。
 * 空值状态必须由外部单独保存（见 `LabelFormDialog` 里的 `hexColor` 状态），
 * 这里只负责在显示层给一个中性的回落色。
 */
export const COLOR_INPUT_FALLBACK = '#cccccc';

/** `<input type="color">` 的值 → API 格式：去掉前导 `#`。 */
export function toApiHexColor(inputValue: string): string {
	return inputValue.replace(/^#/, '');
}

/**
 * API 格式 → `<input type="color">` 的值：补上 `#`。
 *
 * 空值（未设置颜色）回落到中性灰**仅用于显示**，调用方不得把这个回落值当作用户的选择写回。
 */
export function toColorInputValue(apiValue: string | null | undefined): string {
	if (!apiValue) return COLOR_INPUT_FALLBACK;
	return apiValue.startsWith('#') ? apiValue : `#${apiValue}`;
}

/** 有没有设过颜色。空串 / null / undefined 都算没设 —— 后端对"没颜色"给的是空串。 */
export function hasColor(apiValue: string | null | undefined): boolean {
	return Boolean(apiValue);
}

/** 渲染色块用的 CSS 颜色值；没设颜色时返回 null，调用方据此不渲染色块。 */
export function toCssColor(apiValue: string | null | undefined): string | null {
	return hasColor(apiValue) ? toColorInputValue(apiValue) : null;
}
