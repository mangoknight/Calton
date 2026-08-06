import { describe, expect, it } from 'vitest';

import {
	evaluateBundleBudget,
	MAIN_CHUNK_GZIP_BUDGET,
	MAIN_CHUNK_GZIP_MEASURED,
	MAIN_CHUNK_PREFIX,
} from '../../scripts/bundle-budget.mjs';

/**
 * 闸门本身的单测。真实产物的检查在 `npm run build` 里由
 * `scripts/check-bundle-size.mjs` 做（那需要 dist，不适合放进 vitest）。
 */
describe('包体预算闸门', () => {
	it('主 chunk 在预算内时放行', () => {
		const result = evaluateBundleBudget([{ name: 'index-abc.js', gzipBytes: 100_000 }]);
		expect(result.ok).toBe(true);
	});

	it('★ 超出预算时拦下，并说清超了多少', () => {
		const result = evaluateBundleBudget([
			{ name: 'index-abc.js', gzipBytes: MAIN_CHUNK_GZIP_BUDGET + 1 },
		]);
		expect(result.ok).toBe(false);
		expect(result.reason).toBe('over-budget');
		expect(result.message).toContain('超出预算');
	});

	it('恰好等于预算时放行（边界取闭区间）', () => {
		const result = evaluateBundleBudget([
			{ name: 'index-abc.js', gzipBytes: MAIN_CHUNK_GZIP_BUDGET },
		]);
		expect(result.ok).toBe(true);
	});

	/**
	 * ★ 找不到主 chunk 必须**失败**而不是放行。
	 *
	 * 这是这类闸门最典型的失效方式：构建产物形状一变（改了入口名、开了
	 * manualChunks），匹配不上就"没有超预算"，于是闸门在**什么都没检查**的状态下常绿。
	 */
	it('★ 找不到主 chunk 时失败，而不是当作没超', () => {
		const result = evaluateBundleBudget([{ name: 'vendor-xyz.js', gzipBytes: 10 }]);
		expect(result.ok).toBe(false);
		expect(result.reason).toBe('main-chunk-not-found');
	});

	it('★ 产物为空时同样失败', () => {
		expect(evaluateBundleBudget([]).ok).toBe(false);
	});

	/** 只看主 chunk：懒加载出去的 chunk 再大也不该让闸门红。 */
	it('懒加载 chunk 超大不影响判定（只看主 chunk）', () => {
		const result = evaluateBundleBudget([
			{ name: 'index-abc.js', gzipBytes: 100_000 },
			{ name: 'DescriptionEditor-xyz.js', gzipBytes: 900_000 },
		]);
		expect(result.ok).toBe(true);
	});

	/**
	 * ★ 预算与实测值的关系要保持"有余量但不离谱"。
	 * 预算被随手调大到几倍时这条会红 —— 那等于把防线删掉。
	 */
	it('★ 预算相对实测值有余量，但不超过 +30%', () => {
		expect(MAIN_CHUNK_GZIP_BUDGET).toBeGreaterThan(MAIN_CHUNK_GZIP_MEASURED);
		expect(MAIN_CHUNK_GZIP_BUDGET).toBeLessThan(MAIN_CHUNK_GZIP_MEASURED * 1.3);
	});

	it('主 chunk 前缀与 Vite 的入口命名一致', () => {
		expect(MAIN_CHUNK_PREFIX).toBe('index-');
	});
});
