import type { Messages } from './translate';
import { FALLBACK_LOCALE, type SupportedLocale } from './locales';

import fallbackMessages from './lang/en.json';

/**
 * 语言包的装载（F13）。
 *
 * ## ⚠️ 只有 en 是静态打进主 chunk 的，其余**一律懒加载**
 *
 * 这不是风格选择，是包体预算逼出来的：主 chunk 实测 gzip 169_566 字节、
 * 预算 204_000（`scripts/bundle-budget.mjs`），而 en.json gzip 20_122、
 * zh-CN.json gzip 15_915。**静态打进两个语言包就是 205_603，当场超预算。**
 * 所以静态的那一个只能是兜底语言 en（`translate` 缺 key 时要同步取用，
 * 它必须随时在手），显示语言无论是哪个都走懒加载。
 *
 * 上游 `frontend/src/i18n/index.ts` 也是同一个形状（静态 en + 动态 import 其余）。
 */

const LANG_MODULES = import.meta.glob<{ default: Messages }>('./lang/*.json');

export const FALLBACK_MESSAGES = fallbackMessages as unknown as Messages;

/** 已装载的语言包。en 一开始就在。 */
const loaded = new Map<SupportedLocale, Messages>([[FALLBACK_LOCALE, FALLBACK_MESSAGES]]);

export function getLoadedMessages(locale: SupportedLocale): Messages | undefined {
	return loaded.get(locale);
}

/**
 * 装载一个语言包。已装载的直接返回，不重复请求。
 *
 * ⚠️ 装载失败**不抛**，返回兜底语言包并在控制台留一条 —— 一个语言文件
 * 拉不下来不该让整个应用白屏。这与 `translate` 里"缺 key 用 en 顶上"
 * 是同一条取舍：i18n 的失效模式应该是**降级**，不是崩溃。
 */
export async function loadLocaleMessages(locale: SupportedLocale): Promise<Messages> {
	const already = loaded.get(locale);
	if (already) return already;

	const loader = LANG_MODULES[`./lang/${locale}.json`];
	if (!loader) {
		console.error(`[i18n] 没有 ${locale} 的语言包，退回 ${FALLBACK_LOCALE}`);
		return FALLBACK_MESSAGES;
	}

	try {
		const module = await loader();
		const messages = module.default;
		loaded.set(locale, messages);
		return messages;
	} catch (error) {
		console.error(`[i18n] 装载 ${locale} 失败，退回 ${FALLBACK_LOCALE}`, error);
		return FALLBACK_MESSAGES;
	}
}

/**
 * 供测试用：把一份语言包**同步**塞进来。
 *
 * 测试需要确定的语言（不能靠 jsdom 的 navigator.language，那是 en-US），
 * 而真实装载是异步的 —— 每个用例都等一次 import 既慢又给所有断言加一层竞态。
 */
export function primeLocaleMessages(locale: SupportedLocale, messages: Messages): void {
	loaded.set(locale, messages);
}
