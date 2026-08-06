import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { renderApp } from '@/test/render';
import { useUIStore } from '@/store/ui';
import { useTranslation } from './context';
import { getBrowserLocale } from './locales';

/**
 * i18n 的**接线**测试（F13）。
 *
 * ⚠️ 与 `translate.test.ts` 分工不同：那边测的是纯函数，
 * 这边测的是"它真的被挂进了应用，并且真的在起作用"——
 * 实践第 10 条那类"模块交付了但没接上"的问题只有从最外层打才看得见。
 * 所以这里一律用 `renderApp`（真实路由 + 真实 Provider 链），不单独渲染组件。
 */

/** 侧栏第一项：`navigation.overview`。zh-CN 是"概览"，en 是 "Overview"。 */
const OVERVIEW_ZH = '概览';
const OVERVIEW_EN = 'Overview';

describe('i18n 接线', () => {
	/**
	 * ★★ Provider 确实挂在应用里：界面上出现的是**语言包里的译文**，
	 * 而不是某个硬编码的中文。
	 *
	 * 判别式取值：`navigation.overview` 的 zh 译文是"概览"，而 F13 之前这里
	 * 硬编码的是"首页"。两者不同，所以这条能分辨出"t() 真的生效"
	 * 与"只是恰好还显示着老文案"。
	 */
	it('★★ 应用里的文案来自语言包（zh-CN 显示「概览」，不是旧的硬编码「首页」）', async () => {
		renderApp('/labels');

		expect(await screen.findByRole('link', { name: OVERVIEW_ZH })).toBeInTheDocument();
		expect(screen.queryByRole('link', { name: '首页' })).not.toBeInTheDocument();
	});

	/**
	 * ★★★ 切语言，界面**真的跟着变**。
	 *
	 * 这是整个 F13 唯一不可替代的一条：语言包对、t() 对、切换器能点，
	 * 三件事都对，界面照样可能一动不动（比如文案在模块级常量里存的是**文字**
	 * 而不是 key —— 那份常量只算一次，切语言时它不会重算）。
	 *
	 * ⚠️ 走的是真实的动态 import（en 之外的语言包都是懒加载），所以要 waitFor。
	 */
	it('★★★ 切到 English 后侧栏导航文字真的变成英文', async () => {
		renderApp('/labels');
		expect(await screen.findByRole('link', { name: OVERVIEW_ZH })).toBeInTheDocument();

		await userEvent.selectOptions(screen.getByTestId('language-switcher'), 'en');

		await waitFor(() =>
			expect(screen.getByRole('link', { name: OVERVIEW_EN })).toBeInTheDocument(),
		);
		expect(screen.queryByRole('link', { name: OVERVIEW_ZH })).not.toBeInTheDocument();
	});

	/**
	 * ★★ 切语言要把 `<html lang>` / `<html dir>` 设对。
	 *
	 * 判别式取值必须挑一个 **RTL** 语言：只在 en/zh 之间切的话，
	 * `dir` 恒为 `ltr`，"设了 dir"与"根本没设 dir"同解，这条什么也验不了
	 * （实践第 4 条）。
	 */
	it('★★ 切到阿拉伯语后 <html> 的 lang 与 dir 都跟着变（RTL）', async () => {
		renderApp('/labels');
		await screen.findByRole('link', { name: OVERVIEW_ZH });
		expect(document.documentElement.dir).toBe('ltr');

		await userEvent.selectOptions(screen.getByTestId('language-switcher'), 'ar-SA');

		await waitFor(() => expect(document.documentElement.lang).toBe('ar-SA'));
		expect(document.documentElement.dir).toBe('rtl');
	});

	/**
	 * ★★★ **表单校验消息也要跟着语言变。**
	 *
	 * ## 这条是补上来的，补的原因值得写下来
	 *
	 * zod schema 是**模块级常量、只算一次**，所以里面存的必须是 i18n key、
	 * 在渲染时才翻译（与 `Sidebar.tsx` 的 NAV 表同一个坑）。
	 * 我按这个思路写了实现，然后做变异验证 —— **把 key 换回写死的句子，全绿。**
	 *
	 * 也就是说这条设计**当时没有任何测试守着**：改坏了没人会知道，
	 * 而表现只是"切了语言，别处都变了，就校验消息还是中文"——
	 * 正是那种"大部分翻了、个别没翻"的最难察觉的形状。
	 *
	 * 判别式：locale 设成 en，断言看到的是**英文那句**。
	 * 存句子的实现在这里会显示中文，两者分歧。
	 */
	it('★★★ 切到英文后，表单校验消息也是英文（不是写死的中文）', async () => {
		useUIStore.setState({ locale: 'en' });
		renderApp('/login', { token: null });

		await userEvent.click(await screen.findByTestId('login-submit'));

		// user.auth.usernameRequired 的 en 译文
		expect(await screen.findByTestId('username-error')).toHaveTextContent(
			'Please provide a username.',
		);
	});

	/**
	 * ★★ 语言包缺 key 时退回 en —— 用**真实语料里真的缺的那条**验证。
	 *
	 * `navigation.main` 在上游 zh-CN.json 里不存在（`translate.test.ts` 里
	 * 单独钉了"zh 是 en 的真子集"这个前提）。所以中文界面上这处 aria-label
	 * 就是英文的 "Main navigation"。这不是 bug，是设计选择在真实语料上的表现。
	 */
	it('★★ zh-CN 缺的 key 退回 en（真实语料：navigation.main）', async () => {
		renderApp('/labels');
		expect(await screen.findByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument();
	});

	/**
	 * ★★ 没选过语言时跟随浏览器语言。
	 *
	 * store 里 `locale: null` 表示"没选过"。给它一个默认值会让这条永远测不出来 ——
	 * 每个新用户被钉死在默认语言上，而界面上没有任何迹象。
	 */
	it('★★ 没选过语言时跟随浏览器语言', async () => {
		useUIStore.setState({ locale: null });
		vi.spyOn(navigator, 'language', 'get').mockReturnValue('en-US');

		renderApp('/labels');

		expect(await screen.findByRole('link', { name: OVERVIEW_EN })).toBeInTheDocument();
	});

	/**
	 * ★★ 选过之后，浏览器语言**不再**起作用。
	 *
	 * 判别式配置：浏览器是 en，用户选的是 zh-CN —— 两者必须冲突，
	 * 否则"用了存的值"与"用了浏览器值"同解。
	 */
	it('★★ 选过语言后浏览器语言不再起作用（判别式：浏览器 en × 选了 zh-CN）', async () => {
		useUIStore.setState({ locale: 'zh-CN' });
		vi.spyOn(navigator, 'language', 'get').mockReturnValue('en-US');

		renderApp('/labels');

		expect(await screen.findByRole('link', { name: OVERVIEW_ZH })).toBeInTheDocument();
	});
});

describe('浏览器语言匹配', () => {
	it('精确匹配', () => {
		expect(getBrowserLocale('zh-CN')).toBe('zh-CN');
	});

	it('只有主语言时按前缀匹配到具体地区', () => {
		expect(getBrowserLocale('zh')).toBe('zh-CN');
		expect(getBrowserLocale('de')).toBe('de-DE');
	});

	/**
	 * ★ 前缀匹配必须带连字符（`startsWith(browser + '-')`）。
	 *
	 * ## ⚠️ 这条断言的第一版**测不出东西**，记在这里当反例
	 *
	 * 第一版用的判别值是 `'e'`：期望落到兜底 `en`。
	 * 但漏了连字符的实现遇到 `'e'` 时，第一个命中的恰好也是 `'en'`
	 * （`'en'.startsWith('e')`）—— **两种实现同解**，变异验证照样绿。
	 * 这正是实践第 4 条那个坑：断言逻辑没问题、变异也做了，
	 * 但判别值让待验的区分消失了。
	 *
	 * 判别值必须满足：**正确实现落到兜底，错误实现落到某个具体语言**。
	 * `'z'` 就是这样一个值 —— 没有 `z-` 开头的语言（正确 ⇒ `en`），
	 * 而 `'zh-CN'.startsWith('z')` 为真（错误 ⇒ `zh-CN`）。
	 */
	it('★ 前缀匹配带连字符（判别值 "z"：正确落 en，漏连字符会落 zh-CN）', () => {
		expect(getBrowserLocale('z')).toBe('en');
		// 而完整的主语言码照常匹配得到
		expect(getBrowserLocale('zh')).toBe('zh-CN');
	});

	it('不认识的语言退回 en', () => {
		expect(getBrowserLocale('xx-XX')).toBe('en');
		expect(getBrowserLocale(undefined)).toBe('en');
	});
});

describe('Provider 缺失', () => {
	/**
	 * ★★ 没有 Provider 时**抛错**，不静默退回英文。
	 *
	 * 静默退回的话，"忘了把 I18nProvider 挂进 AppProviders"会表现为
	 * "界面是英文的"—— 与"用户浏览器是英文"长得一模一样，没人会去查。
	 */
	it('★★ useI18n 在 Provider 外使用时抛错，而不是悄悄用英文', () => {
		function Boom() {
			useTranslation();
			return null;
		}

		// React 会把渲染期的错误打到 console.error，这里静音，免得测试输出里像是真出了事
		const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
		expect(() => render(<Boom />)).toThrow(/I18nProvider/);
		spy.mockRestore();
	});
});
