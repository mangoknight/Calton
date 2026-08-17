import { useState } from 'react';

import type { TaskComment } from '@/api/comments';
import { Button } from '@/components/ui/button';
import { useCurrentUser } from '@/features/auth/queries';
import { useTranslation } from '@/i18n/context';
import { formatApiDate } from '@/lib/datetime';
import { canModifyComment, isBlankComment } from './comment-permissions';
import {
	useComments,
	useCreateComment,
	useDeleteComment,
	useUpdateComment,
} from './comment-queries';

/**
 * 评论区（F09）。发/编辑/删即时反映在列表。
 *
 * 改/删按钮的可见性由 `canModifyComment` 决定 —— **作者 且 对任务有写权限**，
 * 两个条件的与。别把其中任何一条单独拿来用，理由见 `comment-permissions.ts`。
 */
export function CommentSection({
	taskId,
	canWriteTask,
}: {
	taskId: number;
	canWriteTask: boolean;
}) {
	const query = useComments(taskId);
	const { data: currentUser } = useCurrentUser();
	const t = useTranslation();
	const create = useCreateComment(taskId);

	const [draft, setDraft] = useState('');
	const [draftError, setDraftError] = useState<string | null>(null);
	const [editingId, setEditingId] = useState<number | null>(null);

	function submit(event: React.FormEvent) {
		event.preventDefault();

		// 后端也会拦（412 + 2002），前端先拦省一次来回；口径一致：只有空白算空
		if (isBlankComment(draft)) {
			setDraftError('评论内容不能为空');
			return;
		}
		setDraftError(null);

		create.mutate(draft, {
			onSuccess: () => setDraft(''),
		});
	}

	return (
		<section className="space-y-4" data-testid="comment-section">
			<h2 className="ink-heading text-base">{t('task.comment.title')}</h2>

			{query.isPending ? <p className="text-sm text-muted-foreground">加载中…</p> : null}

			{query.isError ? (
				<p role="alert" className="text-sm text-xyz-red-6">
					{query.error.message}
				</p>
			) : null}

			{query.isSuccess ? (
				<ul className="space-y-3" data-testid="comment-list">
					{query.data.items.length === 0 ? (
						<li data-testid="comments-empty" className="text-sm text-muted-foreground">
							还没有评论
						</li>
					) : (
						query.data.items.map((comment) => (
							<CommentItem
								key={comment.id}
								taskId={taskId}
								comment={comment}
								canModify={canModifyComment(comment, currentUser?.id, canWriteTask)}
								isEditing={editingId === comment.id}
								onStartEdit={() => setEditingId(comment.id)}
								onStopEdit={() => setEditingId(null)}
							/>
						))
					)}
				</ul>
			) : null}

			{canWriteTask ? (
				<form onSubmit={submit} className="space-y-2" data-testid="comment-form">
					<label htmlFor="new-comment" className="sr-only">
						写评论
					</label>
					<textarea
						id="new-comment"
						data-testid="comment-draft"
						aria-label="写评论"
						rows={3}
						value={draft}
						disabled={create.isPending}
						onChange={(event) => setDraft(event.target.value)}
						className="w-full rounded-md border border-input bg-background p-2 text-sm"
						placeholder={t('task.comment.placeholder')}
					/>

					{draftError ? (
						<p role="alert" data-testid="comment-draft-error" className="text-sm text-xyz-red-6">
							{draftError}
						</p>
					) : null}

					{create.isError ? (
						<p role="alert" data-testid="comment-create-error" className="text-sm text-xyz-red-6">
							{create.error.message}
						</p>
					) : null}

					<Button type="submit" size="sm" data-testid="comment-submit" disabled={create.isPending}>
						{create.isPending ? t('task.comment.creating') : t('task.comment.comment')}
					</Button>
				</form>
			) : null}
		</section>
	);
}

function CommentItem({
	taskId,
	comment,
	canModify,
	isEditing,
	onStartEdit,
	onStopEdit,
}: {
	taskId: number;
	comment: TaskComment;
	canModify: boolean;
	isEditing: boolean;
	onStartEdit: () => void;
	onStopEdit: () => void;
}) {
	const update = useUpdateComment(taskId);
	const remove = useDeleteComment(taskId);
	const t = useTranslation();
	const [text, setText] = useState(comment.comment);
	const [error, setError] = useState<string | null>(null);

	function save(event: React.FormEvent) {
		event.preventDefault();
		if (isBlankComment(text)) {
			setError('评论内容不能为空');
			return;
		}
		setError(null);
		update.mutate({ id: comment.id, comment: text }, { onSuccess: onStopEdit });
	}

	const authorName = comment.author?.name || comment.author?.username || '未知用户';

	return (
		<li
			className="rounded-md border border-border bg-card p-3"
			data-testid="comment-item"
			data-comment-id={comment.id}
		>
			<header className="flex items-center gap-2 text-xs text-muted-foreground">
				<span data-testid="comment-author">{authorName}</span>
				{comment.created ? (
					<time dateTime={comment.created}>{formatApiDate(comment.created)}</time>
				) : null}

				{canModify ? (
					<span className="ml-auto flex gap-2">
						<button
							type="button"
							data-testid={`comment-edit-${comment.id}`}
							aria-label={`编辑评论 ${comment.id}`}
							className="hover:text-foreground"
							onClick={onStartEdit}
						>
							{t('input.editor.edit')}
						</button>
						<button
							type="button"
							data-testid={`comment-delete-${comment.id}`}
							aria-label={`删除评论 ${comment.id}`}
							disabled={remove.isPending}
							className="hover:text-foreground"
							onClick={() => remove.mutate(comment.id)}
						>
							{t('misc.delete')}
						</button>
					</span>
				) : null}
			</header>

			{isEditing ? (
				<form onSubmit={save} className="mt-2 space-y-2">
					<textarea
						data-testid={`comment-edit-box-${comment.id}`}
						aria-label={`编辑评论 ${comment.id} 的内容`}
						rows={3}
						value={text}
						disabled={update.isPending}
						onChange={(event) => setText(event.target.value)}
						className="w-full rounded-md border border-input bg-background p-2 text-sm"
					/>
					{error ? (
						<p role="alert" data-testid="comment-edit-error" className="text-sm text-xyz-red-6">
							{error}
						</p>
					) : null}
					{update.isError ? (
						<p role="alert" className="text-sm text-xyz-red-6">
							{update.error.message}
						</p>
					) : null}
					<div className="flex gap-2">
						<Button
							type="submit"
							size="sm"
							data-testid="comment-edit-save"
							disabled={update.isPending}
						>
							{t('misc.save')}
						</Button>
						<Button
							type="button"
							size="sm"
							variant="outline"
							data-testid="comment-edit-cancel"
							onClick={onStopEdit}
						>
							{t('misc.cancel')}
						</Button>
					</div>
				</form>
			) : (
				<p className="mt-1 whitespace-pre-wrap text-sm text-foreground" data-testid="comment-body">
					{comment.comment}
				</p>
			)}

			{remove.isError ? (
				<p role="alert" className="mt-2 text-sm text-xyz-red-6">
					{remove.error.message}
				</p>
			) : null}
		</li>
	);
}
