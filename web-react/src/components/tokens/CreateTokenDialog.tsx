/**
 * 创建 API Token 弹窗。
 *
 * 表单含标题、过期时间、权限三部分，三项全部必填。
 * 成功后不直接关闭，而是弹出 TokenCreatedDialog 展示明文。
 */

import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { useCreateToken } from '@/features/tokens/queries';
import { useTranslation } from '@/i18n/context';
import { toApiTime } from '@/lib/datetime';

import { PermissionPicker } from './PermissionPicker';
import { TokenCreatedDialog } from './TokenCreatedDialog';

interface CreateTokenDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

/** 预设过期选项（天）。用上游已有的 i18n key。 */
const EXPIRY_PRESETS = [
	{ label: 'user.apiTokens.30d', days: 30 },
	{ label: 'user.apiTokens.60d', days: 60 },
	{ label: 'user.apiTokens.90d', days: 90 },
] as const;

/** 自定义过期 key。上游没有对应 key，留中文。 */
const CUSTOM_EXPIRY_KEY = '自定义日期';

function computeExpiry(days: number): string {
	const date = new Date();
	date.setDate(date.getDate() + days);
	return toApiTime(date);
}

export function CreateTokenDialog({ open, onOpenChange }: CreateTokenDialogProps) {
	const t = useTranslation();

	const [title, setTitle] = useState('');
	const [expiryMode, setExpiryMode] = useState<'preset' | 'custom'>('preset');
	const [expiryDays, setExpiryDays] = useState(30);
	const [customExpiryDate, setCustomExpiryDate] = useState('');
	const [permissions, setPermissions] = useState<Record<string, string[]>>({});

	const [titleError, setTitleError] = useState('');
	const [permissionError, setPermissionError] = useState('');
	const [expiryError, setExpiryError] = useState('');

	// 创建成功后的 token 明文，供 TokenCreatedDialog 展示
	const [createdToken, setCreatedToken] = useState<string | null>(null);

	const create = useCreateToken();

	function resetForm() {
		setTitle('');
		setExpiryMode('preset');
		setExpiryDays(30);
		setCustomExpiryDate('');
		setPermissions({});
		setTitleError('');
		setPermissionError('');
		setExpiryError('');
		setCreatedToken(null);
	}

	function validate(): boolean {
		let valid = true;

		if (!title.trim()) {
			setTitleError(t('user.apiTokens.titleRequired') as string);
			valid = false;
		} else {
			setTitleError('');
		}

		if (expiryMode === 'custom') {
			if (!customExpiryDate) {
				setExpiryError('请选择过期日期');
				valid = false;
			} else {
				const selected = new Date(customExpiryDate);
				if (selected <= new Date()) {
					setExpiryError('过期日期必须是将来的日期');
					valid = false;
				} else {
					setExpiryError('');
				}
			}
		} else {
			setExpiryError('');
		}

		const totalActions = Object.values(permissions).reduce((sum, actions) => sum + actions.length, 0);
		if (totalActions === 0) {
			setPermissionError(t('user.apiTokens.permissionRequired') as string);
			valid = false;
		} else {
			setPermissionError('');
		}

		return valid;
	}

	function handleSubmit(event: React.FormEvent) {
		event.preventDefault();
		if (!validate()) return;

		const expiresAt =
			expiryMode === 'custom' ? new Date(customExpiryDate).toISOString() : computeExpiry(expiryDays);

		create.mutate(
			{ title: title.trim(), permissions, expires_at: expiresAt },
			{
				onSuccess: (result) => {
					setCreatedToken(result.token);
				},
			},
		);
	}

	function handleClose() {
		resetForm();
		onOpenChange(false);
	}

	return (
		<>
			<Dialog open={open} onOpenChange={(next) => { if (!next && !createdToken) handleClose(); }}>
				<DialogContent data-testid="create-token-dialog" className="max-w-lg">
					<DialogTitle>{t('user.apiTokens.createAToken') as string}</DialogTitle>

					<form className="mt-4 space-y-4" onSubmit={handleSubmit}>
						<Field
							label={t('user.apiTokens.attributes.title') as string}
							htmlFor="token-title"
							error={titleError}
						>
							<Input
								id="token-title"
								data-testid="token-title-input"
								placeholder={t('user.apiTokens.attributes.titlePlaceholder') as string}
								value={title}
								onChange={(event) => setTitle(event.target.value)}
							/>
						</Field>

						<Field
							label={t('user.apiTokens.attributes.expiresAt') as string}
							htmlFor="token-expiry"
							error={expiryError}
						>
							<div className="space-y-2">
								<div className="flex gap-2">
									{EXPIRY_PRESETS.map((preset) => (
										<Button
											key={preset.days}
											type="button"
											variant={expiryMode === 'preset' && expiryDays === preset.days ? 'default' : 'outline'}
											size="sm"
											data-testid={`token-expiry-${preset.days}d`}
											onClick={() => {
												setExpiryMode('preset');
												setExpiryDays(preset.days);
												setExpiryError('');
											}}
										>
											{t(preset.label) as string}
										</Button>
									))}
									<Button
										type="button"
										variant={expiryMode === 'custom' ? 'default' : 'outline'}
										size="sm"
										data-testid="token-expiry-custom"
										onClick={() => setExpiryMode('custom')}
									>
										{CUSTOM_EXPIRY_KEY}
									</Button>
								</div>

								{expiryMode === 'custom' ? (
									<Input
										id="token-expiry"
										type="date"
										data-testid="token-expiry-custom-date"
										value={customExpiryDate}
										min={new Date().toISOString().split('T')[0]}
										onChange={(event) => {
											setCustomExpiryDate(event.target.value);
											setExpiryError('');
										}}
									/>
								) : null}
							</div>
						</Field>

						<Field
							label={t('user.apiTokens.attributes.permissions') as string}
							htmlFor="permission-picker"
							error={permissionError}
						>
							<PermissionPicker value={permissions} onChange={setPermissions} />
						</Field>

						{create.isError ? (
							<p role="alert" className="text-sm text-xyz-red-6">
								{create.error.message}
							</p>
						) : null}

						<div className="flex justify-end gap-2">
							<Button
								type="button"
								variant="outline"
								data-testid="token-create-cancel"
								onClick={handleClose}
							>
								{t('misc.cancel') as string}
							</Button>
							<Button type="submit" data-testid="token-create-submit" disabled={create.isPending}>
								{create.isPending ? (t('misc.loading') as string) : (t('user.apiTokens.createToken') as string)}
							</Button>
						</div>
					</form>
				</DialogContent>
			</Dialog>

			{createdToken ? (
				<TokenCreatedDialog
					token={createdToken}
					onClose={handleClose}
				/>
			) : null}
		</>
	);
}
