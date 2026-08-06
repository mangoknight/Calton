import { describe, expect, it } from 'vitest';

import {
	CONTRACT_DIR_REL,
	CORRECTED_CONTRACT_FILENAME,
	CORRECTED_MARKER,
	evaluateContractSource,
	RAW_CONTRACT_FILENAME,
} from '../../scripts/contract-source.mjs';

/**
 * 锁住**前端侧**的契约来源常量。
 *
 * 作用范围要说清楚：这里只断言前端自己的常量值，**并不能**真正校验后端用的是同一个
 * 文件名 —— 后端那棵树现在不在本仓库的测试范围内。它起的是"改名必须经过一次人的确认"
 * 的作用：任何人改了文件名/标记，都会撞到这几条断言，从而被迫去确认后端是否同步。
 * 真正的跨侧断言等 CI 里两棵树共存时再补。
 *
 * 为什么值得锁：两边一旦分叉，前端类型从一份契约生成、契约门禁校验另一份，
 * 两者各自漂移，而且 tsc 全绿、没有任何红灯。
 */
describe('契约来源常量（前端侧）', () => {
	it('生成脚本用的是修正版契约', () => {
		expect(CORRECTED_CONTRACT_FILENAME).toBe('calton-v1-corrected.json');
	});

	it('原始冻结版只作降级候选，不是默认来源', () => {
		expect(RAW_CONTRACT_FILENAME).toBe('calton-v1-swagger.json');
		expect(RAW_CONTRACT_FILENAME).not.toBe(CORRECTED_CONTRACT_FILENAME);
	});

	it('契约目录在 server/ 下（不是仓库根的 contract/）', () => {
		expect(CONTRACT_DIR_REL).toBe('server/contract');
	});

	/**
	 * 修正版标记与后端契约测试校验的是同一个事实
	 * （server/tests/contract/test_contract.py 断言 info.version 以此结尾）。
	 * 判定以标记为准而非文件名：文件名会被写错/被换/被重命名，标记跟着内容走。
	 */
	it('修正版标记与后端契约测试一致', () => {
		expect(CORRECTED_MARKER).toBe('-corrected');
	});
});

/**
 * 回退闸门的常驻回归测试。
 *
 * 此前这四个场景只有我手工跑过 —— 一条"没有测试的防线"，正是我要求别人补的那种。
 * 判定逻辑抽成纯函数后就能常驻了。
 */
describe('契约回退闸门', () => {
	const corrected = `0.24.0${CORRECTED_MARKER}`;

	it('修正版契约：放行', () => {
		expect(
			evaluateContractSource({
				infoVersion: corrected,
				contractDirExists: true,
				allowFallback: false,
			}),
		).toEqual({ isCorrected: true, ok: true, reason: null });
	});

	it('★ 契约目录存在但契约没有修正版标记：硬失败', () => {
		// 就是"文件名对、内容却是未修正版"那种情况——我自己踩过
		expect(
			evaluateContractSource({
				infoVersion: '0.24.0',
				contractDirExists: true,
				allowFallback: false,
			}),
		).toMatchObject({ isCorrected: false, ok: false });
	});

	it('显式 ALLOW_SWAGGER_FALLBACK=1：放行但标记原因', () => {
		expect(
			evaluateContractSource({
				infoVersion: '0.24.0',
				contractDirExists: true,
				allowFallback: true,
			}),
		).toMatchObject({ ok: true, reason: 'explicitly-allowed' });
	});

	it('契约目录还不存在（B 线未落盘）：只警告不失败', () => {
		expect(
			evaluateContractSource({
				infoVersion: '0.24.0',
				contractDirExists: false,
				allowFallback: false,
			}),
		).toMatchObject({ isCorrected: false, ok: true });
	});

	it.each([undefined, null, 123, '', '0.24.0-CORRECTED'])(
		'info.version 为 %s 时不算修正版',
		(version) => {
			expect(
				evaluateContractSource({
					infoVersion: version,
					contractDirExists: false,
					allowFallback: false,
				}).isCorrected,
			).toBe(false);
		},
	);
});
