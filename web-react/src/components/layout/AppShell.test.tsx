import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { renderApp } from '@/test/render';

/**
 * ⚠️ **这个文件不按可见文案定位元素**，一律走 testid / `data-nav` / role。
 *
 * 理由不是风格：按文案定位的用例，**在任何一次纯文案调整下都会红，
 * 而文案调整不改变任何行为**。F13 把导航文案换成上游译文时，这批用例
 * 就红过一轮 —— 那批红的信息量是零，只是在告诉我"文案确实换了"。
 *
 * 按文案断言只应出现在**主题就是 i18n 的**用例里
 * （`src/i18n/I18nProvider.test.tsx`）：那里文案变了本来就该红，那正是它要验的东西。
 *
 * 这个文件验的是**骨架**：导航项在不在、激活态对不对、折叠改不改宽度、
 * 主题类挂没挂上。这几件事与文案是什么、界面是哪国语言，全都无关。
 */

/** 导航项的稳定标识：`data-nav` 存的是 i18n key（见 Sidebar.tsx）。 */
const NAV_KEYS = [
	'navigation.overview',
	'/dashboard', // 管理面板 & 看板：Calton 自有页，无 i18n key，data-nav 取路径（见 Sidebar.tsx）
	'project.projects',
	'/board',
	'navigation.upcoming',
	'label.title',
];

describe('AppShell 骨架', () => {
	it('侧边栏列出 Phase 1 导航项，当前路由项标为激活', async () => {
		renderApp('/labels');

		const nav = screen.getByTestId('app-sidebar');
		const links = within(nav).getAllByTestId('nav-link');
		expect(links.map((link) => link.getAttribute('data-nav'))).toEqual(NAV_KEYS);

		// 当前路由是 /labels，激活的应当是标签那一项 —— 按 data-nav 认，不按文字
		expect(nav.querySelector('a[aria-current="page"]')).toHaveAttribute('data-nav', 'label.title');
	});

	it('点导航切换主内容区', async () => {
		renderApp('/');

		const labelsLink = screen
			.getAllByTestId('nav-link')
			.find((link) => link.getAttribute('data-nav') === 'label.title')!;
		await userEvent.click(labelsLink);

		expect(await screen.findByTestId('labels-page')).toBeInTheDocument();
	});

	it('折叠侧边栏后导航项保留给读屏（sr-only），不是被删掉', async () => {
		renderApp('/');
		expect(screen.getByTestId('app-sidebar')).toHaveClass('w-56');

		await userEvent.click(screen.getByTestId('toggle-sidebar'));

		expect(screen.getByTestId('app-sidebar')).toHaveClass('w-16');
		// 折叠后仍然是 4 项，只是文字被 sr-only 藏起来给读屏
		expect(screen.getAllByTestId('nav-link')).toHaveLength(NAV_KEYS.length);
	});

	it('主题切换把 .dark 挂到 <html> 上（Radix Portal 依赖）', async () => {
		renderApp('/');
		expect(document.documentElement).not.toHaveClass('dark');
		expect(screen.getByTestId('toggle-theme')).toHaveAttribute('data-theme', 'light');

		await userEvent.click(screen.getByTestId('toggle-theme'));

		expect(document.documentElement).toHaveClass('dark');
		expect(screen.getByTestId('toggle-theme')).toHaveAttribute('data-theme', 'dark');
	});
});
