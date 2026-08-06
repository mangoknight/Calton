/** 给 bundle-budget.mjs 的类型声明：单测要 import 它验判定逻辑。 */
export const MAIN_CHUNK_GZIP_BUDGET: number;
export const MAIN_CHUNK_GZIP_MEASURED: number;
export const MAIN_CHUNK_PREFIX: string;
export function evaluateBundleBudget(
	chunks: { name: string; gzipBytes: number }[],
	budget?: number,
): { ok: boolean; reason: string | null; message: string };
