import { useState } from 'react';

import type { AssignableUser } from '@/api/assignees';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useTranslation } from '@/i18n/context';
import {
	useAssignUser,
	useProjectMembers,
	useTaskAssignees,
	useUnassignUser,
} from './relation-queries';

/**
 * 指派选择器（F08c）。指派/取消即时生效（点击即存，不需要另外保存）。
 *
 * ## ⚠️ 候选来自**项目成员**，不是全局用户搜索
 *
 * 全局 `GET /users?s=` 受**可发现性**限制：只有精确用户名不受限，子串匹配要
 * `discoverable_by_name=1`（默认 0）。于是打"don"搜不到"dongxp"，得记住完整
 * 用户名才能指派 —— 体验很差。改用 `GET /projects/{id}/projectusers`（项目成员，
 * 不受该限制），成员直接列出、搜索框在本地过滤，点一下就能选。
 *
 * ## ⚠️ 发出去的是**数字 user id**
 *
 * `PUT /tasks/{id}/assignees` 的 body 是 `{user_id: 901}`，DELETE 的路径段也是数字。
 */
export function AssigneeSelector({
	taskId,
	projectId,
	disabled = false,
}: {
	taskId: number;
	projectId?: number;
	disabled?: boolean;
}) {
	const [search, setSearch] = useState('');
	const assigned = useTaskAssignees(taskId);
	const members = useProjectMembers(projectId);
	const assign = useAssignUser(taskId);
	const unassign = useUnassignUser(taskId);

	const assignedUsers = assigned.data?.items ?? [];
	const assignedIds = new Set(assignedUsers.map((user) => user.id));
	const term = search.trim().toLowerCase();
	const candidates = (members.data?.items ?? [])
		.filter((user) => !assignedIds.has(user.id))
		.filter(
			(user) =>
				!term ||
				displayName(user).toLowerCase().includes(term) ||
				(user.username ?? '').toLowerCase().includes(term),
		);

	const t = useTranslation();
	const busy = disabled || assign.isPending || unassign.isPending;
	const canPickMembers = typeof projectId === 'number' && projectId > 0;

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
					{!canPickMembers ? (
						<p data-testid="assignee-hint" className="text-sm text-muted-foreground">
							该任务没有可指派的项目成员。
						</p>
					) : (
						<>
							<Input
								data-testid="assignee-search"
								aria-label={t('task.assignee.placeholder')}
								placeholder="按名字筛选成员…"
								value={search}
								onChange={(event) => setSearch(event.target.value)}
							/>

							{assign.isError || unassign.isError ? (
								<p role="alert" data-testid="assignee-error" className="text-sm text-red-600">
									{(assign.error ?? unassign.error)?.message}
								</p>
							) : null}

							<ul className="flex flex-wrap gap-2" data-testid="user-results">
								{members.isPending ? (
									<li className="text-sm text-muted-foreground">{t('misc.loading')}</li>
								) : candidates.length === 0 ? (
									<li data-testid="no-candidates" className="text-sm text-muted-foreground">
										{term ? '没有匹配的成员' : '项目里没有可指派的其他成员'}
									</li>
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
						</>
					)}
				</div>
			)}
		</section>
	);
}

function displayName(user: AssignableUser): string {
	return user.name || user.username || `#${user.id}`;
}
