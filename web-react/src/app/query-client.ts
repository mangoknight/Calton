import { QueryClient } from '@tanstack/react-query';

/**
 * 单测里每个用例应各建一个 client（retry:false），避免用例间缓存串味。
 */
export function createQueryClient() {
	return new QueryClient({
		defaultOptions: {
			queries: {
				staleTime: 30_000,
				refetchOnWindowFocus: false,
				retry: 1,
			},
			mutations: { retry: 0 },
		},
	});
}
