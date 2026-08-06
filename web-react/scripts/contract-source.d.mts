/** 给 contract-source.mjs 的类型声明：单测要 import 它做文件名耦合断言。 */
export const CORRECTED_CONTRACT_FILENAME: string;
export const RAW_CONTRACT_FILENAME: string;
export const CONTRACT_DIR_REL: string;
export const CORRECTED_MARKER: string;
export function evaluateContractSource(input: {
	infoVersion: unknown;
	contractDirExists: boolean;
	allowFallback: boolean;
}): { isCorrected: boolean; ok: boolean; reason: string | null };
