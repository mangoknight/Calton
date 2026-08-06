import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import * as React from 'react';

import { createQueryClient } from './query-client';
import { I18nProvider } from '@/i18n/I18nProvider';
import { useUIStore } from '@/store/ui';

/** .dark 必须挂在 <html> 上：Radix Portal 渲染到 body，挂内层 div 对它无效。 */
function ThemeSync() {
	const theme = useUIStore((s) => s.theme);
	React.useEffect(() => {
		document.documentElement.classList.toggle('dark', theme === 'dark');
	}, [theme]);
	return null;
}

export function AppProviders({
	children,
	queryClient,
}: {
	children: React.ReactNode;
	queryClient?: QueryClient;
}) {
	const [client] = React.useState(() => queryClient ?? createQueryClient());
	return (
		<QueryClientProvider client={client}>
			<ThemeSync />
			{/*
			 * i18n 挂在这里而不是各页面自己包 —— `useI18n` 拿不到 Provider 会**抛错**，
			 * 所以"忘了接线"会在第一次渲染就炸，而不是悄悄显示成英文
			 * （那与"用户浏览器是英文"长得一样，没人会去查）。
			 */}
			<I18nProvider>{children}</I18nProvider>
		</QueryClientProvider>
	);
}
