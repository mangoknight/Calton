import { ChevronDown, ChevronRight, Folder, ListTodo, Pencil, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import type { Project } from '@/api/projects';
import type { ProjectNode } from '@/features/projects/tree';
import { cn } from '@/lib/utils';
import { ProjectTasks } from './ProjectTasks';

interface TreeActions {
	onEdit: (project: Project) => void;
	onDelete: (project: Project) => void;
}

/**
 * 项目树递归渲染。
 *
 * 能放心递归的前提是 buildProjectTree 已经在建树阶段把环剔掉了 ——
 * 这里拿到的 roots 一定是有限深度的真树。渲染层不再做限深兜底，
 * 免得掩盖数据问题（该报警的要报警，见 ProjectsPage 的 cycles 提示）。
 */

export function ProjectTree({ nodes, onEdit, onDelete }: { nodes: ProjectNode[] } & TreeActions) {
	return (
		<ul role="tree" aria-label="项目树" className="space-y-0.5">
			{nodes.map((node) => (
				<ProjectTreeItem key={node.project.id} node={node} onEdit={onEdit} onDelete={onDelete} />
			))}
		</ul>
	);
}

/** 子层是 group 而不是又一棵 tree —— 一棵树只能有一个 role="tree" 根。 */
function ProjectTreeGroup({ nodes, onEdit, onDelete }: { nodes: ProjectNode[] } & TreeActions) {
	return (
		<ul role="group" className="space-y-0.5">
			{nodes.map((node) => (
				<ProjectTreeItem key={node.project.id} node={node} onEdit={onEdit} onDelete={onDelete} />
			))}
		</ul>
	);
}

function ProjectTreeItem({ node, onEdit, onDelete }: { node: ProjectNode } & TreeActions) {
	const [expanded, setExpanded] = useState(true);
	// 任务展开是独立于子项目展开的一档，默认收起：项目多时不一上来就把所有任务全拉了
	const [tasksOpen, setTasksOpen] = useState(false);
	const hasChildren = node.children.length > 0;

	return (
		<li role="treeitem" aria-expanded={hasChildren ? expanded : undefined}>
			<div
				className="flex items-center gap-1 py-1"
				// 缩进用内联 padding：层级是运行时算出来的，Tailwind 的静态类名覆盖不到任意深度
				style={{ paddingLeft: `${node.depth * 16}px` }}
			>
				{hasChildren ? (
					<button
						type="button"
						onClick={() => setExpanded((v) => !v)}
						data-testid={`project-toggle-${node.project.id}`}
						aria-label={`${expanded ? '折叠' : '展开'} ${node.project.title}`}
						className="text-muted-foreground hover:text-foreground"
					>
						{expanded ? (
							<ChevronDown className="size-4" aria-hidden />
						) : (
							<ChevronRight className="size-4" aria-hidden />
						)}
					</button>
				) : (
					<span className="size-4 shrink-0" />
				)}

				<Folder
					className="size-4 shrink-0 text-muted-foreground"
					aria-hidden
					style={node.project.hex_color ? { color: `#${node.project.hex_color}` } : undefined}
				/>

				<Link
					to={`/projects/${node.project.id}/list`}
					className={cn(
						'truncate text-sm text-foreground hover:text-primary hover:underline',
						node.project.is_archived && 'text-muted-foreground line-through',
					)}
				>
					{node.project.title}
				</Link>

				<span className="ml-auto flex items-center gap-1">
					<button
						type="button"
						data-testid={`project-tasks-toggle-${node.project.id}`}
						aria-label={`${tasksOpen ? '收起' : '展开'}任务 ${node.project.title}`}
						aria-pressed={tasksOpen}
						onClick={() => setTasksOpen((v) => !v)}
						className={cn(
							'hover:text-foreground',
							tasksOpen ? 'text-primary' : 'text-muted-foreground',
						)}
					>
						<ListTodo className="size-4" aria-hidden />
					</button>
					<button
						type="button"
						data-testid={`project-edit-${node.project.id}`}
						aria-label={`编辑 ${node.project.title}`}
						onClick={() => onEdit(node.project)}
						className="text-muted-foreground hover:text-foreground"
					>
						<Pencil className="size-4" aria-hidden />
					</button>
					<button
						type="button"
						data-testid={`project-delete-${node.project.id}`}
						aria-label={`删除 ${node.project.title}`}
						onClick={() => onDelete(node.project)}
						className="text-muted-foreground hover:text-xyz-red-6"
					>
						<Trash2 className="size-4" aria-hidden />
					</button>
				</span>
			</div>

			{tasksOpen ? <ProjectTasks projectId={node.project.id} baseDepth={node.depth + 1} /> : null}

			{hasChildren && expanded ? (
				<ProjectTreeGroup nodes={node.children} onEdit={onEdit} onDelete={onDelete} />
			) : null}
		</li>
	);
}
