/**
 * API Token 管理页面（F14）。
 *
 * 列出当前用户的所有 Token，可新建和删除。
 * 新建后弹出 TokenCreatedDialog 展示明文（仅此一次）。
 */

import { KeyRound, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { CreateTokenDialog } from '@/components/tokens/CreateTokenDialog';
import { DeleteTokenDialog } from '@/components/tokens/DeleteTokenDialog';
import { useTokens } from '@/features/tokens/queries';
import { useTranslation } from '@/i18n/context';
import { isZeroTime, parseApiTime } from '@/lib/datetime';
import type { APIToken } from '@/api/tokens';

/** 现在的时间戳，用于计算过期剩余天数。每次渲染重新取。 */
function now() {
	return Date.now();
}

/**
 * 计算过期剩余天数。
 * 返回值：正数 = 还剩 N 天，0 = 今天过期，负数 = 已过期 N 天，null = 永不过期。
 */
function daysUntil(expiresAt: string): number | null {
	if (isZeroTime(expiresAt)) return null;
	const date = parseApiTime(expiresAt);
	if (!date) return null;
	const diff = date.getTime() - now();
	return Math.ceil(diff / 86_400_000);
}

export function TokensPage() {
	const query = useTokens();
	const t = useTranslation();

	const [createOpen, setCreateOpen] = useState(false);
	const [deleting, setDeleting] = useState<APIToken | null>(null);

	const tokens = query.data?.items ?? [];

	return (
		<section className="p-6" data-testid="tokens-page">
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-lg font-semibold text-foreground">
						{t('user.apiTokens.title') as string}
					</h1>
					<p className="mt-1 text-sm text-muted-foreground">
						{t('user.apiTokens.general') as string}
					</p>
				</div>
				<Button size="sm" data-testid="new-token" onClick={() => setCreateOpen(true)}>
					<Plus aria-hidden />
					{t('user.apiTokens.createAToken') as string}
				</Button>
			</div>

			{query.isPending ? (
				<p className="mt-4 text-sm text-muted-foreground">{t('misc.loading') as string}</p>
			) : null}

			{query.isError ? (
				<p role="alert" className="mt-4 text-sm text-xyz-red-6">
					{query.error.message}
				</p>
			) : null}

			{query.isSuccess ? (
				<div className="mt-4">
					{tokens.length === 0 ? (
						<div className="py-12 text-center" data-testid="tokens-empty">
							<KeyRound className="mx-auto size-12 text-muted-foreground" aria-hidden />
							<p className="mt-3 text-sm font-medium text-foreground">
								还没有 API Token
							</p>
							<p className="mt-1 text-sm text-muted-foreground">
								创建 Token 后，可以用它来通过 API 访问您的任务和项目，无需使用您的账户密码。
							</p>
							<Button
								className="mt-4"
								size="sm"
								data-testid="tokens-empty-create"
								onClick={() => setCreateOpen(true)}
							>
								<Plus aria-hidden />
								{t('user.apiTokens.createAToken') as string}
							</Button>
						</div>
					) : (
						<ul className="space-y-2" data-testid="token-list">
							{tokens.map((token) => (
								<TokenRow
									key={token.id}
									token={token}
									onDelete={() => setDeleting(token)}
								/>
							))}
						</ul>
					)}
				</div>
			) : null}

			{createOpen ? <CreateTokenDialog open={createOpen} onOpenChange={setCreateOpen} /> : null}

			{deleting ? (
				<DeleteTokenDialog
					token={deleting}
					onOpenChange={(open) => setDeleting(open ? deleting : null)}
				/>
			) : null}
		</section>
	);
}

function TokenRow({
	token,
	onDelete,
}: {
	token: APIToken;
	onDelete: () => void;
}) {
	const remaining = daysUntil(token.expires_at);

	let expiryLabel: string;
	let expiryColorClass: string;

	if (remaining === null) {
		expiryLabel = '永不过期';
		expiryColorClass = 'text-muted-foreground';
	} else if (remaining <= 0) {
		expiryLabel = `已过期 ${Math.abs(remaining)} 天`;
		expiryColorClass = 'text-xyz-red-6';
	} else if (remaining <= 7) {
		expiryLabel = `${remaining} 天后过期`;
		expiryColorClass = 'text-xyz-orange-warn';
	} else {
		expiryLabel = `${remaining} 天后过期`;
		expiryColorClass = 'text-xyz-green-6';
	}

	// 权限摘要：取每个 group 已选的 action 数，拼成简短的描述
	const permissionItems = Object.entries(token.permissions).map(([group, actions]) => {
		let groupLabel = group;
		// 常用 group 的友好名称
		const GROUP_LABELS: Record<string, string> = {
			tasks: '任务',
			projects: '项目',
			labels: '标签',
			buckets: '看板',
			teams: '团队',
			comments: '评论',
			notifications: '通知',
			saved_filters: '筛选器',
			relations: '关联',
			sharing: '分享',
			webhooks: 'Webhook',
			other: '其他',
		};
		groupLabel = GROUP_LABELS[group] ?? group;
		return `${groupLabel}(${actions.length})`;
	});

	const created = parseApiTime(token.created);
	const createdLabel = created
		? `创建于 ${created.getFullYear()}-${String(created.getMonth() + 1).padStart(2, '0')}-${String(created.getDate()).padStart(2, '0')}`
		: '';

	return (
		<li className="flex items-center gap-3 border border-border bg-card p-3" data-testid={`token-row-${token.id}`}>
			<KeyRound className="size-6 shrink-0 text-muted-foreground" aria-hidden />

			<div className="min-w-0 flex-1">
				<p className="truncate text-sm font-medium text-foreground" data-testid={`token-title-${token.id}`}>
					{token.title}
				</p>
				<p className="truncate text-xs text-muted-foreground" data-testid={`token-permissions-${token.id}`}>
					{permissionItems.join(' · ')}
				</p>
				{createdLabel ? (
					<p className="text-xs text-muted-foreground" data-testid={`token-created-${token.id}`}>
						{createdLabel}
					</p>
				) : null}
			</div>

			<span
				data-testid={`token-expiry-${token.id}`}
				className={`shrink-0 text-xs font-medium ${expiryColorClass}`}
			>
				{expiryLabel}
			</span>

			<button
				type="button"
				data-testid={`token-delete-${token.id}`}
				aria-label={`删除 ${token.title}`}
				onClick={onDelete}
				className="text-muted-foreground hover:text-xyz-red-6"
			>
				<Trash2 className="size-4" aria-hidden />
			</button>
		</li>
	);
}
