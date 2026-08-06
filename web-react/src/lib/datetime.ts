/**
 * v1 的时间零值是 "0001-01-01T00:00:00Z"，**不是 null** —— Go 的 time.Time 零值序列化结果。
 * 直接丢给 new Date() 会得到一个合法但荒谬的日期（公元 1 年），
 * 于是 UI 上到期日会显示成 "0001-01-01"，排序也会把它排到最前面。
 * 所有从 API 读时间的地方都要先过 isZeroTime / parseApiTime。
 */

export const ZERO_TIME = '0001-01-01T00:00:00Z';

export function isZeroTime(value: string | null | undefined): boolean {
	if (value === null || value === undefined || value === '') return true;
	// 零值在不同序列化路径下可能带偏移（如 0001-01-01T00:00:00+00:00），按年份判定更稳
	return value.startsWith('0001-01-01T');
}

/** 零值/空值 → null；否则返回 Date。无法解析的字符串也返回 null（不抛，UI 要能降级渲染）。 */
export function parseApiTime(value: string | null | undefined): Date | null {
	if (isZeroTime(value)) return null;
	const date = new Date(value as string);
	return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * 列表里展示日期：零值/空值 → null（**调用方据此决定不渲染**，不要渲染成 "-" 之外的东西）。
 *
 * 手工拼而不用 toLocaleDateString：后者的输出随运行环境的 locale 变，
 * 单测在 CI 与本地会得到不同字符串。日期按**本地时区**取，因为到期日是给人看的。
 */
export function formatApiDate(value: string | null | undefined): string | null {
	const date = parseApiTime(value);
	if (!date) return null;
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * 写回 API：null → 零值字符串（后端不接受 null）。
 *
 * 格式对齐 Go 的 RFC3339Nano —— 它会**裁掉小数秒的尾随零**，整秒时连小数点一起去掉：
 *   Go:  2026-08-03T10:30:00Z
 *   JS:  2026-08-03T10:30:00.000Z   ← toISOString() 固定三位
 * 不裁的话，读回来再写回去的往返比对会因为这三位一直判为"有改动"。
 */
export function toApiTime(date: Date | null | undefined): string {
	if (!date) return ZERO_TIME;

	return date
		.toISOString()
		.replace(/\.(\d*[1-9])0*Z$/, '.$1Z') // .120Z → .12Z
		.replace(/\.0+Z$/, 'Z'); // .000Z → Z
}
