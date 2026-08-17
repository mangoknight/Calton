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
				// 切菜单/进页面（组件挂载）一律后台重取，不吃 staleTime 的缓存：
				// 各页面查各自的 key，且各特性的 mutation 只失效自己的 key（如任务详情
				// 编辑不会失效看板/管理面板用的 boardKeys.allTasks，也不失效项目页的
				// projectTree），跨页存在失效缺口 → 换页看到旧数据。'always' 让每次进页
				// 都拉一次新的；有缓存时先秒显旧数据、后台拉到再替换，不闪 loading。
				// staleTime 30s 仍保留，用于同一页内重复挂载/并发同 key 的去抖。
				refetchOnMount: 'always',
				retry: 1,
			},
			mutations: { retry: 0 },
		},
	});
}
