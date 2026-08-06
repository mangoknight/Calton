import * as React from 'react';

import type { SupportedLocale } from './locales';
import type { TranslateParams } from './translate';

/**
 * i18n 的 context 与两个 hook。
 *
 * ⚠️ 单独一个文件是**工具要求**，不是洁癖：`react-refresh/only-export-components`
 * 要求一个模块要么只导出组件、要么只导出别的东西。
 * 和 Provider 放一起会让整个模块在热更新时被整体替换 —— 开发时切一次语言就丢一次状态。
 */

export interface I18nContextValue {
	locale: SupportedLocale;
	setLocale: (locale: SupportedLocale) => void;
	t: (key: string, params?: TranslateParams) => string;
}

export const I18nContext = React.createContext<I18nContextValue | null>(null);

/**
 * ⚠️ Provider 缺失时**抛错**，不静默退回英文。
 *
 * 静默退回的话，"忘了把 I18nProvider 挂进 AppProviders"这个错误会表现为
 * "界面是英文的"—— 而那与"用户的浏览器是英文"长得一模一样，
 * 没有人会去查。这是实践第 10 条那类"交付了但没接上"的典型形状。
 */
export function useI18n(): I18nContextValue {
	const context = React.useContext(I18nContext);
	if (context === null) {
		throw new Error('useI18n 必须在 I18nProvider 内使用 —— 检查 AppProviders 有没有挂上它');
	}
	return context;
}

/** 只要 `t` 时用它，省得每处都解构。 */
export function useTranslation(): I18nContextValue['t'] {
	return useI18n().t;
}
