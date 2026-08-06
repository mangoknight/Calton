import { AlertTriangle, Plus } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import type { Project } from '@/api/projects';
import { DeleteProjectDialog } from '@/components/projects/DeleteProjectDialog';
import { ProjectFormDialog } from '@/components/projects/ProjectFormDialog';
import { ProjectTree } from '@/components/projects/ProjectTree';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/context';
import { useProjectTree } from '@/features/projects/queries';

export function ProjectsPage() {
	const { tree, query } = useProjectTree();
	const t = useTranslation();

	const [formOpen, setFormOpen] = useState(false);
	const [editing, setEditing] = useState<Project | undefined>(undefined);
	const [deleting, setDeleting] = useState<Project | null>(null);

	const allProjects = query.data?.items ?? [];

	function openCreate() {
		setEditing(undefined);
		setFormOpen(true);
	}

	function openEdit(project: Project) {
		setEditing(project);
		setFormOpen(true);
	}

	return (
		<section className="p-6" data-testid="projects-page">
			<div className="flex items-center justify-between">
				<h1 className="text-lg font-semibold text-foreground">{t('project.projects')}</h1>
				<Button size="sm" data-testid="new-project" onClick={openCreate}>
					<Plus aria-hidden />
					{t('project.create.header')}
				</Button>
			</div>

			{query.isPending ? <p className="mt-4 text-sm text-muted-foreground">加载中…</p> : null}

			{query.isError ? (
				<p role="alert" className="mt-4 text-sm text-xyz-red-6">
					{query.error.message}
				</p>
			) : null}

			{query.isSuccess ? (
				<div className="mt-4 space-y-4">
					{tree.cycles.length > 0 ? <CycleWarning cycles={tree.cycles} /> : null}

					{tree.roots.length > 0 ? (
						<ProjectTree nodes={tree.roots} onEdit={openEdit} onDelete={setDeleting} />
					) : null}

					{tree.roots.length === 0 && tree.cycles.length === 0 ? (
						<p className="text-sm text-muted-foreground">还没有项目。</p>
					) : null}
				</div>
			) : null}

			{formOpen ? (
				<ProjectFormDialog
					open={formOpen}
					onOpenChange={setFormOpen}
					project={editing}
					candidates={allProjects}
				/>
			) : null}

			<DeleteProjectDialog
				project={deleting}
				allProjects={allProjects}
				onOpenChange={(open) => !open && setDeleting(null)}
			/>
		</section>
	);
}

/**
 * 上级项目成环时的提示。
 *
 * 不静默丢弃这些项目——用户会以为项目凭空消失了，而且这是后端数据问题，
 * 藏起来只会让它更晚被发现。平铺出来仍然可点进去，用户至少能自救（改掉上级项目）。
 */
function CycleWarning({ cycles }: { cycles: { id: number; title: string }[] }) {
	return (
		<div
			role="alert"
			data-testid="cycle-warning"
			className="border border-xyz-amber-5 bg-xyz-amber-1 p-3"
		>
			<p className="flex items-center gap-2 text-sm font-medium text-xyz-amber-7">
				<AlertTriangle className="size-4" aria-hidden />有 {cycles.length}{' '}
				个项目的上级项目形成了循环，无法挂进项目树
			</p>
			<p className="mt-1 text-sm text-xyz-amber-7">
				它们被平铺在下面，可以点进去把「上级项目」改掉：
			</p>
			<ul className="mt-2 space-y-1">
				{cycles.map((project) => (
					<li key={project.id}>
						<Link
							to={`/projects/${project.id}/list`}
							className="text-sm text-primary underline-offset-4 hover:underline"
						>
							{project.title}
						</Link>
					</li>
				))}
			</ul>
		</div>
	);
}
