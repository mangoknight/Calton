/**
 * 支持的语言（F13）。
 *
 * ## 这张表是**照抄上游的**，不是自己拟的
 *
 * 前端文案是用户可见契约的一部分：同一个界面元素，上游叫什么、我们就得叫什么。
 * 所以语言清单、语言代码、显示名、RTL 名单全部来自
 * `frontend/src/i18n/index.ts`，语言包本身来自 `frontend/src/i18n/lang/*.json`
 * **逐字节复制**。`lang-parity.test.ts` 会持续钉住这件事。
 *
 * ⚠️ 上游 `lang/` 下有 38 个文件，但 `SUPPORTED_LOCALES` 只列了 32 个
 * （多出来的是尚未接入的翻译）。这里**照抄 32 个那份**，不擅自扩表 ——
 * 显示名会跟着一起错。
 */

export const SUPPORTED_LOCALES = {
	en: 'English',
	'de-DE': 'Deutsch',
	'de-swiss': 'Schwizertütsch',
	'ru-RU': 'Русский',
	'fr-FR': 'Français',
	'vi-VN': 'Tiếng Việt',
	'it-IT': 'Italiano',
	'cs-CZ': 'Čeština',
	'pl-PL': 'Polski',
	'nl-NL': 'Nederlands',
	'pt-PT': 'Português',
	'zh-CN': '简体中文',
	'zh-TW': '繁體中文',
	'no-NO': 'Norsk Bokmål',
	'es-ES': 'Español',
	'da-DK': 'Dansk',
	'ja-JP': '日本語',
	'hu-HU': 'Magyar',
	'ar-SA': 'اَلْعَرَبِيَّةُ',
	'fa-IR': 'فارسی',
	'sl-SI': 'Slovenščina',
	'pt-BR': 'Português Brasileiro',
	'hr-HR': 'Hrvatski',
	'uk-UA': 'Українська',
	'lt-LT': 'Lietuvių Kalba',
	'bg-BG': 'Български',
	'ko-KR': '한국어',
	'tr-TR': 'Türkçe',
	'fi-FI': 'Suomi',
	'he-IL': 'עִבְרִית',
	'sv-SE': 'Svenska',
	'el-GR': 'Ελληνικά',
} as const;

export type SupportedLocale = keyof typeof SUPPORTED_LOCALES;

/**
 * 兜底语言。
 *
 * ⚠️ 这是**缺 key 时回退到的语言**，不是"界面默认显示的语言" ——
 * 后者由 `getBrowserLanguage()` 决定。两者混为一谈会让所有非英语用户
 * 一进来就看到英文。
 *
 * 兜底必须是 `en`：只有 en.json 是**全量**的，其余语言包都有缺 key
 * （上游的翻译进度不一），拿一个不全的语言当兜底等于把缺口变成空字符串。
 */
export const FALLBACK_LOCALE: SupportedLocale = 'en';

const RTL_LOCALES: readonly SupportedLocale[] = ['ar-SA', 'he-IL', 'fa-IR'];

export function isRTLLocale(locale: SupportedLocale): boolean {
	return RTL_LOCALES.includes(locale);
}

export function isSupportedLocale(value: string | null | undefined): value is SupportedLocale {
	return value !== null && value !== undefined && value in SUPPORTED_LOCALES;
}

/**
 * 浏览器语言 → 支持的语言。
 *
 * 匹配规则照抄上游：先精确匹配（`zh-CN` → `zh-CN`），
 * 再按**前缀加连字符**匹配（`zh` → `zh-CN`，取表里第一个）。
 *
 * ⚠️ 前缀匹配必须带连字符（`langKey.startsWith(browser + '-')`）。
 * 写成 `startsWith(browser)` 会让 `de` 匹配上 `de-swiss` 之外还可能误伤 ——
 * 更要紧的是 `he`（希伯来语）会匹配到任何以 he 开头的代码。
 */
export function getBrowserLocale(browserLanguage: string | undefined): SupportedLocale {
	if (!browserLanguage) return FALLBACK_LOCALE;

	const match = (Object.keys(SUPPORTED_LOCALES) as SupportedLocale[]).find(
		(langKey) => langKey === browserLanguage || langKey.startsWith(browserLanguage + '-'),
	);

	return match ?? FALLBACK_LOCALE;
}
