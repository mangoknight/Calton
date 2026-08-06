import { useState } from 'react';

import type { AssignableUser } from '@/api/assignees';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useTranslation } from '@/i18n/context';
import {
	useAssignUser,
	useTaskAssignees,
	useUnassignUser,
	useUserSearch,
} from './relation-queries';

/**
 * 指派选择器（F08c）。指派/取消即时生效。
 *
 * ## ⚠️ 发出去的是**数字 user id**
 *
 * `PUT /tasks/{id}/assignees` 的 body 是 `{user_id: 901}`，DELETE 的路径段也是数字。
 * 收用户名的是 **filter DSL 里的 `assignees`**（F11a/F12），那边 JOIN 了 users 表按
 * `username` 比对。两边搞反都表现为"没报错但没效果"，详见 `api/assignees.ts`。
 *
 * ## ⚠️ 必须先输入搜索词
 *
 * `GET /users` 空搜索时后端返回 `null`（不是"全部用户"），
 * 所以空输入不发请求，界面上直接说明要先打字，而不是显示一个空列表让人以为没人。
 */
export function AssigneeSelector({
	taskId,
	disabled = false,
}: {
	taskId: number;
	disabled?: boolean;
}) {
	const [search, setSearch] = useState('');
	const assigned = useTaskAssignees(taskId);
	const results = useUserSearch(search);
	const assign = useAssignUser(taskId);
	const unassign = useUnassignUser(taskId);

	const assignedUsers = assigned.data?.items ?? [];
	const assignedIds = new Set(assignedUsers.map((user) => user.id));
	const candidates = (results.data?.items ?? []).filter((user) => !assignedIds.has(user.id));

	const t = useTranslation();
	const busy = disabled || assign.isPending || unassign.isPending;
	const hasSearch = search.trim().length > 0;

	return (
		<section className="space-y-2" data-testid="assignee-selector">
			<h2 className="text-sm font-medium text-foreground">{t('task.attributes.assignees')}</h2>

			<ul className="flex flex-wrap gap-2" data-testid="assigned-users">
				{assignedUsers.length === 0 ? (
					<li data-testid="assignees-empty" className="text-sm text-muted-foreground">
						还没有指派任何人
					</li>
				) : (
					assignedUsers.map((user) => (
						<li key={user.id} data-testid="assigned-user" data-user-id={user.id}>
							<span className="inline-flex items-center gap-1 rounded bg-muted px-2 py-0.5 text-sm">
								{displayName(user)}
								{disabled ? null : (
									<button
										type="button"
										data-testid={`assignee-remove-${user.id}`}
										aria-label={`取消指派 ${displayName(user)}`}
										disabled={busy}
										className="text-muted-foreground hover:text-foreground"
										// ⚠️ 数字 id，不是 username
										onClick={() => unassign.mutate(user.id)}
									>
										×
									</button>
								)}
							</span>
						</li>
					))
				)}
			</ul>

			{disabled ? null : (
				<div className="space-y-2">
					<Input
						data-testid="assignee-search"
						aria-label={t('task.assignee.placeholder')}
						placeholder={t('task.assignee.placeholder')}
						value={search}
						onChange={(event) => setSearch(event.target.value)}
					/>

					{assign.isError || unassign.isError ? (
						<p role="alert" data-testid="assignee-error" className="text-sm text-xyz-red-6">
							{(assign.error ?? unassign.error)?.message}
						</p>
					) : null}

					{!hasSearch ? (
						<p data-testid="assignee-hint" className="text-sm text-muted-foreground">
							输入用户名开始搜索。
						</p>
					) : (
						<ul className="flex flex-wrap gap-2" data-testid="user-results">
							{results.isPending ? (
								<li className="text-sm text-muted-foreground">{t('misc.loading')}</li>
							) : candidates.length === 0 ? (
								<li className="text-sm text-muted-foreground">没有匹配的用户</li>
							) : (
								candidates.map((user) => (
									<li key={user.id}>
										<Button
											type="button"
											variant="outline"
											size="sm"
											disabled={busy}
											data-testid="user-result"
											data-user-id={user.id}
											// ⚠️ 数字 id，不是 username
											onClick={() => assign.mutate(user.id)}
										>
											{displayName(user)}
										</Button>
									</li>
								))
							)}
						</ul>
					)}
				</div>
			)}
		</section>
	);
}

function displayName(user: AssignableUser): string {
	return user.name || user.username || `#${user.id}`;
}
