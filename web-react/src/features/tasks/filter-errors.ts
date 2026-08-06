import type { CaltonError } from '@/api/errors';

/**
 * 筛选器错误展示（F11a）。
 *
 * ## 核心原则：后端 message 一律原样透出，不改写、不吞
 *
 * 这些 message 里内插了**用户写了什么**（字段名 / 比较符 / 取值 / 整条表达式），
 * 是唯一能告诉用户"错在哪"的信息。前端的补充说明只能**加在旁边**，不能替换它。
 *
 * ## 五个错误码出自**不同的解析阶段**，不能合并成一个出口
 *
 * 后端 `core/error_codes.py` 里的 message 模板（这是实现侧的权威定义）：
 *
 *   4016 `The task field '{task_field}' is invalid.`
 *   4017 `The task filter comparator '{comparator}' is invalid.`
 *   4018 `The task filter concatinator '{concatinator}' is invalid.`
 *   4019 `The task filter value '{value}' for field '{field}' is invalid.`
 *   4024 `The filter expression '{expression}' is invalid: {expression_error}`
 *
 * ⚠️ **4018 不在最初的任务卡里**（卡上只写了 4016/4017/4019）。它是同族的第五个码，
 * 用户把 `&&` 写成 `and`/`&` 就会撞上，DSL 输入框必然会遇到，所以一并覆盖。
 *
 * ## ⚠️ 三个"照抄不许改进"的实测事实
 *
 * **① 比较符先于字段校验**（`filters/parser.py:300`，注释原文：
 * "Comparator before field, as upstream: a filter that is wrong in both ways is 4017"）。
 * 所以 **4017 不代表字段是对的** —— 两处都写错时只会看到 4017，修好比较符后
 * 可能再冒出一个 4016。UI 必须把这句说出来，否则用户会以为"就剩这一个错"。
 *
 * **② 4024 的 message 里引用的是"预处理之后"的表达式，不是用户原文**
 * （`parser.py` 的 `parse_task_filter`：`expression=preprocessed`）。
 * 实测例子：用户写 `due_date > 2026-01-01||+1M/d`，
 * message 里是 `due_date > '2026-01-01'||+1M/d` —— **日期被自动加了引号**。
 * 那对引号正是 `+` 变成非法字符的原因，所以它有用；但它**不是用户打的**，
 * UI 必须标明这是"解析器看到的表达式"，否则用户会找自己没写过的引号。
 *
 * **③ 日期字段的 4019 拿不到"日期哪里错了"**
 * （`filters/datemath.py` 文件头：datemath 的错误文本被上游**丢弃**，
 * 失败后降级到通用时间解析，再失败才报 4019，而 4019 只内插 field 与 value，
 * 原文是 "Nothing the parser says reaches the client"）。
 * 所以对日期类取值，后端能给的就只有"这个值不行"，给不出"错在第几个字符"。
 * 这一格必须由前端补一句可用语法，否则用户面对的是一条无从下手的报错。
 *
 * ## ⚠️ 不要解析 message 文本
 *
 * 4019 的 message 在 Go 侧有格式化 bug（`%!s(int64=950)`）且回显的是**内部列名**
 * （用户写 `labels`，message 里是 `label_id`），已由 team-lead 收进对拍豁免 ——
 * 也就是说 **Python 侧的这条 message 不与 Go 逐字节对齐**，
 * 而 Python 的单测只断言了 `code == 4019`、**没有钉住 message**。
 * 结论：message 可以**展示**，但任何"从 message 里抠出字段名再去高亮输入框"的做法
 * 都建立在一个没有测试保护、且两侧不一致的字符串上。本组件只展示，不解析。
 */

/** 筛选器相关的错误码。同族但出自不同解析阶段。 */
export const FILTER_ERROR_CODES = {
	invalidField: 4016,
	invalidComparator: 4017,
	invalidConcatinator: 4018,
	invalidValue: 4019,
	invalidExpression: 4024,
} as const;

const FILTER_CODE_SET: ReadonlySet<number> = new Set(Object.values(FILTER_ERROR_CODES));

/** 这个错误是不是"用户的 filter 写错了"，而不是网络/权限/服务端故障。 */
export function isFilterError(error: CaltonError | null | undefined): boolean {
	return error?.code !== undefined && FILTER_CODE_SET.has(error.code);
}

/**
 * 每个码的**补充说明**。故意不含后端 message —— message 由组件单独原样渲染，
 * 两者在 UI 上是分开的两块，避免有人日后"顺手"把说明和原文拼成一句而丢掉原文。
 */
export function explanationFor(code: number): string | null {
	switch (code) {
		case FILTER_ERROR_CODES.invalidField:
			return '字段名要用 API 的字段名（如 done、priority、due_date、labels、assignees），不是界面上的中文名。';
		case FILTER_ERROR_CODES.invalidComparator:
			// ① 校验顺序：比较符先于字段
			return '可用的比较符：= != > >= < <= like in not in。注意服务端先校验比较符再校验字段，所以改对比较符后可能还会暴露字段名的错误。';
		case FILTER_ERROR_CODES.invalidConcatinator:
			return '条件之间只能用 && 或 ||。';
		case FILTER_ERROR_CODES.invalidValue:
			// ③ 日期类取值拿不到细节
			return '若这是日期字段：支持 2026-01-01 这样的绝对日期，以及 now、now+30d、now/w 这类相对写法；服务端不会告诉你日期具体错在哪一段，只会说这个值不行。';
		case FILTER_ERROR_CODES.invalidExpression:
			return null; // 表达式类错误的说明与原文强相关，单独渲染，见组件
		default:
			return null;
	}
}
