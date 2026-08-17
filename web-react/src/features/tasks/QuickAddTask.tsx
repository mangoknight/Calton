import { useRef, useState, type FormEvent } from 'react';

import { cn } from '@/lib/utils';
import { useCreateTask } from './create-queries';

/**
 * 行内快速建任务：一个输入框 + 提交按钮，回车即建（F-quickadd）。
 *
 * 只收标题，桶归属交给后端默认桶（见 `createTask` 注释）。挂在 List / Kanban
 * 两个视图顶部，是当前 UI 里**唯一**的建任务入口。
 */
export function QuickAddTask({ projectId }: { projectId: number }) {
	const [title, setTitle] = useState('');
	const inputRef = useRef<HTMLInputElement>(null);
	const create = useCreateTask(projectId);

	function onSubmit(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		const trimmed = title.trim();
		// 空标题（或全是空白）不发请求 —— 后端也会拒，但没必要往返一趟
		if (trimmed === '') return;

		create.mutate(
			{ title: trimmed },
			{
				onSuccess: () => {
					// 成功后清空并把焦点还回输入框，方便连续建多条
					setTitle('');
					inputRef.current?.focus();
				},
			},
		);
	}

	return (
		<form
			onSubmit={onSubmit}
			className="flex shrink-0 items-start gap-2"
			data-testid="quick-add-form"
		>
			<div className="flex min-w-0 flex-1 flex-col gap-1">
				<input
					ref={inputRef}
					type="text"
					value={title}
					onChange={(event) => setTitle(event.target.value)}
					disabled={create.isPending}
					placeholder="添加任务，回车创建…"
					data-testid="quick-add-input"
					className={cn(
						'w-full rounded-md border border-input bg-card px-3 py-2 text-sm text-foreground',
						'placeholder:text-muted-foreground',
						'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
						'disabled:cursor-not-allowed disabled:opacity-60',
					)}
				/>
				{create.isError ? (
					<p
						role="alert"
						data-testid="quick-add-error"
						className="text-xs text-xyz-red-6"
					>
						{create.error.message}
					</p>
				) : null}
			</div>

			<button
				type="submit"
				disabled={create.isPending}
				data-testid="quick-add-submit"
				className={cn(
					'shrink-0 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground',
					'transition-colors hover:bg-primary/90',
					'disabled:cursor-not-allowed disabled:opacity-60',
				)}
			>
				添加
			</button>
		</form>
	);
}
