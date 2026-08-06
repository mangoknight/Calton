import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';

import { apiClient } from '@/api/client';
import { AppProviders } from '@/app/providers';
import { routes } from '@/app/routes';

import './index.css';

const router = createBrowserRouter(routes);

// 刷新失败或二次 401 → 已登出，回登录页。带上来处，F03 登录后跳回。
apiClient.setOnUnauthenticated(() => {
	const from = `${window.location.pathname}${window.location.search}`;
	if (!from.startsWith('/login')) {
		void router.navigate(`/login?redirect=${encodeURIComponent(from)}`);
	}
});

const container = document.getElementById('root');
if (!container) throw new Error('#root not found');

createRoot(container).render(
	<StrictMode>
		<AppProviders>
			<RouterProvider router={router} />
		</AppProviders>
	</StrictMode>,
);
