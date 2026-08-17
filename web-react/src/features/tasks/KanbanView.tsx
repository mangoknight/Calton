import {
	DndContext,
	PointerSensor,
	useDraggable,
	useDroppable,
	useSensor,
	useSensors,
	type DragEndEvent,
} from '@dnd-kit/core';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { isBucketFull, type Bucket } from '@/api/buckets';
import type { Task } from '@/api/tasks';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/context';
import { formatApiDate } from '@/lib/datetime';
import { cn } from '@/lib/utils';
import { bucketDropId, resolveDrop, taskDragId } from './board-move';
import { BucketFormDialog, DeleteBucketDialog } from './BucketDialogs';
import { useBoard, useCreateBucket, useDeleteBucket, useUpdateBucket } from './bucket-queries';
import { TaskQueryError } from './FilterError';
import { useFilterParam } from './filter-param';
import { useMoveTask } from './useMoveTask';

/**
 * 看板视图静态渲染 + 列（桶）增删改（F07a）。
 * 拖拽与位置计算是 F07b，这里的卡片**不可拖动**。
 *
 * 板面数据走的是 tasks 端点的多态形态（见 `api/buckets.ts` 文件头）：
 * kanban view 返回的是带 tasks 的 Bucket[]，`count` 只在这条路径上才有值。
 */
export function KanbanView({ projectId, viewId }: { projectId: number; viewId: number }) {
	const { filter } = useFilterParam();
	const query = useBoard(projectId, viewId, filter);
	const context = { projectId, viewId };

	const createBucket = useCreateBucket(context);
	const updateBucket = useUpdateBucket(context);
	const removeBucket = useDeleteBucket(context);
	const moveTask = useMoveTask(projectId, viewId, filter);
	const t = useTranslation();

	// 按住挪动 6px 才算拖拽，否则点开任务详情的点击会被当成拖拽吞掉
	const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

	const [formState, setFormState] = useState<{ open: boolean; bucket: Bucket | null }>({
		open: false,
		bucket: null,
	});
	const [pendingDelete, setPendingDelete] = useState<Bucket | null>(null);

	if (query.isPending) {
		return <p className="text-sm text-muted-foreground">加载中…</p>;
	}

	if (query.isError) {
		return <TaskQueryError error={query.error} />;
	}

	const buckets = query.data.items;

	function onDragEnd(event: DragEndEvent) {
		const move = resolveDrop(buckets, event.active.id, event.over?.id);
		// resolveDrop 返回 null = 这次拖拽没有产生实际移动，不发请求
		if (move) moveTask.mutate(move);
	}

	return (
		<div className="flex h-full flex-col gap-3" data-testid="kanban-view">
			<div className="flex shrink-0 items-center justify-end gap-3">
				{moveTask.isError ? (
					<p role="alert" data-testid="move-error" className="text-sm text-xyz-red-6">
						{moveTask.error.message}
					</p>
				) : null}
				<Button
					type="button"
					size="sm"
					variant="outline"
					data-testid="new-bucket"
					onClick={() => setFormState({ open: true, bucket: null })}
				>
					{t('project.kanban.addBucket')}
				</Button>
			</div>

			{buckets.length === 0 ? (
				<p data-testid="kanban-empty" className="text-sm text-muted-foreground">
					这个看板还没有列。新建一列后就能往里放任务了。
				</p>
			) : (
				<DndContext sensors={sensors} onDragEnd={onDragEnd}>
					<div className="flex min-h-0 flex-1 gap-4 overflow-x-auto pb-2">
						{buckets.map((bucket) => (
							<BucketColumn
								key={bucket.id}
								bucket={bucket}
								onEdit={() => setFormState({ open: true, bucket })}
								onDelete={() => setPendingDelete(bucket)}
							/>
						))}
					</div>
				</DndContext>
			)}

			<BucketFormDialog
				// key 让弹窗每次打开都重建，否则表单里留着上一个桶的标题
				key={formState.bucket?.id ?? 'new'}
				open={formState.open}
				bucket={formState.bucket}
				mutation={formState.bucket ? updateBucket : createBucket}
				onOpenChange={(open) => setFormState((state) => ({ ...state, open }))}
			/>

			<DeleteBucketDialog
				bucket={pendingDelete}
				// 后端删最后一列会 412 + code 10003，前端先拦一道并解释原因
				isLastBucket={buckets.length <= 1}
				mutation={removeBucket}
				onOpenChange={() => setPendingDelete(null)}
			/>
		</div>
	);
}

function BucketColumn({
	bucket,
	onEdit,
	onDelete,
}: {
	bucket: Bucket;
	onEdit: () => void;
	onDelete: () => void;
}) {
	const t = useTranslation();
	const full = isBucketFull(bucket);
	const tasks = bucket.tasks ?? [];
	// 整列都是放置区：落在列的空白处 = 放到末尾
	const { setNodeRef, isOver } = useDroppable({ id: bucketDropId(bucket.id) });

	return (
		<section
			ref={setNodeRef}
			data-testid="bucket-column"
			data-bucket-id={bucket.id}
			data-full={full || undefined}
			className={cn(
				'flex w-72 shrink-0 flex-col rounded-lg border border-border bg-muted/30',
				full && 'border-xyz-red-5',
				isOver && 'ring-2 ring-primary',
			)}
		>
			<header className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
				<h3 className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
					{bucket.title}
				</h3>

				<span
					data-testid="bucket-count"
					className={cn('text-xs', full ? 'font-medium text-xyz-red-6' : 'text-muted-foreground')}
				>
					{/* limit 为 0 表示不限，此时只报数量，不要渲染成 "3/0" */}
					{bucket.limit > 0 ? `${bucket.count}/${bucket.limit}` : bucket.count}
				</span>

				<button
					type="button"
					onClick={onEdit}
					data-testid={`bucket-edit-${bucket.id}`}
					aria-label={`编辑列 ${bucket.title}`}
					className="text-xs text-muted-foreground hover:text-foreground"
				>
					{t('input.editor.edit')}
				</button>
				<button
					type="button"
					onClick={onDelete}
					data-testid={`bucket-delete-${bucket.id}`}
					aria-label={`删除列 ${bucket.title}`}
					className="text-xs text-muted-foreground hover:text-foreground"
				>
					{t('misc.delete')}
				</button>
			</header>

			{full ? (
				<p
					role="status"
					data-testid="bucket-full-notice"
					className="shrink-0 border-b bg-xyz-red-1 px-3 py-1.5 text-xs text-xyz-red-7"
				>
					{t('project.kanban.bucketLimitReached')}
				</p>
			) : null}

			<ul className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
				{tasks.map((task) => (
					<TaskCard key={task.id} task={task} />
				))}
			</ul>

			{/*
			  count 是总数，tasks 只是当前页（每列最多取 BOARD_TASKS_PER_BUCKET 条）。
			  差值不提示的话，用户会以为这一列真的就这么几张卡。
			*/}
			{bucket.count > tasks.length ? (
				<p
					data-testid="bucket-truncated"
					className="shrink-0 px-3 pb-2 text-xs text-muted-foreground"
				>
					还有 {bucket.count - tasks.length} 个任务未显示
				</p>
			) : null}
		</section>
	);
}

function TaskCard({ task }: { task: Task }) {
	// ⚠️ 零值时间不能直接 new Date，会渲染成公元 1 年
	const dueDate = formatApiDate(task.due_date);
	// 卡片既是拖拽源，也是放置目标（落在卡上 = 插到它前面）
	const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
		id: taskDragId(task.id),
	});
	const { setNodeRef: setDropRef } = useDroppable({ id: taskDragId(task.id) });

	return (
		<li
			ref={(node) => {
				setNodeRef(node);
				setDropRef(node);
			}}
			data-testid="task-card"
			data-task-id={task.id}
			data-dragging={isDragging || undefined}
			className={cn(isDragging && 'opacity-50')}
			{...attributes}
			{...listeners}
		>
			<Link
				to={`/tasks/${task.id}`}
				className="block rounded-md border border-border bg-card p-2 text-sm shadow-sm transition-colors hover:border-primary hover:bg-accent/50"
			>
				<span className={cn('block', task.done && 'text-muted-foreground line-through')}>
					{task.title}
				</span>
				{dueDate ? (
					<time dateTime={task.due_date} className="mt-1 block text-xs text-muted-foreground">
						{dueDate}
					</time>
				) : null}
			</Link>
		</li>
	);
}
