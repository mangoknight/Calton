import { useQuery } from '@tanstack/react-query';

import type { CaltonError } from '@/api/errors';
import type { Paginated } from '@/api/pagination';
import { listProjectTasks, listTasks, type Task } from '@/api/tasks';
import {
	browserTimezone,
	FAVORITES_PSEUDO_PROJECT_ID,
	THIS_WEEK_FILTER,
	TODAY_FILTER,
} from './home-filters';

/** Home 页一屏够用的条数；这里不做翻页（要翻页请去项目视图）。 */
const HOME_SECTION_SIZE = 20;

export const homeKeys = {
	all: ['home'] as const,
	section: (name: string) => ['home', 'section', name] as const,
	anyTasks: () => ['home', 'any-tasks'] as const,
};

/**
 * 按 datemath 取一段时间内到期的任务。
 *
 * ⚠️ `filter_timezone` **必须发** —— 后端 datemath 的默认时区是 UTC，
 * 不发的话 `now/d` 截的是 UTC 的今天，见 `home-filters.ts` 文件头。
 */
function useFilteredTasks(name: string, filter: string) {
	return useQuery<Paginated<Task>, CaltonError>({
		queryKey: homeKeys.section(name),
		queryFn: () =>
			listTasks({
				per_page: HOME_SECTION_SIZE,
				filter,
				filter_timezone: browserTimezone(),
			}),
	});
}

export function useTodayTasks() {
	return useFilteredTasks('today', TODAY_FILTER);
}

export function useThisWeekTasks() {
	return useFilteredTasks('this-week', THIS_WEEK_FILTER);
}

/**
 * 收藏。走**伪项目 -1** 的项目入口，不是 `filter=is_favorite = true` ——
 * `is_favorite` 不在可筛选白名单里，那么写会 400/4016（见 `api/tasks.ts`）。
 */
export function useFavoriteTasks() {
	return useQuery<Paginated<Task>, CaltonError>({
		queryKey: homeKeys.section('favorites'),
		queryFn: () => listProjectTasks(FAVORITES_PSEUDO_PROJECT_ID, { per_page: HOME_SECTION_SIZE }),
	});
}

/**
 * 这个账号到底有没有任务 —— 用来区分两种空。
 *
 * "今日没有到期任务"（筛选没匹配到，完全正常）与"你还没有任何任务"（该去建一个）
 * 在界面上长得一样，但用户该做的事完全相反。光看分区自己的结果分不出来，
 * 因为两种情况下它都是空的，所以要一条**不带筛选**的信号。
 *
 * 只取 1 条：这里只关心"有没有"，不关心有多少。
 * （不要改用分页头的 `result_count` 去数总数 —— 它是**本页条数**不是总数。）
 */
export function useHasAnyTasks() {
	return useQuery<boolean, CaltonError>({
		queryKey: homeKeys.anyTasks(),
		queryFn: async () => {
			const page = await listTasks({ per_page: 1 });
			return page.items.length > 0;
		},
	});
}
