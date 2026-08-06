import { Outlet } from 'react-router-dom';

/** 登录/注册不套 AppShell（无侧边栏）。 */
export function AuthLayout() {
	return (
		<div
			className="flex min-h-screen items-center justify-center bg-xyz-gray-2"
			data-testid="auth-layout"
		>
			<div className="w-full max-w-sm border bg-card p-6">
				<Outlet />
			</div>
		</div>
	);
}
