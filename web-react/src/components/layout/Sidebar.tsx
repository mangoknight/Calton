import { CalendarClock, Columns3, Filter, FolderTree, Home, KeyRound, LayoutDashboard, Tags } from 'lucide-react';
import { NavLink } from 'react-router-dom';

import { useSavedFilters } from '@/features/filters/queries';
import { useTranslation } from '@/i18n/context';
import { cn } from '@/lib/utils';
import { useUIStore } from '@/store/ui';

/**
 * Phase 1 导航面（终稿 §4 的 ★ 页面）。项目设置/teams/用户设置在 P2 之后。
 *
 * ⚠️ 文案存的是 **key**，不是文字：语言在运行时会变，导航表是模块级常量、
 * 只算一次。存文字的话切语言时这四项不会跟着变，而且**只有这四项不变**——
 * 那种"大部分界面翻了、个别地方没翻"的 bug 最难注意到。
 *
 * key 一律照抄上游（`frontend/src/i18n/lang/`），不自己起名 ——
 * 界面文案是用户可见契约的一部分。
 */
/**
 * `labelKey` 走 i18n；`label` 是字面量（用于上游没有对应 key 的 Calton 自有页面 ——
 * 全局看板不是上游概念，硬造一个 i18n key 得同步所有语言包，得不偿失）。
 */
const NAV: {
	to: string;
	icon: typeof Home;
	end: boolean;
	labelKey?: string;
	label?: string;
}[] = [
	{ to: '/', labelKey: 'navigation.overview', icon: Home, end: true },
	{ to: '/dashboard', labelKey: 'navigation.dashboard', icon: LayoutDashboard, end: false },
	{ to: '/projects', labelKey: 'project.projects', icon: FolderTree, end: false },
	{ to: '/board', labelKey: 'navigation.board', icon: Columns3, end: false },
	{ to: '/tasks/by/upcoming', labelKey: 'navigation.upcoming', icon: CalendarClock, end: false },
	{ to: '/labels', labelKey: 'label.title', icon: Tags, end: false },
	{ to: '/tokens', labelKey: 'user.settings.apiTokens.title', icon: KeyRound, end: false },
];

export function Sidebar() {
	const collapsed = useUIStore((s) => s.sidebarCollapsed);
	const t = useTranslation();

	return (
		<nav
			aria-label={t('navigation.main')}
			data-testid="app-sidebar"
			className={cn(
				'shrink-0 border-r border-sidebar-border bg-sidebar py-4 transition-[width]',
				collapsed ? 'w-16' : 'w-56',
			)}
		>
			<ul className="space-y-1 px-2">
				{NAV.map(({ to, labelKey, label, icon: Icon, end }) => (
					<li key={to}>
						{/*
						 * `data-nav` 是给测试用的稳定抓手，值取 i18n key（没有 key 的用路径）。
						 *
						 * ⚠️ 测试**不要按可见文案定位导航项**：那样的用例在任何一次纯文案调整下
						 * 都会红，而文案调整不改变任何行为。F13 迁移时这批测试就红过一轮，
						 * 红的信息量是零。按文案断言只应出现在**主题就是 i18n 的**用例里
						 * （`i18n/I18nProvider.test.tsx`），那里文案变了本来就该红。
						 */}
						<NavLink
							to={to}
							data-testid="nav-link"
							data-nav={labelKey ?? to}
							end={end}
							className={({ isActive }) =>
								cn(
									// 靛蓝激活态：左侧竖条指示（before 伪元素，不挤压内容）+ 暖灰非激活态
									'relative flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
									isActive
										? 'bg-accent font-medium text-primary before:absolute before:inset-y-1.5 before:left-0 before:w-0.5 before:rounded-full before:bg-primary'
										: 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
								)
							}
						>
							<Icon aria-hidden />
							<span className={cn(collapsed && 'sr-only')}>{label ?? t(labelKey!)}</span>
						</NavLink>
					</li>
				))}
			</ul>

			<SavedFilterNav collapsed={collapsed} />
		</nav>
	);
}

/**
 * 保存的过滤器（F11b）。
 *
 * ⚠️ 清单来自 `GET /projects` 里的负 ID 伪项目，**不是**某个 `/filters` 列表端点
 * （那个端点不存在）。换算与"-1 是收藏夹"的判据都在 `features/filters/pseudo-project.ts`。
 *
 * 链接指向 `/filters/{正的 filter id}` —— 负数只在调接口时出现，不进 URL。
 */
function SavedFilterNav({ collapsed }: { collapsed: boolean }) {
	const { filters } = useSavedFilters();
	const t = useTranslation();

	if (filters.length === 0) return null;

	return (
		<div className="mt-4 border-t border-sidebar-border pt-4" data-testid="saved-filter-nav">
			<p
				className={cn(
					// 分区小标题：小号大写 + 加宽字距，编辑感
					'px-3 pb-1 text-xs uppercase tracking-wider text-muted-foreground',
					collapsed && 'sr-only',
				)}
			>
				{/*
				 * ⚠️ 用 `filters.title` 而不是语义更贴的 `navigation.savedFilters`：
				 * 后者**上游 zh-CN 缺翻译**，会退回英文 "Saved filters"，
				 * 于是中文界面上冒出一行英文。`filters.title` 在 zh-CN 里就是"筛选器"。
				 * 这是"照抄上游 key"与"上游翻译进度不齐"之间的取舍，选了后者可见的那一侧。
				 */}
				{t('filters.title')}
			</p>
			<ul className="space-y-1 px-2">
				{filters.map((entry) => (
					<li key={entry.filterId}>
						<NavLink
							to={`/filters/${entry.filterId}`}
							data-testid="saved-filter-link"
							data-filter-id={entry.filterId}
							data-project-id={entry.projectId}
							className={({ isActive }) =>
								cn(
									'relative flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
									isActive
										? 'bg-accent font-medium text-primary before:absolute before:inset-y-1.5 before:left-0 before:w-0.5 before:rounded-full before:bg-primary'
										: 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
								)
							}
						>
							<Filter aria-hidden />
							<span className={cn(collapsed && 'sr-only')}>{entry.title}</span>
						</NavLink>
					</li>
				))}
			</ul>
		</div>
	);
}
