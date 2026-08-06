import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { useAuthToken } from '@/features/auth/queries';

/**
 * 登录闸门。没有 token 直接跳登录页并记住来处。
 *
 * refresh cookie 失效的场景不在这里判断：client.ts 刷新失败会清掉 token
 * 并调 onUnauthenticated，token 一清空这里立刻跟着跳，两条路径殊途同归。
 */
export function RequireAuth() {
	const token = useAuthToken();
	const location = useLocation();

	if (token === null) {
		const from = `${location.pathname}${location.search}`;
		return <Navigate to={`/login?redirect=${encodeURIComponent(from)}`} replace />;
	}

	return <Outlet />;
}
