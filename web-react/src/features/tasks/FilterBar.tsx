import { useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { SavedFilterFormDialog } from '@/features/filters/SavedFilterFormDialog';
import { useTranslation } from '@/i18n/context';
import { filterHints } from './filter-hints';
import { useFilterParam } from './filter-param';

/**
 * 筛选器 DSL 输入（F11a）。
 *
 * ## 为什么是"提交时生效"而不是边打边筛
 *
 * DSL 在输入过程中**几乎每一刻都是语法错误**（打到 `done =` 时还没有值）。
 * 边打边发的话，用户会看到一串本来就该出现的报错在眼前闪，
 * 真正的错误反而淹没了。所以按回车/点"应用"才提交。
 *
 * ## 提示不拦提交
 *
 * `filterHints` 产出的是旁注（见该模块头注）。这里刻意**不**根据它禁用提交按钮：
 * 那几种写法后端全都返回 200，前端拦下来就是"UI 拦得住、API 拦不住"的分歧。
 */
export function FilterBar() {
	const { filter, setFilter } = useFilterParam();
	const t = useTranslation();

	// 受控输入的本地草稿；已应用的值在 URL 上。
	const [draft, setDraft] = useState(filter);

	// URL 上的 filter 被外部改变（前进/后退、点了"清除"）时把草稿同步回来，
	// 否则地址栏与输入框会显示两个不同的条件。
	useEffect(() => setDraft(filter), [filter]);

	const [saveOpen, setSaveOpen] = useState(false);

	const hints = filterHints(draft);
	const isDirty = draft !== filter;

	return (
		<form
			className="mt-3"
			data-testid="filter-bar"
			onSubmit={(event) => {
				event.preventDefault();
				setFilter(draft);
			}}
		>
			<div className="flex items-end gap-2">
				<div className="min-w-0 flex-1">
					{/* ⚠️ filters.query.label 上游 zh-CN 缺翻译 → 退回 en "Filter query"。
					    与 SavedFilterFormDialog 里同一个 key 的取舍保持一致。 */}
					<label htmlFor="task-filter" className="text-sm font-medium text-foreground">
						{t('filters.query.label')}
					</label>
					<Input
						id="task-filter"
						data-testid="filter-input"
						value={draft}
						placeholder={t('filters.query.placeholder')}
						className="mt-1 font-mono"
						onChange={(event) => setDraft(event.target.value)}
					/>
				</div>

				<Button type="submit" size="sm" data-testid="filter-apply" disabled={!isDirty}>
					{t('filters.showResults')}
				</Button>
				{filter !== '' ? (
					<Button
						type="button"
						variant="outline"
						size="sm"
						data-testid="filter-clear"
						onClick={() => setFilter('')}
					>
						{t('filters.clear')}
					</Button>
				) : null}
				{/*
				 * 存的是**输入框里的那份（draft）**，不是已应用的 `filter`。
				 * 用户改了条件还没点"应用"就来保存时，屏幕上写着 A 却存下 B 是更坏的意外；
				 * 何况弹窗里这个表达式还能再改。
				 *
				 * 这个按钮**不因为表达式为空而禁用** —— 与 `filter-param.ts` 同一条规矩：
				 * 什么样的表达式算有效由后端说了算，前端不替它做决定。
				 */}
				<Button
					type="button"
					variant="outline"
					size="sm"
					data-testid="save-filter-button"
					onClick={() => setSaveOpen(true)}
				>
					{t('filters.create.action')}
				</Button>
			</div>

			{hints.length > 0 ? (
				<ul className="mt-2 space-y-1" data-testid="filter-hints">
					{hints.map((hint) => (
						<li
							key={hint.id}
							data-testid={`filter-hint-${hint.id}`}
							className="text-sm text-xyz-orange-6"
						>
							{hint.message}
						</li>
					))}
				</ul>
			) : null}

			{/*
			 * 弹窗自带 <form>，而 Radix 把它 Portal 到 body ——
			 * DOM 上不会形成 form 套 form（那是无效 HTML，且外层提交会被内层触发）。
			 * 写在这里是为了让它跟着 FilterBar 的生命周期走。
			 */}
			<SavedFilterFormDialog open={saveOpen} onOpenChange={setSaveOpen} initialFilter={draft} />
		</form>
	);
}
