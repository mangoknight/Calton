import { describe, expect, it } from 'vitest';

import {
	COLOR_INPUT_FALLBACK,
	hasColor,
	toApiHexColor,
	toColorInputValue,
	toCssColor,
} from './hex-color';

/**
 * ⚠️ 本文件的测试数据**必须带 `#`**。
 *
 * `toApiHexColor('e8e8e8')` 是个**不动点** —— 不带 `#` 的输入在"去掉 `#`"这个变换下
 * 原样返回，所以拿它做样本的话，把整个 `replace` 删掉测试照样绿。
 * 真正有判别力的样本只有 `'#e8e8e8'` 这种。下面显式区分了这两类。
 */
describe('toApiHexColor', () => {
	it('★ 去掉 <input type="color"> 带的前导 #', () => {
		// 判别性样本：带 # 的输入在该变换下不是不动点，删掉实现这条会红
		expect(toApiHexColor('#e8e8e8')).toBe('e8e8e8');
	});

	it('已经是 API 格式时原样返回（幂等；⚠️ 这条抓不到"忘了去 #"）', () => {
		// 不动点样本：只能防"多去了一层"，不能防"没去"。留着是为了钉住幂等性，
		// 并在注释里标明它不承重，免得后来者误以为有它就够了。
		expect(toApiHexColor('e8e8e8')).toBe('e8e8e8');
	});

	it('只去掉一个 # —— 不做"清理到底"的猜测', () => {
		expect(toApiHexColor('##e8e8e8')).toBe('#e8e8e8');
	});
});

describe('toColorInputValue', () => {
	it('★ 给 API 格式补上 #（否则 <input type="color"> 拒收，静默显示黑色）', () => {
		expect(toColorInputValue('e8e8e8')).toBe('#e8e8e8');
	});

	it('空值回落到中性灰而不是黑色', () => {
		// 回落成 #000000 会与"用户真的选了黑色"无法区分
		expect(toColorInputValue('')).toBe(COLOR_INPUT_FALLBACK);
		expect(toColorInputValue(null)).toBe(COLOR_INPUT_FALLBACK);
		expect(toColorInputValue(undefined)).toBe(COLOR_INPUT_FALLBACK);
	});
});

describe('往返', () => {
	it('★ API → 输入框 → API 不变形', () => {
		expect(toApiHexColor(toColorInputValue('ff8400'))).toBe('ff8400');
	});
});

describe('hasColor / toCssColor', () => {
	it('空串算"没设颜色" —— 后端对未设置给的就是空串而不是 null', () => {
		expect(hasColor('')).toBe(false);
		expect(hasColor(null)).toBe(false);
		expect(hasColor('e8e8e8')).toBe(true);
	});

	it('★ 没颜色时返回 null，让调用方不渲染色块（而不是渲染一个灰点冒充）', () => {
		expect(toCssColor('')).toBeNull();
		expect(toCssColor('e8e8e8')).toBe('#e8e8e8');
	});
});
