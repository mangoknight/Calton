import type { TaskComment } from '@/api/comments';

/**
 * 谁能改/删一条评论。
 *
 * ## 后端的真实规则是**两个条件的与**（task_comment_permissions.go:30-54）
 *
 * ```
 * canUserModifyTaskComment =
 *     t.CanWrite(task)               // ① 对这个任务有写权限
 *  && a.GetID() == comment.AuthorID  // ② 且你是这条评论的作者
 * ```
 *
 * 两条都要满足，容易只记住其中一条：
 *
 * - **只记 ②（"仅作者"）** → 一个作者若后来被降成只读，仍会看到删除按钮，
 *   点下去 403。这正是我们一路在防的"能点、一点就 403"。
 * - **只记 ①（"有写权限就行"）** → 项目管理员能改别人的发言。
 *   tester 的警告原话："若并入项目权限体系，管理员可静默篡改他人发言。"
 *
 * 所以 UI 必须同时看这两样，且**不要**因为"项目权限"这四个字就把 ① 去掉 ——
 * ① 在这里不是授权来源，是**额外的限制**。
 *
 * ## 判不出作者时**失败关闭**
 *
 * `author` 可能是 null（防御性口径，与 F10 对 `created_by` 的处理一致）。
 * 判不出来就不给改，而不是放行或崩掉。
 */
export function canModifyComment(
	comment: Pick<TaskComment, 'author'>,
	currentUserId: number | undefined,
	canWriteTask: boolean,
): boolean {
	// ① 没有任务写权限，作者也改不了自己的评论
	if (!canWriteTask) return false;

	// 判不出当前用户或作者 → 失败关闭
	const authorId = comment.author?.id;
	if (typeof authorId !== 'number' || typeof currentUserId !== 'number') return false;

	// ② 必须是作者本人
	return authorId === currentUserId;
}

/**
 * 空评论拦截。
 *
 * 后端 `valid:"dbtext,required"`，空值 412 + code 2002。前端同样拦，
 * 口径要一致：**只有空白算空**，不做任何别的内容判断（别顺手加长度下限之类
 * 后端没有的规则 —— 那会造成"UI 拦住、API 却接受"的分歧）。
 */
export function isBlankComment(text: string | null | undefined): boolean {
	return !text || text.trim().length === 0;
}
