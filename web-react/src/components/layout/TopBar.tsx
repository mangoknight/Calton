import { LogOut, Moon, PanelLeft, Sun } from 'lucide-react';
import { Link } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { LanguageSwitcher } from '@/components/layout/LanguageSwitcher';
import { useCurrentUser, useLogout } from '@/features/auth/queries';
import { useTranslation } from '@/i18n/context';
import { useUIStore } from '@/store/ui';

export function TopBar() {
	const theme = useUIStore((s) => s.theme);
	const toggleTheme = useUIStore((s) => s.toggleTheme);
	const toggleSidebar = useUIStore((s) => s.toggleSidebar);
	const { data: user } = useCurrentUser();
	const logout = useLogout();
	const t = useTranslation();

	return (
		<header
			data-testid="app-topbar"
			className="flex h-14 shrink-0 items-center gap-3 border-b bg-card px-4"
		>
			<Button
				variant="ghost"
				size="icon"
				data-testid="toggle-sidebar"
				aria-label={t('navigation.closeSidebar')}
				onClick={toggleSidebar}
			>
				<PanelLeft aria-hidden />
			</Button>
			<Link to="/" className="text-base font-semibold text-foreground">
				Calton
			</Link>
			<div className="flex-1" />
			<LanguageSwitcher />
			{/*
			 * ⚠️ 下面这两句是**上游语言包里没有对应 key 的**：Calton 的主题在设置页选，
			 * 没有顶栏这个开关，所以它没有这条文案。硬编码中文留在这里是**有意的**，
			 * 不是漏迁 —— 编一个上游没有的 key 塞进语言包会让 lang-parity 守卫红，
			 * 而那道守卫正是用来防止我们和上游的文案契约分叉的。
			 * 要 i18n 它，得先在上游提一个 key（或明确登记为"我们独有的文案"并另建命名空间）。
			 */}
			<Button
				variant="ghost"
				size="icon"
				data-testid="toggle-theme"
				data-theme={theme}
				aria-label={theme === 'light' ? '切换到暗色' : '切换到亮色'}
				onClick={toggleTheme}
			>
				{theme === 'light' ? <Moon aria-hidden /> : <Sun aria-hidden />}
			</Button>
			{user ? (
				<>
					<span className="text-sm text-muted-foreground">{user.name || user.username}</span>
					<Button
						variant="ghost"
						size="icon"
						data-testid="logout"
						aria-label={t('user.auth.logout')}
						onClick={() => logout.mutate()}
						disabled={logout.isPending}
					>
						<LogOut aria-hidden />
					</Button>
				</>
			) : null}
		</header>
	);
}
