import { useState } from 'react';

import type { Bucket } from '@/api/buckets';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { Field } from '@/components/ui/field';
import { useTranslation } from '@/i18n/context';
import { Input } from '@/components/ui/input';
import type { useCreateBucket, useDeleteBucket, useUpdateBucket } from './bucket-queries';

/** 新建与编辑共用一个表单：字段完全相同，分成两个组件只会让两边慢慢长歪。 */
export function BucketFormDialog({
	open,
	bucket,
	mutation,
	onOpenChange,
}: {
	open: boolean;
	/** 传 null 表示新建。 */
	bucket: Bucket | null;
	mutation: ReturnType<typeof useCreateBucket> | ReturnType<typeof useUpdateBucket>;
	onOpenChange: (open: boolean) => void;
}) {
	const t = useTranslation();
	const [title, setTitle] = useState(bucket?.title ?? '');
	const [limit, setLimit] = useState(String(bucket?.limit ?? 0));
	const [titleError, setTitleError] = useState<string | null>(null);

	if (!open) return null;

	const isEdit = bucket !== null;

	function submit(event: React.FormEvent) {
		event.preventDefault();

		const trimmed = title.trim();
		if (!trimmed) {
			// 后端 valid:"required" 也会拦，但那要等一个来回，且报的是英文
			setTitleError('请填写标题');
			return;
		}
		setTitleError(null);

		// 空字符串/非数字一律当 0（不限），而不是 NaN —— NaN 发出去后端会 400
		const parsedLimit = Number.parseInt(limit, 10);
		const payload = {
			title: trimmed,
			limit: Number.isNaN(parsedLimit) || parsedLimit < 0 ? 0 : parsedLimit,
		};

		const options = { onSuccess: () => onOpenChange(false) };
		if (isEdit) {
			(mutation as ReturnType<typeof useUpdateBucket>).mutate(
				{ id: bucket.id, ...payload },
				options,
			);
		} else {
			(mutation as ReturnType<typeof useCreateBucket>).mutate(payload, options);
		}
	}

	return (
		<Dialog open onOpenChange={onOpenChange}>
			<DialogContent data-testid="bucket-form">
				{/* ⚠️ 上游只有"新建"这个 key（`addBucket`），**没有编辑桶的标题 key**，
				    所以编辑那侧留中文。不为了对称去编一个上游没有的 key。 */}
				<DialogTitle className="ink-heading text-lg">
					{isEdit ? '编辑列' : t('project.kanban.addBucket')}
				</DialogTitle>
				<form onSubmit={submit} className="mt-4 space-y-4">
					<Field label={t('project.title')} htmlFor="bucket-title" error={titleError ?? undefined}>
						<Input
							id="bucket-title"
							data-testid="bucket-title-input"
							value={title}
							onChange={(event) => setTitle(event.target.value)}
							aria-invalid={titleError ? true : undefined}
						/>
					</Field>

					<Field label="容量上限" htmlFor="bucket-limit">
						<Input
							id="bucket-limit"
							data-testid="bucket-limit-input"
							type="number"
							min={0}
							value={limit}
							onChange={(event) => setLimit(event.target.value)}
						/>
						<p className="text-xs text-muted-foreground">填 0 表示不限制。</p>
					</Field>

					{mutation.isError ? (
						<p role="alert" className="text-sm text-xyz-red-6">
							{mutation.error.message}
						</p>
					) : null}

					<div className="flex justify-end gap-2">
						<Button
							type="button"
							variant="outline"
							data-testid="bucket-form-cancel"
							onClick={() => onOpenChange(false)}
						>
							{t('misc.cancel')}
						</Button>
						<Button type="submit" data-testid="bucket-form-submit" disabled={mutation.isPending}>
							{mutation.isPending ? t('misc.saving') : t('misc.save')}
						</Button>
					</div>
				</form>
			</DialogContent>
		</Dialog>
	);
}

/**
 * 删除确认。
 *
 * ★ 文案必须说清楚**任务不会被删**：后端把桶里的任务搬到该视图的默认桶
 * （kanban.go:414-420）。不说的话用户会以为任务跟着一起没了，于是不敢删空不掉的列——
 * 这跟项目删除是完全相反的语义（那边是硬删、不可恢复），更要写明白。
 */
export function DeleteBucketDialog({
	bucket,
	isLastBucket,
	mutation,
	onOpenChange,
}: {
	bucket: Bucket | null;
	isLastBucket: boolean;
	mutation: ReturnType<typeof useDeleteBucket>;
	onOpenChange: (open: boolean) => void;
}) {
	const t = useTranslation();

	if (!bucket) return null;

	return (
		<Dialog open onOpenChange={onOpenChange}>
			<DialogContent data-testid="bucket-delete-dialog">
				<DialogTitle className="ink-heading text-lg">{t('project.kanban.deleteHeaderBucket')}</DialogTitle>
				<DialogDescription>
					{isLastBucket
						? t('project.kanban.deleteLast')
						: `确定删除「${bucket.title}」吗？列里的 ${bucket.count} 个任务不会被删除，会移动到该看板的默认列。`}
				</DialogDescription>

				{mutation.isError ? (
					<p role="alert" className="mt-3 text-sm text-xyz-red-6">
						{mutation.error.message}
					</p>
				) : null}

				<div className="mt-4 flex justify-end gap-2">
					<Button
						type="button"
						variant="outline"
						data-testid="bucket-delete-dismiss"
						onClick={() => onOpenChange(false)}
					>
						{isLastBucket ? '知道了' : t('misc.cancel')}
					</Button>
					{isLastBucket ? null : (
						<Button
							type="button"
							variant="destructive"
							data-testid="bucket-delete-confirm"
							disabled={mutation.isPending}
							onClick={() => mutation.mutate(bucket.id, { onSuccess: () => onOpenChange(false) })}
						>
							{mutation.isPending ? t('misc.loading') : t('misc.delete')}
						</Button>
					)}
				</div>
			</DialogContent>
		</Dialog>
	);
}
