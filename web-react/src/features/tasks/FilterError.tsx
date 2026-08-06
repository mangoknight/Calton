import type { CaltonError } from '@/api/errors';
import { explanationFor, FILTER_ERROR_CODES, isFilterError } from './filter-errors';

/**
 * 筛选器错误的**展示层**。所有"为什么这么显示"的口径在 `filter-errors.ts` 的文件头，
 * 改这里之前先读那段 —— 尤其是"后端 message 一律原样透出，不改写不解析"。
 */

/**
 * 视图取数失败的统一出口：filter 写错了走 `FilterError`（有针对性的解释），
 * 其余（401/403/网络/服务端故障）保持原来的朴素展示。
 *
 * ⚠️ 分流判据是**错误码**而不是 status —— 这五个码都是 400，
 * 但不是所有 400 都来自 filter（例如排序字段非法也是 400/4016…
 * 注意 4016 **同时**用于排序与筛选的未知字段，见 `error_codes.py` 的
 * `models.ErrInvalidTaskField`）。把 4016 一律解释成"筛选字段错了"会误导
 * 那些实际是 `?sort=` 写错的用户，所以文案只说"字段名不对"，不说是哪个入口。
 */
export function TaskQueryError({ error }: { error: CaltonError }) {
	if (isFilterError(error)) return <FilterError error={error} />;

	return (
		<p role="alert" className="text-sm text-xyz-red-6">
			{error.message}
		</p>
	);
}

export function FilterError({ error }: { error: CaltonError }) {
	const code = error.code;
	const isExpression = code === FILTER_ERROR_CODES.invalidExpression;
	const explanation = code === undefined ? null : explanationFor(code);

	return (
		<div
			role="alert"
			data-testid="filter-error"
			className="border border-xyz-red-5 bg-xyz-red-1 p-3 text-sm"
		>
			<p className="font-medium text-xyz-red-7">筛选条件无法执行</p>

			{/* 后端原文：唯一能定位错误的信息，永远原样展示 */}
			<p data-testid="filter-error-message" className="mt-1 font-mono text-xs text-xyz-red-7">
				{error.message}
			</p>

			{isExpression ? (
				// ② 引号是预处理加的，不是用户打的
				<p data-testid="filter-error-preprocessed-note" className="mt-2 text-xyz-red-7">
					上面引号里的表达式是服务端预处理后的样子，可能与你输入的不完全一样（例如日期会被自动加上引号），
					冒号后面是解析器指出的具体位置。
				</p>
			) : null}

			{explanation ? (
				<p data-testid="filter-error-explanation" className="mt-2 text-xyz-red-7">
					{explanation}
				</p>
			) : null}
		</div>
	);
}
