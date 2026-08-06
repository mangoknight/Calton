import { describe, expect, it } from 'vitest';

/**
 * jsdom 排版 API 补丁的**前提断言**（见 `src/test/setup.ts`）。
 *
 * ## 为什么要单独一条测试，而不是"反正坏了退出码会变 1"
 *
 * 退出码确实会变 1 —— 但它给出的现象是 **65 条 prosemirror 内部的
 * "Unhandled Error"**，堆栈全在 `node_modules` 里，跟"谁把补丁删了"
 * 看不出任何关系。上一次它就是这么存在了很久：**用例全绿、退出码 1**，
 * 没人愿意去翻。
 *
 * 这条测试把同一个故障变成**一条指名道姓的红**：
 * "Range.getClientRects 不见了 ⇒ 去看 setup.ts 那段补丁"。
 * 成本一条断言，收益是排查从"翻 65 条堆栈"变成"读一行断言消息"。
 *
 * ## ⚠️ 它不保证坐标逻辑是对的
 *
 * 补出来的几何量全是零。这条测试断言的是**这些 API 存在且不抛**，
 * 不是"坐标算得对"。依赖真实坐标的行为在 jsdom 里测不了，
 * 真实覆盖在 F14（生产构建 E2E）。
 */
describe('jsdom 缺失的排版 API 已补上（prosemirror 依赖它们）', () => {
	it('★ Range.getClientRects 存在，且返回空列表而不是抛错', () => {
		const range = document.createRange();
		expect(typeof range.getClientRects).toBe('function');
		// jsdom 对 Element 的表达就是空列表，这里保持一致
		expect(range.getClientRects()).toHaveLength(0);
	});

	/**
	 * ★ `singleRect` 在 getClientRects 为空时会**回落到 getBoundingClientRect**，
	 * 所以它必须存在 —— 只补前一个的话，错误只是换个地方抛。
	 */
	it('★ Range.getBoundingClientRect 存在（getClientRects 为空时的回落路径）', () => {
		const range = document.createRange();
		expect(typeof range.getBoundingClientRect).toBe('function');
		expect(range.getBoundingClientRect().width).toBe(0);
	});

	/**
	 * ★ `posAtCoords` 对 `null` 有明确处理（调用方 mousedown 也有），
	 * 所以返回 null 是**上游自己写的回落分支**，不是我们改了它的行为。
	 */
	it('★ document.elementFromPoint 存在，且返回 null（没有排版就没有命中元素）', () => {
		expect(typeof document.elementFromPoint).toBe('function');
		expect(document.elementFromPoint(0, 0)).toBeNull();
	});
});
