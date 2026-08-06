import { useState } from 'react';

import type { Label, LabelWritePayload } from '@/api/labels';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { useCreateLabel, useUpdateLabel } from '@/features/labels/queries';
import { labelWriteErrorMessage } from '@/features/labels/permissions';
import { useTranslation } from '@/i18n/context';
import { hasColor, toApiHexColor, toColorInputValue } from '@/lib/hex-color';

/**
 * 新建 / 编辑标签。
 *
 * ## ⚠️ 这里**故意没有**标题必填校验
 *
 * `PUT /labels` 对空标题返回 **201**（实测，`label.create.empty_title_is_accepted`），
 * 与项目页的"请输入项目名称"是两套口径。前端补一道校验会造成
 * "UI 拦得住、API 拦不住"的行为分歧，而前端不在对拍范围内，没有任何自动化能发现它。
 * 要加校验请走终稿 §5.3 的例外清单。
 *
 * ## ⚠️ 颜色的"未设置"必须单独存
 *
 * `<input type="color">` 没有空值这一档，空串会被浏览器回落成 `#000000`。
 * 如果直接把输入框的值当作用户的选择，那么"给一个没有颜色的标签改个名字"
 * 就会顺手把它染成黑色 —— 而且因为 POST 是全量替换，这个副作用会真的落库。
 * 所以 `hexColor` 存的是 **API 格式且允许空串**的值，输入框的显示值由它派生，
 * 反向只在用户真的操作了颜色控件时才写回。
 */
export function LabelFormDialog({
	label,
	onOpenChange,
}: {
	/** 传 undefined 表示新建。 */
	label?: Label;
	onOpenChange: (open: boolean) => void;
}) {
	const isEdit = label !== undefined;

	const [title, setTitle] = useState(label?.title ?? '');
	const [description, setDescription] = useState(label?.description ?? '');
	// API 格式（不带 #），空串 = 未设置。不要用输入框的值代替它，见组件头注释。
	const [hexColor, setHexColor] = useState(label?.hex_color ?? '');

	const create = useCreateLabel();
	const update = useUpdateLabel();
	const mutation = isEdit ? update : create;
	const t = useTranslation();

	function handleSubmit(event: React.FormEvent) {
		event.preventDefault();

		// 全量替换：三列一次性发全。类型上必填，漏列编译不过。
		const payload: LabelWritePayload = { title, description, hex_color: hexColor };

		if (isEdit) {
			update.mutate({ id: label.id, payload }, { onSuccess: () => onOpenChange(false) });
		} else {
			create.mutate(payload, { onSuccess: () => onOpenChange(false) });
		}
	}

	return (
		<Dialog open onOpenChange={onOpenChange}>
			<DialogContent data-testid="label-form">
				<DialogTitle>{isEdit ? t('label.edit.header') : t('label.create.title')}</DialogTitle>

				<form className="mt-4 space-y-4" onSubmit={handleSubmit}>
					<Field label={t('label.attributes.title')} htmlFor="label-title">
						<Input
							id="label-title"
							data-testid="label-title-input"
							value={title}
							onChange={(event) => setTitle(event.target.value)}
						/>
					</Field>

					<Field label={t('label.attributes.description')} htmlFor="label-description">
						<Input
							id="label-description"
							data-testid="label-description-input"
							value={description}
							onChange={(event) => setDescription(event.target.value)}
						/>
					</Field>

					<Field label={t('label.attributes.color')} htmlFor="label-color">
						<div className="flex items-center gap-2">
							<input
								id="label-color"
								data-testid="label-color-input"
								type="color"
								className="h-9 w-16 border border-input bg-background"
								value={toColorInputValue(hexColor)}
								// 输入框给的是 "#rrggbb"，存进去之前去掉 `#`
								onChange={(event) => setHexColor(toApiHexColor(event.target.value))}
							/>
							{hasColor(hexColor) ? (
								<Button
									type="button"
									variant="outline"
									size="sm"
									data-testid="label-clear-color"
									onClick={() => setHexColor('')}
								>
									清除颜色
								</Button>
							) : (
								<span className="text-sm text-muted-foreground">未设置颜色</span>
							)}
						</div>
					</Field>

					{mutation.isError ? (
						<p role="alert" className="text-sm text-xyz-red-6">
							{labelWriteErrorMessage(mutation.error)}
						</p>
					) : null}

					<div className="flex justify-end gap-2">
						<Button
							type="button"
							variant="outline"
							data-testid="label-form-cancel"
							onClick={() => onOpenChange(false)}
						>
							{t('misc.cancel')}
						</Button>
						<Button type="submit" data-testid="label-form-submit" disabled={mutation.isPending}>
							{mutation.isPending ? '保存中…' : '保存'}
						</Button>
					</div>
				</form>
			</DialogContent>
		</Dialog>
	);
}
