/**
 * 消息格式化（F13）。纯函数，不碰 React，便于单测。
 *
 * ## 为什么是自己写的，而不是装一个 i18n 库
 *
 * 语言包**逐字节照抄上游**（vue-i18n 格式），所以真正要兼容的不是"某个库"，
 * 而是**上游语言包里实际出现的那几种写法**。实测 en.json 1316 条消息，
 * 只用到三种构造：
 *
 * | 构造 | 条数 | 例 |
 * |---|---|---|
 * | `{name}` 插值 | 149 | `Good Night {username}!` |
 * | `\|` 复数分支 | 23 | `{count} comment \| {count} comments` |
 * | `{'x'}` 字面量转义 | 1 | `e.g. frederic{'@'}calton.io` |
 *
 * 没有 `@:key` 链接消息（0 条），没有内嵌 HTML（0 条），没有非字符串叶子（0 条）。
 * 覆盖这三种所需的代码比一个库的适配层还短，而且 gzip 预算这边**装不下第二个语言包**
 * （见 `messages.ts` 的说明），能省的都得省。
 *
 * ## ⚠️ 竖线不等于复数：`\|` 只在**调用方给了 count** 时才拆
 *
 * 语料里有一条 `migrate.csv.delimiters.pipe = "Pipe (\|)"` —— 它是在**描述竖线这个字符**。
 * 见到竖线就拆的实现会把它变成 `"Pipe ("`。
 * vue-i18n 也是这个语义（`t` 不拆、给了数量才拆），这里照做。
 */

/** 语言包是任意深度的嵌套对象，叶子必须是字符串。 */
export type Messages = { [key: string]: string | Messages };

export interface TranslateParams {
	/** 给了它才启用复数分支；同时作为 `{count}` 的插值来源。 */
	count?: number;
	[name: string]: string | number | undefined;
}

/**
 * 按 `a.b.c` 取一条消息。取不到、或取到的不是字符串（点到了中间节点）都返回 null，
 * 让调用方去走兜底语言。
 */
export function lookupMessage(messages: Messages, key: string): string | null {
	let node: string | Messages | undefined = messages;

	for (const segment of key.split('.')) {
		if (typeof node !== 'object' || node === null) return null;
		node = node[segment];
	}

	return typeof node === 'string' ? node : null;
}

/**
 * 复数分支的选取（vue-i18n 默认规则）：
 * - 2 支：`count === 1` 取第一支，否则第二支
 * - 3 支：`0` / `1` / 其余
 *
 * ⚠️ 上游给俄语单独写了规则（3 支时按俄语的 few/many 选），我们**没有复刻**。
 * 这不是遗漏而是范围声明：俄语语言包里的 3 支消息一旦被用到，
 * 复数形式会挑错。真要支持俄语时补在这里，并从上游 `i18n/index.ts` 的
 * `pluralRules` 抄规则，不要自己推。
 */
export function selectPluralForm(forms: string[], count: number): string {
	if (forms.length === 1) return forms[0];

	if (forms.length === 2) {
		return count === 1 ? forms[0] : forms[1];
	}

	if (count === 0) return forms[0];
	if (count === 1) return forms[1];
	return forms[2];
}

/**
 * 插值。`{'…'}` 是**字面量**（转义用），先于命名参数处理 ——
 * 否则 `{'@'}` 会被当成名为 `'@'` 的参数，找不到就原样留在界面上。
 *
 * 找不到对应参数的 `{name}` **原样保留**，不替换成空串：
 * 界面上留下 `{username}` 是一个刺眼的、能被发现的 bug；
 * 悄悄变成空串则会一直没人报。
 */
export function interpolate(message: string, params: TranslateParams): string {
	return message.replace(/\{(?:'([^']*)'|([^{}']+))\}/g, (whole, literal, name) => {
		if (literal !== undefined) return literal;

		const value = params[name as string];
		return value === undefined ? whole : String(value);
	});
}

/**
 * 完整的一次翻译：查 key → 选复数分支 → 插值。
 *
 * @param messages 当前语言的语言包
 * @param fallback 兜底语言包（en）。当前语言缺这条 key 时用它。
 * @returns 两份都没有这条 key 时返回 **key 本身** —— 与上游一致，
 *   也让缺失在界面上直接可见（见 `interpolate` 里同样的取舍）。
 */
export function translate(
	messages: Messages,
	fallback: Messages,
	key: string,
	params: TranslateParams = {},
): string {
	const raw = lookupMessage(messages, key) ?? lookupMessage(fallback, key);
	if (raw === null) return key;

	// ⚠️ 只有给了 count 才拆竖线，见文件头「竖线不等于复数」
	const message =
		params.count === undefined
			? raw
			: selectPluralForm(
					raw.split('|').map((f) => f.trim()),
					params.count,
				);

	return interpolate(message, params);
}
