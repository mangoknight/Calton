import * as React from 'react';

import { useUIStore } from '@/store/ui';
import { getBrowserLocale, isRTLLocale } from './locales';
import { I18nContext, type I18nContextValue } from './context';
import { FALLBACK_MESSAGES, getLoadedMessages, loadLocaleMessages } from './messages';
import { translate, type Messages, type TranslateParams } from './translate';

/**
 * i18n 的 React 接线（F13）。
 *
 * ## 初始语言 = 用户存过的 > 浏览器语言 > en
 *
 * ⚠️ "存过的"必须能与"没存过"区分开，所以 store 里存的是 `SupportedLocale | null`
 * 而不是给个默认值。给默认值的话，一个从没选过语言的用户会被永久钉在那个默认值上，
 * 浏览器语言再也不起作用 —— 而这件事**没有任何界面提示**，只会被当成"i18n 没生效"。
 *
 * ## 语言包是异步来的，所以第一帧可能还是 en
 *
 * 只有 en 静态打进主 chunk（包体预算，见 `messages.ts`）。切到别的语言要等一次
 * 动态 import。这期间**显示兜底语言而不是空白** —— 闪一下英文，好过闪一屏骨架。
 */

export function I18nProvider({ children }: { children: React.ReactNode }) {
	const storedLocale = useUIStore((s) => s.locale);
	const setStoredLocale = useUIStore((s) => s.setLocale);

	const locale = storedLocale ?? getBrowserLocale(navigator.language);

	// 已装载的语言包（同步可得的那份）。装载完成后靠 setState 触发重渲染。
	const [messages, setMessages] = React.useState<Messages>(
		() => getLoadedMessages(locale) ?? FALLBACK_MESSAGES,
	);

	React.useEffect(() => {
		// 只用于忽略过期的装载结果（用户在装载途中又切了语言）
		let cancelled = false;

		// 同步能拿到就别走异步 —— 否则每次切回一个已装载的语言都要多一帧英文
		const already = getLoadedMessages(locale);
		if (already) {
			setMessages(already);
		} else {
			void loadLocaleMessages(locale).then((loaded) => {
				// 用户在装载途中又切了语言时，丢弃这次的结果
				if (!cancelled) setMessages(loaded);
			});
		}

		// 这两个属性是给浏览器和读屏用的，不设的话阿拉伯语/希伯来语界面不会翻转
		document.documentElement.lang = locale;
		document.documentElement.dir = isRTLLocale(locale) ? 'rtl' : 'ltr';

		return () => {
			cancelled = true;
		};
	}, [locale]);

	const t = React.useCallback(
		(key: string, params?: TranslateParams) => translate(messages, FALLBACK_MESSAGES, key, params),
		[messages],
	);

	const value = React.useMemo<I18nContextValue>(
		() => ({ locale, setLocale: setStoredLocale, t }),
		[locale, setStoredLocale, t],
	);

	return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
