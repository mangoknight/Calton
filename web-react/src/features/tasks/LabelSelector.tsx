import { useState } from 'react';

import type { Label } from '@/api/labels';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useTranslation } from '@/i18n/context';
import { useAddLabel, useAllLabels, useRemoveLabel, useTaskLabels } from './relation-queries';

/**
 * 标签选择器（F08c）。打/摘标签即时生效。
 *
 * ## ⚠️ 这里**没有**权限过滤，是有意的
 *
 * `GET /labels` 返回什么就是能用什么，**包括别人建的标签**（挂到自己任务上实测 201）。
 * 若在这里加一层"只显示我能管理的标签"，共享标签会从选择器里消失 ——
 * 用户明明能用却选不到，而且没有任何提示说明原因。
 * "能改/能删"是另一档权限，属于 F10 管理页，别把那边的判断挪过来。
 *
 * 本组件也**不负责新建标签**（那是 F10）：不建标签就不需要颜色选择器，
 * 也就绕开了 `<input type="color">` 没有空值档、会把 `hex_color` 从 `""`
 * 写成 `"000000"` 的那个坑。
 */
export function LabelSelector({
	taskId,
	disabled = false,
}: {
	taskId: number;
	disabled?: boolean;
}) {
	const [search, setSearch] = useState('');
	const attached = useTaskLabels(taskId);
	const all = useAllLabels(search);
	const add = useAddLabel(taskId);
	const remove = useRemoveLabel(taskId);

	const attachedLabels = attached.data?.items ?? [];
	const attachedIds = new Set(attachedLabels.map((label) => label.id));
	// 只把已挂上的排除掉 —— 这是去重，不是权限过滤
	const available = (all.data?.items ?? []).filter((label) => !attachedIds.has(label.id));

	const t = useTranslation();
	const busy = disabled || add.isPending || remove.isPending;

	return (
		<section className="space-y-2" data-testid="label-selector">
			<h2 className="ink-heading text-base">{t('task.attributes.labels')}</h2>

			<ul className="flex flex-wrap gap-2" data-testid="attached-labels">
				{attachedLabels.length === 0 ? (
					<li data-testid="labels-empty" className="text-sm text-muted-foreground">
						还没有标签
					</li>
				) : (
					attachedLabels.map((label) => (
						<li key={label.id} data-testid="attached-label" data-label-id={label.id}>
							<span className="inline-flex items-center gap-1 rounded bg-muted px-2 py-0.5 text-sm">
								<LabelSwatch label={label} />
								{label.title}
								{disabled ? null : (
									<button
										type="button"
										data-testid={`label-remove-${label.id}`}
										aria-label={t('task.label.removeLabel', { label: label.title })}
										disabled={busy}
										className="text-muted-foreground hover:text-foreground"
										onClick={() => remove.mutate(label.id)}
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
					<Input
						data-testid="label-search"
						aria-label={t('task.label.placeholder')}
						placeholder={t('task.label.placeholder')}
						value={search}
						onChange={(event) => setSearch(event.target.value)}
					/>

					{add.isError || remove.isError ? (
						<p role="alert" data-testid="label-error" className="text-sm text-xyz-red-6">
							{(add.error ?? remove.error)?.message}
						</p>
					) : null}

					<ul className="flex flex-wrap gap-2" data-testid="available-labels">
						{available.length === 0 ? (
							<li className="text-sm text-muted-foreground">
								{all.isPending ? t('misc.loading') : '没有可添加的标签'}
							</li>
						) : (
							available.map((label) => (
								<li key={label.id}>
									<Button
										type="button"
										variant="outline"
										size="sm"
										disabled={busy}
										data-testid="available-label"
										data-label-id={label.id}
										onClick={() => add.mutate(label.id)}
									>
										<LabelSwatch label={label} />
										{label.title}
									</Button>
								</li>
							))
						)}
					</ul>
				</div>
			)}
		</section>
	);
}

/** hex_color 不带前导 `#`，渲染时才补上；空值不渲染色块。 */
function LabelSwatch({ label }: { label: Label }) {
	if (!label.hex_color) return null;
	return (
		<span
			aria-hidden
			data-testid="label-swatch"
			className="inline-block size-2 rounded-full"
			style={{ backgroundColor: `#${label.hex_color}` }}
		/>
	);
}
