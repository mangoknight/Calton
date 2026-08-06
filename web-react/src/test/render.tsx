import { render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';

import { apiClient } from '@/api/client';
import { AppProviders } from '@/app/providers';
import { createQueryClient } from '@/app/query-client';
import { routes } from '@/app/routes';

export const TEST_TOKEN = 'test-jwt';

interface RenderAppOptions {
	/** 传 null 模拟未登录。默认已登录 —— 业务页面都在登录闸门后面。 */
	token?: string | null;
}

/** 按真实路由表在内存路由里渲染整个应用，起点由 `path` 指定。 */
export function renderApp(path = '/', { token = TEST_TOKEN }: RenderAppOptions = {}) {
	apiClient.tokens.set(token);

	const router = createMemoryRouter(routes, { initialEntries: [path] });
	const queryClient = createQueryClient();
	queryClient.setDefaultOptions({ queries: { retry: false } });

	return {
		router,
		...render(
			<AppProviders queryClient={queryClient}>
				<RouterProvider router={router} />
			</AppProviders>,
		),
	};
}

/** 只需要 Provider 包一层时用（不带路由）。 */
export function renderWithProviders(ui: ReactNode) {
	const queryClient = createQueryClient();
	queryClient.setDefaultOptions({ queries: { retry: false } });
	return render(<AppProviders queryClient={queryClient}>{ui}</AppProviders>);
}
