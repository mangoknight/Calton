import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { z } from 'zod';

import type { Project } from '@/api/projects';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import {
	applyParentSelection,
	initialParentValue,
	parseParentSelection,
	PARENT_KEEP,
	selectableParents,
	TOP_LEVEL_PARENT_ID,
} from '@/features/projects/parent-field';
import { canAttachUnder, canReparent } from '@/features/projects/permissions';
import { useCreateProject, useUpdateProject } from '@/features/projects/mutations';
import { useTranslation } from '@/i18n/context';

// ⚠️ 消息存 i18n key 不存句子（理由见 LoginPage.tsx 同处注释）
const schema = z.object({
	title: z.string().trim().min(1, 'project.create.addTitleRequired'),
	description: z.string().optional(),
	parent: z.string(),
});

type FormValues = z.infer<typeof schema>;

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	/** 传入表示编辑，不传表示新建。 */
	project?: Project;
	/** 可选的上级项目候选（编辑时会排除自己）。 */
	candidates: Project[];
}

export function ProjectFormDialog({ open, onOpenChange, project, candidates }: Props) {
	const isEdit = project !== undefined;
	const create = useCreateProject();
	const update = useUpdateProject();
	const mutation = isEdit ? update : create;
	const t = useTranslation();

	const {
		register,
		handleSubmit,
		reset,
		formState: { errors },
	} = useForm<FormValues>({
		resolver: zodResolver(schema),
		values: {
			title: project?.title ?? '',
			description: project?.description ?? '',
			parent: initialParentValue(),
		},
	});

	const onSubmit = handleSubmit((values) => {
		const selection = parseParentSelection(values.parent);

		if (isEdit) {
			// ⚠️ POST 是全量替换：完整对象回传，只发改动字段会清空其余字段。
			// 但 parent_project_id 是这条规则的显式例外 —— applyParentSelection
			// 在"不修改"时会把它从 payload 里删掉，避免用陈旧值覆盖别人的移动操作。
			const payload = applyParentSelection(
				{ ...project, title: values.title, description: values.description },
				selection,
			);
			update.mutate(payload, {
				onSuccess: () => {
					onOpenChange(false);
					reset();
				},
			});
			return;
		}

		const payload = applyParentSelection(
			{ title: values.title, description: values.description },
			selection,
		);
		create.mutate(payload, {
			onSuccess: () => {
				onOpenChange(false);
				reset();
			},
		});
	});

	const selectable = selectableParents(candidates, project);
	// 第 2 道闸：改动 parent 需要对被移动的项目有 Admin。没有就整个禁掉，
	// 免得用户填完表单才吃 403。新建项目不受此限（还没有这个项目）。
	const reparentAllowed = !isEdit || canReparent(project);

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent data-testid="project-form">
				<DialogTitle className="ink-heading text-lg">
						{isEdit ? t('project.edit.header') : t('project.create.header')}
					</DialogTitle>
				<DialogDescription>
					{isEdit ? t('project.edit.titlePlaceholder') : t('project.create.titlePlaceholder')}
				</DialogDescription>

				<form onSubmit={onSubmit} className="mt-4 space-y-4" noValidate>
					<Field
						label={t('project.title')}
						htmlFor="project-title"
						error={errors.title?.message ? t(errors.title.message) : undefined}
					>
						<Input
							id="project-title"
							data-testid="project-title-input"
							autoFocus
							{...register('title')}
						/>
					</Field>

					<Field label={t('project.edit.description')} htmlFor="project-description">
						<Input
							id="project-description"
							data-testid="project-description-input"
							{...register('description')}
						/>
					</Field>

					<Field
						label={t('project.parent')}
						htmlFor="project-parent"
						error={
							reparentAllowed
								? undefined
								: '你对该项目没有管理员权限，无法调整它的上级项目（其余字段仍可修改）'
						}
					>
						<select
							id="project-parent"
							data-testid="project-parent-select"
							disabled={!reparentAllowed}
							className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm disabled:cursor-not-allowed disabled:opacity-50"
							{...register('parent')}
						>
							{/* 实测定案：省略 = 不改，传数字 = 设值（0 即顶层），所以只有两态 */}
							<option value={PARENT_KEEP}>{isEdit ? '不修改' : '无（顶层项目）'}</option>
							{isEdit ? <option value={TOP_LEVEL_PARENT_ID}>移到顶层</option> : null}
							{selectable.map((candidate) => (
								// 第 3 道闸：挂到某父级下需要对该父级有 Admin。
								// 注定 403 的选项提前禁掉并注明原因，别让用户提交后才知道。
								<option
									key={candidate.id}
									value={candidate.id}
									disabled={!canAttachUnder(candidate)}
								>
									{candidate.title}
									{canAttachUnder(candidate) ? '' : '（需要管理员权限）'}
								</option>
							))}
						</select>
					</Field>

					{mutation.isError ? (
						<p role="alert" className="text-sm text-xyz-red-6">
							{mutation.error.message}
						</p>
					) : null}

					<div className="flex justify-end gap-2">
						<Button
							type="button"
							variant="outline"
							data-testid="project-form-cancel"
							onClick={() => onOpenChange(false)}
						>
							{t('misc.cancel')}
						</Button>
						<Button type="submit" data-testid="project-form-submit" disabled={mutation.isPending}>
							{mutation.isPending ? t('misc.saving') : t('misc.save')}
						</Button>
					</div>
				</form>
			</DialogContent>
		</Dialog>
	);
}
