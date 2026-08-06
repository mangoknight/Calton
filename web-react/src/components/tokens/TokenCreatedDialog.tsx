/**
 * 创建 Token 成功后展示明文的弹窗。
 *
 * ⚠️ **明文只出现一次**，关闭后不可恢复。因此：
 * - 禁止点击遮罩关闭（`onInteractOutside` 阻止）
 * - 禁止按 Esc 关闭（`onEscapeKeyDown` 阻止）
 * - 必须点击"我已复制，关闭"按钮才能关闭
 * - 提供一键复制按钮
 */

import { Check, Copy, KeyRound } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { useTranslation } from '@/i18n/context';

interface TokenCreatedDialogProps {
	token: string;
	onClose: () => void;
}

export function TokenCreatedDialog({ token, onClose }: TokenCreatedDialogProps) {
	const t = useTranslation();
	const [copied, setCopied] = useState(false);

	async function handleCopy() {
		try {
			await navigator.clipboard.writeText(token);
			setCopied(true);
			setTimeout(() => setCopied(false), 2000);
		} catch {
			// 剪贴板 API 可能因权限被拒绝（非 HTTPS / 非安全上下文）。
			// 不做 fallback —— 用户仍可手动选中文字复制。
		}
	}

	return (
		<Dialog open onOpenChange={(next) => { if (!next) onClose(); }}>
			{/*
			 * Radix Dialog 默认允许点击遮罩和按 Esc 关闭。
			 * 这里用 onInteractOutside 阻止遮罩关闭，用 stopPropagation 阻止 Esc 关闭。
			 * 注意：onEscapeKeyDown 阻止默认行为后，Dialog 内部不会关闭。
			 */}
			<DialogContent
				data-testid="token-created-dialog"
				className="max-w-md"
				onInteractOutside={(event) => event.preventDefault()}
				onEscapeKeyDown={(event) => event.preventDefault()}
			>
				<div className="text-center">
					<KeyRound className="mx-auto size-12 text-xyz-blue-6" aria-hidden />
					<DialogTitle className="mt-3 text-lg font-semibold text-foreground">
						{t('user.apiTokens.tokenCreatedSuccess') as string}
					</DialogTitle>
				</div>

				{/*
				 * ⚠️ 上游的 `tokenCreatedNotSeeAgain` 值是 "Store it in a secure location, you won't see it again!"
				 * 翻译后是"将其存储在一个安全的位置，你不会再看到它了！"
				 * 直接引用上游 key。
				 */}
				<p className="text-center text-sm text-xyz-orange-warn" data-testid="token-created-warning">
					{t('user.apiTokens.tokenCreatedNotSeeAgain') as string}
				</p>

				<div className="mt-2 flex gap-2">
					<Input
						data-testid="token-created-value"
						value={token}
						readOnly
						className="flex-1 font-mono text-xs"
						onFocus={(event) => event.target.select()}
					/>
					<Button
						type="button"
						variant="outline"
						size="sm"
						data-testid="token-created-copy"
						className="shrink-0"
						onClick={handleCopy}
					>
						{copied ? <Check className="size-4 text-xyz-green-6" aria-hidden /> : <Copy className="size-4" aria-hidden />}
						{copied ? '已复制' : '复制'}
					</Button>
				</div>

				{/*
				 * 额外提示：如果剪贴板不可用，提醒用户手动选中复制。
				 * 上游没有对应 key，留中文。
				 */}
				<p className="text-xs text-muted-foreground" data-testid="token-created-hint">
					请立即复制并妥善保管。关闭此窗口后将无法再次查看完整的 Token。
				</p>

				<div className="mt-2 flex justify-center">
					<Button
						type="button"
						variant="default"
						data-testid="token-created-close"
						onClick={onClose}
					>
						我已复制，关闭
					</Button>
				</div>
			</DialogContent>
		</Dialog>
	);
}
