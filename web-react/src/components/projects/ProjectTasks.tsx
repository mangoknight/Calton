import { ChevronDown, ChevronRight, Circle, CircleCheck, CircleDot } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { statusOf } from '@/features/board/queries';
import { useProjectTaskTree } from '@/features/tasks/queries';
import type { TaskNode } from '@/features/tasks/subtask-tree';
import { cn } from '@/lib/utils';

/**
 * 项目节点展开后，在其下渲染该项目的**任务/子任务树**。
 *
 * `baseDepth` = 项目节点自身 depth + 1，任务再在其上按子任务嵌套加深，
 * 缩进和上面的项目树连续（同用每级 16px 的内联 padding）。
 * 数据懒加载：只有项目被展开、这个组件被挂上才会发请求。
 */
export function ProjectTasks({ projectId, baseDepth }: { projectId: number; baseDepth: number }) {
	const { data, isPending, isError, error } = useProjectTaskTree(projectId, true);
	const indent = { paddingLeft: `${baseDepth * 16 + 20}px` };

	if (isPending) {
		return (
			<p className="py-1 text-xs text-muted-foreground" style={indent}>
				任务加载中…
			</p>
		);
	}

	if (isError) {
		return (
			<p role="alert" className="py-1 text-xs text-xyz-red-6" style={indent}>
				{error.message}
			</p>
		);
	}

	if (data.tree.roots.length === 0) {
		return (
			<p className="py-1 text-xs text-muted-foreground" style={indent}>
				该项目暂无任务
			</p>
		);
	}

	return (
		<ul role="group" className="space-y-0.5">
			{data.tree.roots.map((node) => (
				<TaskTreeItem key={node.task.id} node={node} baseDepth={baseDepth} />
			))}
			{data.truncated ? (
				<li className="py-1 text-xs text-muted-foreground" style={indent}>
					任务较多，仅展示了一部分
				</li>
			) : null}
		</ul>
	);
}

function StatusIcon({ node }: { node: TaskNode }) {
	const status = statusOf(node.task);
	if (status === 'done') {
		return <CircleCheck className="size-4 shrink-0 text-xyz-green-6" aria-label="已完成" />;
	}
	if (status === 'doing') {
		return <CircleDot className="size-4 shrink-0 text-xyz-blue-6" aria-label="进行中" />;
	}
	return <Circle className="size-4 shrink-0 text-muted-foreground" aria-label="待办" />;
}

function TaskTreeItem({ node, baseDepth }: { node: TaskNode; baseDepth: number }) {
	const [expanded, setExpanded] = useState(true);
	const hasChildren = node.children.length > 0;
	// baseDepth 是项目那一层的偏移，node.depth 是任务子树内的层级
	const pad = (baseDepth + node.depth) * 16 + 20;

	return (
		<li role="treeitem" aria-expanded={hasChildren ? expanded : undefined}>
			<div className="flex items-center gap-1 rounded-md py-0.5 pr-1 transition-colors hover:bg-accent/60"
					style={{ paddingLeft: `${pad}px` }}>
				{hasChildren ? (
					<button
						type="button"
						onClick={() => setExpanded((v) => !v)}
						data-testid={`task-toggle-${node.task.id}`}
						aria-label={`${expanded ? '折叠' : '展开'}子任务 ${node.task.title}`}
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

				<StatusIcon node={node} />

				<Link
					to={`/tasks/${node.task.id}`}
					data-testid={`project-task-${node.task.id}`}
					className={cn(
						'truncate text-sm text-foreground hover:text-primary hover:underline',
						node.task.done && 'text-muted-foreground line-through',
					)}
				>
					{node.task.title}
				</Link>

				{node.task.identifier ? (
					<span className="shrink-0 text-xs text-muted-foreground">{node.task.identifier}</span>
				) : null}
			</div>

			{hasChildren && expanded ? (
				<ul role="group" className="space-y-0.5">
					{node.children.map((child) => (
						<TaskTreeItem key={child.task.id} node={child} baseDepth={baseDepth} />
					))}
				</ul>
			) : null}
		</li>
	);
}
