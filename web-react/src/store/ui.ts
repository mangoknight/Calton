import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import type { SupportedLocale } from '@/i18n/locales';

/**
 * 只放 UI 状态 —— 服务端状态一律走 TanStack Query（终稿 §4）。
 * 往这里塞项目/任务数据会绕开缓存失效，评审直接打回。
 */

export type Theme = 'light' | 'dark';

interface UIState {
	theme: Theme;
	sidebarCollapsed: boolean;
	/**
	 * 用户**显式选过**的界面语言。
	 *
	 * ⚠️ `null` 表示"没选过"，此时跟随浏览器语言 —— 它与"选了英文"必须能区分开。
	 * 给它一个默认值（比如 'en'）会把每个新用户永久钉在那个语言上，
	 * 浏览器语言再也不起作用，而界面上没有任何迹象能让人发现这件事。
	 */
	locale: SupportedLocale | null;
	setTheme: (theme: Theme) => void;
	toggleTheme: () => void;
	toggleSidebar: () => void;
	setLocale: (locale: SupportedLocale | null) => void;
}

export const useUIStore = create<UIState>()(
	persist(
		(set) => ({
			theme: 'light',
			sidebarCollapsed: false,
			locale: null,
			setTheme: (theme) => set({ theme }),
			toggleTheme: () => set((s) => ({ theme: s.theme === 'light' ? 'dark' : 'light' })),
			toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
			setLocale: (locale) => set({ locale }),
		}),
		{ name: 'calton-ui' },
	),
);
