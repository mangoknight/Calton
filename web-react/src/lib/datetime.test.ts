import { describe, expect, it } from 'vitest';

import { formatApiDate, isZeroTime, parseApiTime, toApiTime, ZERO_TIME } from './datetime';

describe('v1 零值时间', () => {
	it.each([ZERO_TIME, '0001-01-01T00:00:00+00:00', '', null, undefined])('%s 判为零值', (value) => {
		expect(isZeroTime(value)).toBe(true);
		expect(parseApiTime(value)).toBeNull();
	});

	it('真实时间正常解析', () => {
		const date = parseApiTime('2026-08-03T10:00:00Z');
		expect(date).toBeInstanceOf(Date);
		expect(date?.toISOString()).toBe('2026-08-03T10:00:00.000Z');
	});

	it('无法解析的字符串返回 null 而不是抛错（UI 要能降级渲染）', () => {
		expect(parseApiTime('not-a-date')).toBeNull();
	});

	it('写回时 null → 零值字符串，后端不接受 null', () => {
		expect(toApiTime(null)).toBe(ZERO_TIME);
		expect(toApiTime(undefined)).toBe(ZERO_TIME);
	});

	// 对齐 Go 的 RFC3339Nano：尾随零裁掉，整秒时连小数点一起去掉。
	// 不裁的话读回写回的往返比对会因为多出的 .000 一直判为有改动。
	it.each([
		['2026-08-03T10:30:00Z', '2026-08-03T10:30:00Z'],
		['2026-08-03T10:30:00.120Z', '2026-08-03T10:30:00.12Z'],
		['2026-08-03T10:30:00.100Z', '2026-08-03T10:30:00.1Z'],
		['2026-08-03T10:30:00.123Z', '2026-08-03T10:30:00.123Z'],
	])('%s 写回为 %s', (input, expected) => {
		expect(toApiTime(new Date(input))).toBe(expected);
	});

	it('往返一致：读回来再写回去，字符串与原文逐字相同', () => {
		for (const original of ['2026-08-03T10:30:00Z', '2026-08-03T10:30:00.5Z']) {
			expect(toApiTime(parseApiTime(original))).toBe(original);
		}
	});
});

describe('formatApiDate（列表展示用）', () => {
	it.each([ZERO_TIME, '', null, undefined, 'not-a-date'])(
		'%s → null（调用方据此不渲染）',
		(value) => {
			expect(formatApiDate(value)).toBeNull();
		},
	);

	it('按本地时区补零输出 YYYY-MM-DD', () => {
		const date = new Date('2026-08-03T12:00:00Z');
		const pad = (n: number) => String(n).padStart(2, '0');
		const expected = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
		expect(formatApiDate('2026-08-03T12:00:00Z')).toBe(expected);
		// 补零必须真发生：一位数的月/日不能输出成 "2026-1-5"
		expect(formatApiDate('2026-01-05T12:00:00Z')).toMatch(/^\d{4}-\d{2}-\d{2}$/);
	});
});
