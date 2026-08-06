/**
 * 权限选择器（F14）。
 *
 * 以分组 checkbox 的形式展示可用的权限组和操作，每个组折叠/展开。
 *
 * ⚠️ **权限组的来源**：本组件硬编码一组已知的 permission group，
 * 而非从 `GET /routes` 动态获取 —— 那个端点在 Phase 1 还没封装。
 * 如果后端新增了 group，这里需要同步更新。
 *
 * ⚠️ **至少选一个 action**：父组件在校验时确保至少有一个 action 被勾选，
 * 否则创建出的 token 没有任何用途。
 */

import { ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';

import { useTranslation } from '@/i18n/context';
import { cn } from '@/lib/utils';

/**
 * 一个权限组的定义。
 * 每个 action 的 label 使用 i18n key（与上游 `routes.<group>.<action>` 模式一致）。
 */
interface PermissionGroup {
	group: string;
	labelKey: string;
	actions: Array<{ action: string; labelKey: string }>;
}

const PERMISSION_GROUPS: PermissionGroup[] = [
	{
		group: 'tasks',
		labelKey: 'task.tasks',
		actions: [
			{ action: 'read_all', labelKey: 'tasks.read_all' },
			{ action: 'create', labelKey: 'tasks.create' },
			{ action: 'update', labelKey: 'tasks.update' },
			{ action: 'delete', labelKey: 'tasks.delete' },
		],
	},
	{
		group: 'projects',
		labelKey: 'project.projects',
		actions: [
			{ action: 'read_all', labelKey: 'projects.read_all' },
			{ action: 'create', labelKey: 'projects.create' },
			{ action: 'update', labelKey: 'projects.update' },
			{ action: 'delete', labelKey: 'projects.delete' },
		],
	},
	{
		group: 'labels',
		labelKey: 'label.title',
		actions: [
			{ action: 'read_all', labelKey: 'labels.read_all' },
			{ action: 'create', labelKey: 'labels.create' },
			{ action: 'update', labelKey: 'labels.update' },
			{ action: 'delete', labelKey: 'labels.delete' },
		],
	},
	{
		group: 'buckets',
		labelKey: 'bucket.title',
		actions: [
			{ action: 'read_all', labelKey: 'buckets.read_all' },
			{ action: 'create', labelKey: 'buckets.create' },
			{ action: 'update', labelKey: 'buckets.update' },
			{ action: 'delete', labelKey: 'buckets.delete' },
		],
	},
	{
		group: 'teams',
		labelKey: 'team.title',
		actions: [
			{ action: 'read_all', labelKey: 'teams.read_all' },
			{ action: 'create', labelKey: 'teams.create' },
			{ action: 'update', labelKey: 'teams.update' },
			{ action: 'delete', labelKey: 'teams.delete' },
		],
	},
	{
		group: 'comments',
		labelKey: 'comment.title',
		actions: [
			{ action: 'read_all', labelKey: 'comments.read_all' },
			{ action: 'create', labelKey: 'comments.create' },
			{ action: 'update', labelKey: 'comments.update' },
			{ action: 'delete', labelKey: 'comments.delete' },
		],
	},
	{
		group: 'notifications',
		labelKey: 'notification.title',
		actions: [{ action: 'read_all', labelKey: 'notifications.read_all' }],
	},
	{
		group: 'saved_filters',
		labelKey: 'filters.title',
		actions: [
			{ action: 'read_one', labelKey: 'saved_filters.view' },
			{ action: 'create', labelKey: 'saved_filters.create' },
			{ action: 'update', labelKey: 'saved_filters.update' },
			{ action: 'delete', labelKey: 'saved_filters.delete' },
		],
	},
	{
		group: 'relations',
		labelKey: 'relation.title',
		actions: [
			{ action: 'read_all', labelKey: 'relations.read_all' },
			{ action: 'create', labelKey: 'relations.create' },
			{ action: 'delete', labelKey: 'relations.delete' },
		],
	},
];

export interface PermissionPickerProps {
	value: Record<string, string[]>;
	onChange: (permissions: Record<string, string[]>) => void;
	error?: string;
}

export function PermissionPicker({ value, onChange, error }: PermissionPickerProps) {
	const t = useTranslation();
	const [openGroups, setOpenGroups] = useState<Set<string>>(
		() => new Set(Object.keys(value).length > 0 ? Object.keys(value) : ['tasks', 'projects']),
	);

	function toggleGroup(group: string) {
		setOpenGroups((prev) => {
			const next = new Set(prev);
			if (next.has(group)) next.delete(group);
			else next.add(group);
			return next;
		});
	}

	function isActionSelected(group: string, action: string): boolean {
		return (value[group] ?? []).includes(action);
	}

	function toggleAction(group: string, action: string) {
		const current = value[group] ?? [];
		const next = isActionSelected(group, action)
			? current.filter((a) => a !== action)
			: [...current, action];

		const newPermissions = { ...value };
		if (next.length === 0) {
			delete newPermissions[group];
		} else {
			newPermissions[group] = next;
		}
		onChange(newPermissions);
	}

	const errorId = 'permission-picker-error';

	return (
		<div className="space-y-2" data-testid="permission-picker" role="group" aria-label="Permissions">
			{PERMISSION_GROUPS.map((groupDef) => {
				const isOpen = openGroups.has(groupDef.group);
				const selectedCount = (value[groupDef.group] ?? []).length;

				return (
					<div key={groupDef.group} className="border border-border bg-card" data-testid={`perm-group-${groupDef.group}`}>
						<button
							type="button"
							data-testid={`perm-group-toggle-${groupDef.group}`}
							className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium text-foreground hover:bg-accent"
							onClick={() => toggleGroup(groupDef.group)}
						>
							{isOpen ? <ChevronDown className="size-4 shrink-0" aria-hidden /> : <ChevronRight className="size-4 shrink-0" aria-hidden />}
							{t(groupDef.labelKey) as string}
							{selectedCount > 0 ? (
								<span className="ml-auto text-xs text-muted-foreground">
									{selectedCount}
								</span>
							) : null}
						</button>

						{isOpen ? (
							<div className="flex flex-wrap gap-3 border-t border-border px-3 py-2" data-testid={`perm-actions-${groupDef.group}`}>
								{groupDef.actions.map((actionDef) => {
									const checked = isActionSelected(groupDef.group, actionDef.action);
									return (
										<label
											key={actionDef.action}
											data-testid={`perm-action-${groupDef.group}-${actionDef.action}`}
											className={cn(
												'flex cursor-pointer items-center gap-1.5 text-sm',
												checked ? 'text-foreground' : 'text-muted-foreground',
											)}
										>
											<input
												type="checkbox"
												className="size-4 accent-xyz-blue-6"
												checked={checked}
												onChange={() => toggleAction(groupDef.group, actionDef.action)}
											/>
											{/* 上游没有每个 action 的独立翻译 key，直接用英文 action 名 */}
											{actionDef.action}
										</label>
									);
								})}
							</div>
						) : null}
					</div>
				);
			})}

			{error ? (
				<p
					id={errorId}
					data-testid={errorId}
					role="alert"
					className="text-sm text-xyz-red-6"
				>
					{error}
				</p>
			) : null}
		</div>
	);
}
