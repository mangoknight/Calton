import { useI18n } from '@/i18n/context';
import { isSupportedLocale, SUPPORTED_LOCALES, type SupportedLocale } from '@/i18n/locales';

/**
 * 语言切换器（F13）。
 *
 * ## 为什么是原生 `<select>`
 *
 * 32 个语言用自定义下拉要自己做键盘导航、滚动、屏幕阅读器播报，
 * 而原生 select 在移动端还会用系统选择器。这里没有需要自定义的交互。
 *
 * ## ⚠️ 选项文字**不翻译**
 *
 * 每个语言的名字用**它自己的语言**写（`简体中文` 而不是 `Chinese`）——
 * 一个看不懂当前界面语言的用户，正是靠认出母语的名字才找得回来。
 * 这也是上游 `SUPPORTED_LOCALES` 那张表的写法，照抄。
 */
export function LanguageSwitcher() {
	const { locale, setLocale, t } = useI18n();

	return (
		<label className="flex items-center gap-2 text-sm text-muted-foreground">
			<span className="sr-only">{t('user.settings.general.language')}</span>
			<select
				aria-label={t('user.settings.general.language')}
				data-testid="language-switcher"
				value={locale}
				onChange={(event) => {
					const next = event.target.value;
					// 表里来的值本来就合法，这层是防"有人往 option 里塞了表外的值"
					if (isSupportedLocale(next)) setLocale(next);
				}}
				className="h-9 border border-input bg-background px-2 text-sm"
			>
				{(Object.entries(SUPPORTED_LOCALES) as [SupportedLocale, string][]).map(([code, label]) => (
					<option key={code} value={code}>
						{label}
					</option>
				))}
			</select>
		</label>
	);
}
