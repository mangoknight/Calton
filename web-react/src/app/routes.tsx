import type { RouteObject } from 'react-router-dom';

import { RequireAuth } from '@/components/auth/RequireAuth';
import { AppShell } from '@/components/layout/AppShell';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { BoardPage } from '@/routes/BoardPage';
import { DashboardPage } from '@/routes/DashboardPage';
import { DueSoonPage } from '@/routes/DueSoonPage';
import { HomePage } from '@/routes/HomePage';
import { LabelsPage } from '@/routes/LabelsPage';
import { LoginPage } from '@/routes/LoginPage';
import { FilterPage } from '@/routes/FilterPage';
import { NotFound } from '@/routes/NotFound';
import { ProjectsPage } from '@/routes/ProjectsPage';
import { ProjectViewPage } from '@/routes/ProjectViewPage';
import { TokensPage } from '@/routes/TokensPage';
import { RegisterPage } from '@/routes/RegisterPage';
import { TaskDetailPage } from '@/routes/TaskDetailPage';
import { RouteError } from '@/routes/RouteError';

/**
 * Phase 1 路由面（终稿 §4 的 ★ 页面）。
 * 视图容器（List/Table/Kanban/Gantt 四合一）由 F05a 接手，
 * 届时替换 `/projects/:projectId/:view` 的 element，路径本身不变。
 */
export const routes: RouteObject[] = [
	{
		element: <AuthLayout />,
		errorElement: <RouteError />,
		children: [
			{ path: '/login', element: <LoginPage /> },
			{ path: '/register', element: <RegisterPage /> },
		],
	},
	{
		// 业务页面全部要登录
		element: <RequireAuth />,
		errorElement: <RouteError />,
		children: [
			{
				element: <AppShell />,
				children: [
					{ index: true, element: <HomePage /> },
					{ path: '/dashboard', element: <DashboardPage /> },
					{ path: '/board', element: <BoardPage /> },
					{ path: '/projects', element: <ProjectsPage /> },
					{ path: '/projects/:projectId/:view', element: <ProjectViewPage /> },
					{ path: '/tasks/by/upcoming', element: <DueSoonPage /> },
					{ path: '/tasks/:taskId', element: <TaskDetailPage /> },
					{ path: '/labels', element: <LabelsPage /> },
					{ path: '/tokens', element: <TokensPage /> },
					{ path: '/filters/:filterId', element: <FilterPage /> },
				],
			},
		],
	},
	{
		// 404 不挡在登录闸门后面：未登录访问不存在的路径应该看到 404，
		// 而不是被送去登录页再回来发现还是 404。
		element: <AppShell />,
		errorElement: <RouteError />,
		children: [{ path: '*', element: <NotFound /> }],
	},
];
