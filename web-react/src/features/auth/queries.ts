import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';

import {
	getCurrentUser,
	login,
	logout,
	register,
	type CurrentUser,
	type LoginPayload,
	type RegisterPayload,
} from '@/api/auth';
import { apiClient } from '@/api/client';
import { CaltonError } from '@/api/errors';

export const authKeys = {
	currentUser: ['auth', 'current-user'] as const,
};

/** token 变化要驱动重渲染（登录/登出/刷新后拿到新 token）。 */
export function useAuthToken(): string | null {
	return useSyncExternalStore(
		(onChange) => apiClient.tokens.subscribe(onChange),
		() => apiClient.tokens.get(),
		() => null,
	);
}

export function useCurrentUser() {
	const token = useAuthToken();

	return useQuery<CurrentUser, CaltonError>({
		queryKey: authKeys.currentUser,
		queryFn: () => getCurrentUser(),
		// 没 token 就别打了，401 除了刷屏没有任何用
		enabled: token !== null,
		// 401 已经由 client 处理（刷新+登出），这里重试只会拖慢跳转
		retry: (failureCount, error) => !error.isUnauthenticated && failureCount < 1,
		staleTime: 5 * 60_000,
	});
}

export function useLogin() {
	const queryClient = useQueryClient();

	return useMutation<unknown, CaltonError, LoginPayload>({
		mutationFn: (payload) => login(payload),
		onSuccess: () => {
			// 换了身份，旧缓存一律作废
			void queryClient.invalidateQueries();
		},
	});
}

export function useRegister() {
	return useMutation<CurrentUser, CaltonError, RegisterPayload>({
		mutationFn: (payload) => register(payload),
	});
}

export function useLogout() {
	const queryClient = useQueryClient();

	return useMutation({
		mutationFn: () => logout(),
		onSettled: () => queryClient.clear(),
	});
}
