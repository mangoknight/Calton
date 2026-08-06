/**
 * 筛选器 DSL 的**编写期提示**（F11a）。
 *
 * ## ⚠️ 这些是提示，不是校验 —— 绝不阻止提交
 *
 * F10 立过的规矩是"前端不许替后端补校验"，那条规矩防的是**前端拦住了后端会接受的请求**。
 * 本模块与它不冲突，因为它**不拦任何东西**：`filterHints()` 只产出文案，
 * 调用方照常把用户原样输入的 filter 发出去。区别是"拦下" vs "旁注"。
 *
 * 之所以要旁注，是因为下面这几种写法**后端全都返回 200**，
 * 用户拿到的是一份**看起来正常但语义不对**的结果，没有任何信号提示他写错了。
 *
 * ## 三条都来自实测语料（`_filter_like.yaml`），不是推断
 *
 * 1. **`assignees like …` 被整个丢弃** —— 200，返回项目里**全部**任务。
 *    不是报错、也不是空集：`assignees like 'zzz'`（绝不可能匹配任何人）照样返回全部。
 *    ☠ 危害在方向：被丢掉的是一个**收窄**条件，于是用户看到的数据**比预期多**。
 *    若这个 filter 被当成"只看指派给我的"来用，静默丢弃意味着**看到所有人的任务**，
 *    而请求 200、无警告、无日志。这是本组唯一一条会造成"少看见拦截、多看见数据"的。
 * 2. **`assignees` 比的是用户名，不是用户 id** —— `assignees = 901` 静默返回空集。
 * 3. **`labels` 比的是标签 id，不是标签名** —— 与 `assignees` 恰好相反。
 *    两个长得一模一样的关联字段，一个收 id 一个收字符串，猜错的那一方静默返回空集。
 *
 * ## 匹配策略：宁可漏报，不可误报
 *
 * 正则只认**高置信度**的写法（如 `assignees = 123`），不做完整解析。
 * 漏报的代价是少一条提示；误报的代价是对一个**正确**的 filter 说它写错了 ——
 * 后者会让用户不敢相信提示，进而连真正有用的第 1 条也一起忽略。
 * 所以 `labels != 'bug'`、多余空格等变体**故意不覆盖**。
 */

export interface FilterHint {
	/** 稳定 id，用于 UI key 与测试定位。 */
	id: string;
	message: string;
}

/** `assignees like` —— 条件被静默丢弃，结果**变多**。 */
const ASSIGNEES_LIKE = /\bassignees\s+like\b/i;
/** `assignees = 123` / `assignees == '123'` —— 拿 id 当用户名比，静默空集。 */
const ASSIGNEES_NUMERIC = /\bassignees\s*(?:!=|={1,2})\s*['"]?\d+['"]?/i;
/** `labels = bug` / `labels = 'bug'` —— 拿名字当 id 比，静默空集。值以字母开头才算。 */
const LABELS_NON_NUMERIC = /\blabels\s*(?:!=|={1,2})\s*['"]?[A-Za-z_][\w-]*['"]?/i;

export function filterHints(filter: string): FilterHint[] {
	const hints: FilterHint[] = [];

	if (ASSIGNEES_LIKE.test(filter)) {
		hints.push({
			id: 'assignees-like-dropped',
			message:
				'assignees 不支持 like：这个条件会被服务端整个丢掉，请求照样成功，' +
				"但结果里会包含没有被筛掉的任务（比你预期的多）。改用 assignees = '用户名'。",
		});
	}

	if (ASSIGNEES_NUMERIC.test(filter)) {
		hints.push({
			id: 'assignees-expects-username',
			message: 'assignees 比较的是用户名而不是用户 id，填数字会得到空结果且不报错。',
		});
	}

	if (LABELS_NON_NUMERIC.test(filter)) {
		hints.push({
			id: 'labels-expects-id',
			message: 'labels 比较的是标签 id 而不是标签名，填名字会得到空结果且不报错。',
		});
	}

	return hints;
}
