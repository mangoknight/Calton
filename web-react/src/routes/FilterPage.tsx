import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/field';
import {
	useDeleteSavedFilter,
	useSavedFilter,
	useUpdateSavedFilter,
} from '@/features/filters/queries';
import { parseFilterRouteId, projectIdFromSavedFilterId } from '@/features/filters/pseudo-project';
import { ListView } from '@/features/tasks/ListView';
import { useTranslation } from '@/i18n/context';
import { useProjectViews } from '@/features/views/queries';

/**
 * 保存的过滤器页（F11b）。
 *
 * ## 路由用**正的 filter id**，负数只在调接口时出现
 *
 * `/filters/2` 而不是 `/projects/-3/list`。这样 `parseRouteId`
 * （`/projects/:projectId/:view` 的守卫）不必为了容纳负数而放宽 ——
 * 放宽它等于把 `/projects/new/list` 那道防线一起拆了。
 * 换算集中在 `pseudo-project.ts`，发生在边界上。
 *
 * 任务查询仍然**走伪项目 id 路径**（验收要求），只是那个负数不出现在 URL 里。
 */
export function FilterPage() {
	const params = useParams();
	const filterId = parseFilterRouteId(params.filterId);

	if (filterId === null) {
		return (
			<section className="p-6" data-testid="invalid-filter-route">
				<h1 className="ink-heading text-2xl">无效的筛选器</h1>
				<p role="alert" className="mt-2 text-sm text-muted-foreground">
					地址里的筛选器 ID「{params.filterId}」不是有效的正整数。
				</p>
			</section>
		);
	}

	return <FilterDetail filterId={filterId} />;
}

function FilterDetail({ filterId }: { filterId: number }) {
	const query = useSavedFilter(filterId);
	const navigate = useNavigate();
	const update = useUpdateSavedFilter(filterId);
	const remove = useDeleteSavedFilter();
	const t = useTranslation();

	const [editing, setEditing] = useState(false);
	const [title, setTitle] = useState('');
	const [titleError, setTitleError] = useState<string | null>(null);

	// 伪项目 id 只在这里出现，用来查任务
	const projectId = projectIdFromSavedFilterId(filterId);

	if (query.isPending) {
		return <p className="p-6 text-sm text-muted-foreground">加载中…</p>;
	}

	if (query.isError) {
		return (
			<p role="alert" className="p-6 text-sm text-xyz-red-6">
				{query.error.message}
			</p>
		);
	}

	const filter = query.data;

	function startEdit() {
		setTitle(filter.title);
		setTitleError(null);
		setEditing(true);
	}

	function save(event: React.FormEvent) {
		event.preventDefault();
		const trimmed = title.trim();
		// 后端 title 是 valid:"required"，空标题 412；前端先拦
		if (!trimmed) {
			setTitleError(t('filters.create.titleRequired'));
			return;
		}
		setTitleError(null);
		update.mutate(
			{
				title: trimmed,
				description: filter.description,
				// ⚠️ 必须回传 filters —— 后端 valid:"required"，丢了这个过滤条件就没了
				filters: { filter: filter.filters?.filter ?? '' },
			},
			{ onSuccess: () => setEditing(false) },
		);
	}

	return (
		<section className="flex h-full flex-col gap-4 p-6" data-testid="filter-page">
			<header className="flex items-center gap-3">
				{editing ? (
					<form onSubmit={save} className="flex flex-1 items-end gap-2">
						<Field
							label={t('filters.attributes.title')}
							htmlFor="filter-title"
							error={titleError ?? undefined}
						>
							<Input
								id="filter-title"
								data-testid="filter-title-input"
								value={title}
								onChange={(event) => setTitle(event.target.value)}
							/>
						</Field>
						<Button
							type="submit"
							size="sm"
							data-testid="filter-rename-save"
							disabled={update.isPending}
						>
							{t('misc.save')}
						</Button>
						<Button
							type="button"
							size="sm"
							variant="outline"
							data-testid="filter-rename-cancel"
							onClick={() => setEditing(false)}
						>
							{t('misc.cancel')}
						</Button>
					</form>
				) : (
					<>
						<h1 className="ink-heading flex-1 text-2xl">{filter.title}</h1>
						<Button
							type="button"
							size="sm"
							variant="outline"
							data-testid="filter-rename"
							onClick={startEdit}
						>
							重命名
						</Button>
						<Button
							type="button"
							size="sm"
							variant="destructive"
							data-testid="filter-delete"
							disabled={remove.isPending}
							onClick={() =>
								remove.mutate(filterId, { onSuccess: () => void navigate('/projects') })
							}
						>
							{t('misc.delete')}
						</Button>
					</>
				)}
			</header>

			{filter.filters?.filter ? (
				<p className="text-sm text-muted-foreground" data-testid="filter-expression">
					条件：<code>{filter.filters.filter}</code>
				</p>
			) : null}

			{update.isError || remove.isError ? (
				<p role="alert" data-testid="filter-error" className="text-sm text-xyz-red-6">
					{(update.error ?? remove.error)?.message}
				</p>
			) : null}

			{projectId === null ? (
				<p role="alert" className="text-sm text-xyz-red-6">
					无法为该筛选器换算出项目 ID。
				</p>
			) : (
				<FilterTasks projectId={projectId} />
			)}
		</section>
	);
}

/**
 * 过滤器的任务列表。走**伪项目 id** 的视图路径，复用 F05b 的 List 视图。
 *
 * ⚠️ 这里不能硬编码 view id：saved filter 的伪项目也有自己的视图集合，
 * id 与真实项目的不同。先查 views 再取 list 那个。
 */
function FilterTasks({ projectId }: { projectId: number }) {
	const views = useProjectViews(projectId);

	if (views.isPending) {
		return <p className="text-sm text-muted-foreground">加载中…</p>;
	}

	if (views.isError) {
		return (
			<p role="alert" className="text-sm text-xyz-red-6">
				{views.error.message}
			</p>
		);
	}

	const listView = views.data.items.find((view) => view.view_kind === 'list');
	if (!listView) {
		return (
			<p role="alert" data-testid="filter-missing-view" className="text-sm text-xyz-red-6">
				该筛选器没有列表视图，无法展示任务。
			</p>
		);
	}

	return <ListView projectId={projectId} viewId={listView.id} />;
}
