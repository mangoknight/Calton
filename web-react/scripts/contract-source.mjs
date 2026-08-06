/**
 * 契约文件名的**单一事实来源**。
 *
 * ⚠️ 这个文件名必须与后端契约测试使用的文件名保持一致。两边分叉的后果是隐性的：
 * 前端类型从一份契约生成、契约门禁校验另一份，两者各自漂移且 tsc 全绿、没有红灯。
 * 改名时**两边一起改**，`src/api/contract-source.test.ts` 会把这条耦合钉住。
 *
 * 为什么必须是 corrected 而不是 raw（tester 实测的 operation 差异）：
 *   corrected 比 raw 多：GET /projects/{project}/tasks、GET /token/test、POST /labels/{label}
 *   corrected 比 raw 少：PUT /labels/{id}  ← 服务端根本没有这个路由
 */

/** 修正版契约文件名 —— 唯一可信的生成来源。 */
export const CORRECTED_CONTRACT_FILENAME = 'calton-v1-corrected.json';

/** 冻结的原始契约文件名（已知有三处标注错误，仅作降级候选）。 */
export const RAW_CONTRACT_FILENAME = 'calton-v1-swagger.json';

/** 契约目录，相对仓库根。 */
export const CONTRACT_DIR_REL = 'server/contract';

/**
 * ★ 修正版契约的**自我标记**：`info.version` 以此结尾。
 *
 * 判定"是不是修正版"以这个标记为准，**不看文件名** —— 后端契约测试
 * server/tests/contract/test_contract.py 校验的是同一个事实，两边对齐。
 * 文件名可以被写错、被换、被重命名，标记跟着内容走。
 */
export const CORRECTED_MARKER = '-corrected';

/**
 * 回退闸门的**纯逻辑**，与文件系统解耦，便于常驻回归测试。
 *
 * 判定"是不是修正版"以契约自带标记为准（info.version 以 CORRECTED_MARKER 结尾），
 * 不看文件名 —— 文件名可以被写错/被换/被重命名，标记跟着内容走。
 *
 * @param {{infoVersion: unknown, contractDirExists: boolean, allowFallback: boolean}} input
 * @returns {{isCorrected: boolean, ok: boolean, reason: string|null}}
 */
export function evaluateContractSource({ infoVersion, contractDirExists, allowFallback }) {
	const isCorrected = typeof infoVersion === 'string' && infoVersion.endsWith(CORRECTED_MARKER);

	// 契约目录还不存在（B 线尚未落盘）：允许用兜底来源，只警告
	if (isCorrected || !contractDirExists) {
		return { isCorrected, ok: true, reason: null };
	}

	if (allowFallback) {
		return { isCorrected, ok: true, reason: 'explicitly-allowed' };
	}

	return { isCorrected, ok: false, reason: 'missing-corrected-marker' };
}
