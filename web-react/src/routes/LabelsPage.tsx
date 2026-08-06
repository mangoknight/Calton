import { Pencil, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';

import type { Label } from '@/api/labels';
import { DeleteLabelDialog } from '@/components/labels/DeleteLabelDialog';
import { LabelFormDialog } from '@/components/labels/LabelFormDialog';
import { Button } from '@/components/ui/button';
import { useCurrentUser } from '@/features/auth/queries';
import { canManageLabel } from '@/features/labels/permissions';
import { useLabels } from '@/features/labels/queries';
import { useTranslation } from '@/i18n/context';
import { toCssColor } from '@/lib/hex-color';

/**
 * 标签管理页（F10）。
 *
 * ⚠️ 本页列出的是**可见集合**，其中一部分是别人建的、当前用户改不了。
 * 列表内容与按钮可见性由**两个独立的判断**驱动：
 *   列表 = `GET /labels` 返回的全集（不做任何创建者过滤）
 *   按钮 = `canManageLabel`（仅创建者）
 * 合并成一个判断会静默走向两种错误之一，见 `features/labels/permissions.ts`。
 *
 * 把标签挂到任务上是 F08c 的事；那条路径的判据是"可见"而非"创建者"，
 * 所以它**不该**复用 `canManageLabel`。
 */
export function LabelsPage() {
	const query = useLabels();
	const t = useTranslation();
	const { data: currentUser } = useCurrentUser();

	const [formOpen, setFormOpen] = useState(false);
	const [editing, setEditing] = useState<Label | undefined>(undefined);
	const [deleting, setDeleting] = useState<Label | null>(null);

	const labels = query.data?.items ?? [];

	function openCreate() {
		setEditing(undefined);
		setFormOpen(true);
	}

	function openEdit(label: Label) {
		setEditing(label);
		setFormOpen(true);
	}

	return (
		<section className="p-6" data-testid="labels-page">
			<div className="flex items-center justify-between">
				<h1 className="text-lg font-semibold text-foreground">{t('label.title')}</h1>
				<Button size="sm" data-testid="new-label" onClick={openCreate}>
					<Plus aria-hidden />
					{t('label.create.title')}
				</Button>
			</div>

			{query.isPending ? (
				<p className="mt-4 text-sm text-muted-foreground">{t('misc.loading')}</p>
			) : null}

			{query.isError ? (
				<p role="alert" className="mt-4 text-sm text-xyz-red-6">
					{query.error.message}
				</p>
			) : null}

			{query.isSuccess ? (
				<div className="mt-4">
					{labels.length === 0 ? (
						<p className="text-sm text-muted-foreground">{t('label.newCTA')}</p>
					) : (
						<ul className="space-y-2" data-testid="label-list">
							{labels.map((label) => (
								<LabelRow
									key={label.id}
									label={label}
									manageable={canManageLabel(label, currentUser?.id)}
									onEdit={() => openEdit(label)}
									onDelete={() => setDeleting(label)}
								/>
							))}
						</ul>
					)}
				</div>
			) : null}

			{formOpen ? <LabelFormDialog label={editing} onOpenChange={setFormOpen} /> : null}

			{deleting ? (
				<DeleteLabelDialog
					label={deleting}
					onOpenChange={(open) => setDeleting(open ? deleting : null)}
				/>
			) : null}
		</section>
	);
}

function LabelRow({
	label,
	manageable,
	onEdit,
	onDelete,
}: {
	label: Label;
	manageable: boolean;
	onEdit: () => void;
	onDelete: () => void;
}) {
	const swatch = toCssColor(label.hex_color);

	return (
		<li className="flex items-center gap-3 border border-border bg-card p-3">
			{swatch ? (
				<span
					data-testid={`label-swatch-${label.id}`}
					className="size-4 shrink-0 rounded-full border border-border"
					style={{ backgroundColor: swatch }}
					aria-hidden
				/>
			) : null}

			<div className="min-w-0 flex-1">
				<p className="truncate text-sm font-medium text-foreground">{label.title}</p>
				{label.description ? (
					<p className="truncate text-sm text-muted-foreground">{label.description}</p>
				) : null}
			</div>

			{manageable ? (
				<span className="flex shrink-0 items-center gap-1">
					<button
						type="button"
						data-testid={`label-edit-${label.id}`}
						aria-label={`编辑 ${label.title}`}
						onClick={onEdit}
						className="text-muted-foreground hover:text-foreground"
					>
						<Pencil className="size-4" aria-hidden />
					</button>
					<button
						type="button"
						data-testid={`label-delete-${label.id}`}
						aria-label={`删除 ${label.title}`}
						onClick={onDelete}
						className="text-muted-foreground hover:text-xyz-red-6"
					>
						<Trash2 className="size-4" aria-hidden />
					</button>
				</span>
			) : (
				// 别人建的标签仍然列在这里（可见即可用，F08c 的选择器要能选到它），
				// 只是不给改/删入口。说明谁建的，免得用户以为是自己漏了权限。
				<span
					data-testid={`label-foreign-${label.id}`}
					className="shrink-0 text-sm text-muted-foreground"
				>
					由 {label.created_by?.username ?? '其他成员'} 创建，仅创建者可修改
				</span>
			)}
		</li>
	);
}
