import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { useTranslation } from '@/i18n/context';
import { useCreateSavedFilter } from './queries';

/**
 * 新建保存的筛选器（F11b）。
 *
 * ## 入口为什么在 FilterBar 上
 *
 * 「保存筛选器」需要一个**筛选表达式**做内容，而表达式的唯一来源就是 F11a 的
 * `?filter=` —— 它已经在 URL 上了。把入口放在别处（比如侧栏）意味着用户得
 * 凭空手写一遍 DSL，那正是 F11a 存在的意义所在的反面。
 *
 * ## 新建成功后跳到 `/filters/{正的 id}`
 *
 * 不跳 `/projects/{伪项目 id}/list`。负数只在调接口时出现，不进 URL ——
 * 理由见 `pseudo-project.ts` 的文件头（放宽 `parseRouteId` 会把
 * `/projects/new/list` 那道防线一起拆了）。
 *
 * ⚠️ 但**不假定响应里一定有 id**：拿不到可用 id 时只关闭弹窗，
 * 让侧栏（数据源是 `GET /projects`）把新筛选器显示出来，
 * 而不是跳去 `/filters/undefined` 把一次成功的创建显示成一个坏页面。
 */
export function SavedFilterFormDialog({
	open,
	onOpenChange,
	initialFilter,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	/** 预填的筛选表达式，通常是当前 `?filter=` 的值。 */
	initialFilter: string;
}) {
	const create = useCreateSavedFilter();
	const navigate = useNavigate();
	const t = useTranslation();

	const [title, setTitle] = useState('');
	const [filter, setFilter] = useState(initialFilter);
	const [titleError, setTitleError] = useState<string | null>(null);

	// 弹窗每次打开都取当时的表达式：用户可能改了筛选条件再来保存，
	// 用挂载时那一份会把上一次的条件存进去。
	useEffect(() => {
		if (!open) return;
		setTitle('');
		setFilter(initialFilter);
		setTitleError(null);
		create.reset();
		// create 是 mutation 对象，每次渲染都是新引用，放进依赖会每帧重置表单
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open, initialFilter]);

	function submit(event: React.FormEvent) {
		event.preventDefault();

		const trimmedTitle = title.trim();
		if (!trimmedTitle) {
			setTitleError(t('filters.create.titleRequired'));
			return;
		}
		setTitleError(null);

		create.mutate(
			// ⚠️ 表达式**原样发出，不 trim** —— 与 `filter-param.ts` 同一条规矩：
			// 纯空白在后端不等于空筛选，前端顺手规范化就是替后端做决定。
			{ title: trimmedTitle, filters: { filter } },
			{
				onSuccess: (filter) => {
					onOpenChange(false);
					if (Number.isInteger(filter.id) && filter.id > 0) {
						void navigate(`/filters/${filter.id}`);
					}
				},
			},
		);
	}

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent data-testid="saved-filter-form">
				<DialogTitle className="ink-heading text-lg">{t('filters.create.title')}</DialogTitle>
				<DialogDescription>{t('filters.create.description')}</DialogDescription>

				<form onSubmit={submit} className="mt-4 space-y-4" noValidate>
					<Field
						label={t('filters.attributes.title')}
						htmlFor="new-filter-title"
						error={titleError ?? undefined}
					>
						<Input
							id="new-filter-title"
							data-testid="filter-title-input"
							autoFocus
							value={title}
							onChange={(event) => setTitle(event.target.value)}
						/>
					</Field>

					<Field label={t('filters.query.label')} htmlFor="new-filter-expression">
						<Input
							id="new-filter-expression"
							data-testid="filter-expression-input"
							className="font-mono"
							value={filter}
							onChange={(event) => setFilter(event.target.value)}
						/>
					</Field>

					{create.isError ? (
						<p
							role="alert"
							data-testid="saved-filter-form-error"
							className="text-sm text-xyz-red-6"
						>
							{create.error.message}
						</p>
					) : null}

					<div className="flex justify-end gap-2">
						<Button
							type="button"
							variant="outline"
							data-testid="filter-save-cancel"
							onClick={() => onOpenChange(false)}
						>
							{t('misc.cancel')}
						</Button>
						<Button type="submit" data-testid="filter-save-submit" disabled={create.isPending}>
							{create.isPending ? t('misc.saving') : t('misc.save')}
						</Button>
					</div>
				</form>
			</DialogContent>
		</Dialog>
	);
}
