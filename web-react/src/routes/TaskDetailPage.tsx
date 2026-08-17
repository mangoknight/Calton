import { lazy, Suspense } from 'react';
import { useParams } from 'react-router-dom';

import type { Task } from '@/api/tasks';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { statusOf, type BoardStatus } from '@/features/board/queries';
import { AssigneeSelector } from '@/features/tasks/AssigneeSelector';
import { CommentSection } from '@/features/tasks/CommentSection';
import { LabelSelector } from '@/features/tasks/LabelSelector';
import { useTask, useUpdateTask } from '@/features/tasks/detail-queries';
import { formatApiDate, parseApiTime, toApiTime, ZERO_TIME } from '@/lib/datetime';
import { useTranslation } from '@/i18n/context';
import { parseRouteId } from '@/lib/route-params';
import { cn } from '@/lib/utils';

/**
 * ⚠️ 必须是**动态 import**，否则 Rollup 仍会把 TipTap 打进主包。
 * 命名导出要在这里转成 default，React.lazy 只认 default。
 */
const DescriptionEditor = lazy(() =>
	import('@/features/tasks/DescriptionEditor').then((module) => ({
		default: module.DescriptionEditor,
	})),
);

/**
 * 任务详情骨架 + 基础字段（F08a）：done / 优先级 / 到期日。
 * 富文本描述是 F08b，标签与指派是 F08c，评论是 F09。
 *
 * ⚠️ 保存走的是**全量替换**（见 `detail-queries.ts`），页面上任何一个输入框
 * 都不要单独 POST 自己那个字段。
 */
export function TaskDetailPage() {
	const params = useParams();
	const taskId = parseRouteId(params.taskId);

	if (taskId === null) {
		return (
			<section className="p-6" data-testid="invalid-task-route">
				<h1 className="text-lg font-semibold text-foreground">无效的任务</h1>
				<p role="alert" className="mt-2 text-sm text-muted-foreground">
					地址里的任务 ID「{params.taskId}」不是有效的数字。
				</p>
			</section>
		);
	}

	return <TaskDetail taskId={taskId} />;
}

/**
 * ⚠️ 存的是 **i18n key** 不是文字：这张表是模块级常量、只算一次，
 * 存文字的话切语言时优先级选项不会跟着变（同 `columns.ts` / `Sidebar.tsx` 的坑）。
 */
const PRIORITIES = [
	{ value: 0, labelKey: 'task.priority.unset' },
	{ value: 1, labelKey: 'task.priority.low' },
	{ value: 2, labelKey: 'task.priority.medium' },
	{ value: 3, labelKey: 'task.priority.high' },
	{ value: 4, labelKey: 'task.priority.urgent' },
	{ value: 5, labelKey: 'task.priority.doNow' },
];

function TaskDetail({ taskId }: { taskId: number }) {
	const t = useTranslation();
	const query = useTask(taskId);
	const update = useUpdateTask(taskId);

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

	const task = query.data.data;
	// x-max-permission：0=读 1=写 2=管理。只读时不给编辑控件，而不是让用户改完吃 403
	const readOnly = query.data.maxPermission === 0;

	return (
		<section className="flex h-full flex-col gap-6 p-6" data-testid="task-detail">
			<header className="flex items-start gap-3">
				<input
					type="checkbox"
					checked={task.done ?? false}
					disabled={readOnly || update.isPending}
					data-testid="toggle-done"
					aria-label={t('task.detail.done')}
					className="mt-1.5 size-4 shrink-0"
					onChange={(event) => update.mutate({ done: event.target.checked })}
				/>
				<div className="min-w-0 flex-1">
					<h1
						className={cn(
							'text-xl font-semibold',
							task.done ? 'text-muted-foreground line-through' : 'text-foreground',
						)}
					>
						{task.title}
					</h1>
					{task.identifier ? (
						<p className="mt-1 text-sm text-muted-foreground">{task.identifier}</p>
					) : null}
				</div>
			</header>

			{update.isError ? (
				<p role="alert" data-testid="save-error" className="text-sm text-xyz-red-6">
					{update.error.message}
					<InvalidFieldList fields={update.error.invalidFields} />
				</p>
			) : null}

			<dl className="grid max-w-md grid-cols-[8rem_1fr] gap-y-3 text-sm">
				<dt className="text-muted-foreground">状态</dt>
				<dd data-testid="detail-done">
					<StatusPicker
						task={task}
						disabled={readOnly || update.isPending}
						onPick={(patch) => update.mutate(patch)}
					/>
				</dd>

				<dt className="text-muted-foreground">到期日</dt>
				<dd data-testid="detail-due-date">{formatApiDate(task.due_date) ?? '未设置'}</dd>
			</dl>

			{/*
			  富文本编辑器（TipTap/ProseMirror）单独切一个 chunk，只有进到任务详情页才下载。
			  它占主包的一大半，而首页/项目树/看板都用不到。
			  占位**不用 role="alert"** —— 这是"还没到"，不是"坏了"，
			  读屏把它播成告警会让人以为出错（沿用 F05a 对 Gantt 占位的同一条区分）。
			*/}
			<Suspense
				fallback={
					<p data-testid="description-loading" className="text-sm text-muted-foreground">
						编辑器加载中…
					</p>
				}
			>
				<DescriptionEditor
					description={task.description}
					disabled={readOnly}
					onSave={(description) => update.mutate({ description })}
				/>
			</Suspense>

			<div className="grid max-w-2xl gap-6 md:grid-cols-2">
				<LabelSelector taskId={taskId} disabled={readOnly} />
				<AssigneeSelector taskId={taskId} projectId={task.project_id} disabled={readOnly} />
			</div>

			{/* 评论的改/删 = 作者 且 对任务有写权限，两个条件的与（见 comment-permissions.ts） */}
			<CommentSection taskId={taskId} canWriteTask={!readOnly} />

			{readOnly ? (
				<p data-testid="read-only-notice" className="text-sm text-muted-foreground">
					你对该任务只有只读权限，不能修改。
				</p>
			) : (
				<div className="grid max-w-md gap-4">
					<Field label={t('task.attributes.priority')} htmlFor="task-priority">
						<select
							id="task-priority"
							data-testid="task-priority"
							value={task.priority ?? 0}
							disabled={update.isPending}
							className="h-9 rounded-md border border-input bg-background px-3 text-sm"
							onChange={(event) => update.mutate({ priority: Number(event.target.value) })}
						>
							{PRIORITIES.map((item) => (
								<option key={item.value} value={item.value}>
									{t(item.labelKey)}
								</option>
							))}
						</select>
					</Field>

					<DueDateField task={task} disabled={update.isPending} onSave={update.mutate} />
				</div>
			)}
		</section>
	);
}

/**
 * 到期日。`<input type="date">` 收发的是 `YYYY-MM-DD`，
 * 而 API 要 RFC3339；清空要发**零值字符串**而不是 null（发 null 会 412）。
 */
const STATUS_OPTIONS: { key: BoardStatus; label: string }[] = [
	{ key: 'todo', label: '待办' },
	{ key: 'doing', label: '进行中' },
	{ key: 'done', label: '已完成' },
];

/**
 * 三态状态控件（待办/进行中/已完成），与看板一致。task 没有独立状态字段，
 * 由 done + percent_done 推导（`statusOf`），点击写回对应字段：
 * - 待办：done=false, percent=0
 * - 进行中：done=false，进度给个非零值（已在 1–99 之间则保留，否则 50）
 * - 已完成：done=true
 * 指派不会因此丢失 —— `useUpdateTask` 会把当前 assignees 一并回传。
 */
function StatusPicker({
	task,
	disabled,
	onPick,
}: {
	task: Task;
	disabled: boolean;
	onPick: (patch: { done: boolean; percent_done?: number }) => void;
}) {
	const current = statusOf(task);
	function patchFor(status: BoardStatus): { done: boolean; percent_done?: number } {
		if (status === 'done') return { done: true };
		if (status === 'todo') return { done: false, percent_done: 0 };
		const pd = task.percent_done;
		return { done: false, percent_done: pd && pd > 0 && pd < 100 ? pd : 50 };
	}
	return (
		<div className="inline-flex rounded-md border border-xyz-gray-4 p-0.5" role="group" aria-label="任务状态">
			{STATUS_OPTIONS.map(({ key, label }) => (
				<button
					key={key}
					type="button"
					data-testid="status-option"
					data-status={key}
					data-active={current === key}
					aria-pressed={current === key}
					disabled={disabled || current === key}
					onClick={() => onPick(patchFor(key))}
					className={cn(
						'rounded px-2.5 py-0.5 text-xs transition-colors',
						current === key
							? 'bg-xyz-blue-6 text-white'
							: 'text-xyz-gray-6 hover:bg-xyz-gray-2 disabled:opacity-50',
					)}
				>
					{label}
				</button>
			))}
		</div>
	);
}

function DueDateField({
	task,
	disabled,
	onSave,
}: {
	task: Task;
	disabled: boolean;
	onSave: (patch: { due_date: string }) => void;
}) {
	const t = useTranslation();
	const current = parseApiTime(task.due_date);
	const inputValue = current ? formatApiDate(task.due_date)! : '';

	return (
		<Field label={t('task.attributes.dueDate')} htmlFor="task-due-date">
			<div className="flex items-center gap-2">
				<Input
					id="task-due-date"
					type="date"
					value={inputValue}
					disabled={disabled}
					onChange={(event) => {
						const raw = event.target.value;
						// 清空 → 零值字符串，不是 null
						if (!raw) {
							onSave({ due_date: ZERO_TIME });
							return;
						}
						// date input 给的是本地日期，按当地 00:00 转成 RFC3339
						const parsed = new Date(`${raw}T00:00:00`);
						if (Number.isNaN(parsed.getTime())) return;
						onSave({ due_date: toApiTime(parsed) });
					}}
				/>
				{current ? (
					<Button
						type="button"
						variant="outline"
						size="sm"
						data-testid="clear-due-date"
						disabled={disabled}
						onClick={() => onSave({ due_date: ZERO_TIME })}
					>
						清除
					</Button>
				) : null}
			</div>
		</Field>
	);
}

/**
 * 字段级错误。
 *
 * ⚠️ 校验失败是 **412 + code 2002 + invalid_fields**，但**不是所有 412 都带
 * invalid_fields** —— 业务规则类的 412（桶满 10004、删最后一列 10003）就没有。
 * 所以这里按 `invalid_fields` 存不存在来渲染，绝不按 status===412 推断它一定有。
 */
function InvalidFieldList({ fields }: { fields?: string[] }) {
	if (!fields?.length) return null;

	return (
		<span data-testid="invalid-fields" className="mt-1 block">
			有问题的字段：{fields.join('、')}
		</span>
	);
}
