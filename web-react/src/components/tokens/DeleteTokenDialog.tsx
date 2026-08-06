/**
 * 删除 API Token 的二次确认弹窗。
 *
 * 文案直接引用上游已有的 i18n key（`user.apiTokens.delete.*`）。
 * 若上游缺对应 key，按项目惯例留中文。
 */

import type { APIToken } from '@/api/tokens';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { useDeleteToken } from '@/features/tokens/queries';
import { useTranslation } from '@/i18n/context';

interface DeleteTokenDialogProps {
	token: APIToken;
	onOpenChange: (open: boolean) => void;
}

export function DeleteTokenDialog({ token, onOpenChange }: DeleteTokenDialogProps) {
	const remove = useDeleteToken();
	const t = useTranslation();

	return (
		<Dialog open onOpenChange={onOpenChange}>
			<DialogContent data-testid="token-delete-dialog">
				<DialogTitle>{t('user.apiTokens.delete.header') as string}</DialogTitle>
				<DialogDescription>
					{/*
					 * 上游 `user.apiTokens.delete.text1` 含 `{token}` 参数。
					 * t() 的 translate 支持模板替换。
					 */}
					{t('user.apiTokens.delete.text1', { token: token.title }) as string}
				</DialogDescription>

				{/*
				 * 上游 `delete.text2` 是 "This will revoke access to all applications or integrations using it. You cannot undo this."
				 */}
				<p className="text-sm text-muted-foreground" data-testid="token-delete-warning">
					{t('user.apiTokens.delete.text2') as string}
				</p>

				{remove.isError ? (
					<p role="alert" className="text-sm text-xyz-red-6">
						{remove.error.message}
					</p>
				) : null}

				<div className="mt-4 flex justify-end gap-2">
					<Button
						type="button"
						variant="outline"
						data-testid="token-delete-cancel"
						onClick={() => onOpenChange(false)}
					>
						{t('misc.cancel') as string}
					</Button>
					<Button
						type="button"
						variant="destructive"
						data-testid="token-delete-confirm"
						disabled={remove.isPending}
						onClick={() => remove.mutate(token.id, { onSuccess: () => onOpenChange(false) })}
					>
						{remove.isPending ? (t('misc.loading') as string) : (t('misc.delete') as string)}
					</Button>
				</div>
			</DialogContent>
		</Dialog>
	);
}
