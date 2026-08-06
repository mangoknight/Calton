import type { Label } from '@/api/labels';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { labelWriteErrorMessage } from '@/features/labels/permissions';
import { useDeleteLabel } from '@/features/labels/queries';
import { useTranslation } from '@/i18n/context';

/**
 * 删除标签的二次确认。
 *
 * 删标签**不删任务** —— 它只是把这个标签从所有挂着它的任务上摘掉。
 * 文案要说清楚这一点：不说的话用户会以为贴了这个标签的任务也会没，从而不敢删。
 */
export function DeleteLabelDialog({
	label,
	onOpenChange,
}: {
	label: Label;
	onOpenChange: (open: boolean) => void;
}) {
	const remove = useDeleteLabel();
	const t = useTranslation();

	return (
		<Dialog open onOpenChange={onOpenChange}>
			<DialogContent data-testid="label-delete-dialog">
				{/*
				 * ⚠️ 标题与下面这段说明**上游没有对应 key**（上游只有 `label.deleteSuccess`
				 * 这个 toast 文案，没有删除确认弹窗的标题/正文）。留中文是有意的：
				 * 编一个上游没有的 key 会让 lang-parity 守卫红。
				 * 而且这段正文承载的是**实测结论**（删标签不删任务），比措辞本身更要紧。
				 */}
				<DialogTitle>删除标签</DialogTitle>
				<DialogDescription>
					确定删除标签「{label.title}」吗？它会从所有使用它的任务上被摘掉，但任务本身不会被删除。
					此操作不可恢复。
				</DialogDescription>

				{remove.isError ? (
					<p role="alert" className="mt-3 text-sm text-xyz-red-6">
						{labelWriteErrorMessage(remove.error)}
					</p>
				) : null}

				<div className="mt-4 flex justify-end gap-2">
					<Button
						type="button"
						variant="outline"
						data-testid="label-delete-cancel"
						onClick={() => onOpenChange(false)}
					>
						{t('misc.cancel')}
					</Button>
					<Button
						type="button"
						variant="destructive"
						data-testid="label-delete-confirm"
						disabled={remove.isPending}
						onClick={() => remove.mutate(label.id, { onSuccess: () => onOpenChange(false) })}
					>
						{remove.isPending ? t('misc.loading') : t('misc.delete')}
					</Button>
				</div>
			</DialogContent>
		</Dialog>
	);
}
