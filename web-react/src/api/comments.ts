import { apiClient, type CaltonClient } from './client';
import type { Paginated } from './pagination';

/**
 * 任务评论（F09）。
 *
 * ## ⚠️ 一、每个端点的路径里都带 taskID，包括读单条
 *
 * `GET /tasks/{task}/comments/{commentid}` —— **taskID 参与校验**，不是装饰。
 * 只按 commentID 查会让人遍历读别人任务下的评论。所以本模块所有函数都要求
 * 传入 taskId，没有"只给 commentId"的重载。
 *
 * ## ⚠️ 二、作者字段叫 `author`，不是 `created_by`
 *
 * 内嵌的是**完整 user 对象**（`TaskComment.Author`，task_comments.go:34），
 * 而 `AuthorID` 的 json tag 是 `-`（不出现在响应里）。所以判作者只能读
 * `comment.author.id`。写成 `created_by` 会永远取到 undefined ——
 * 那样"仅作者可改"会退化成"谁都不能改"，按钮全不显示，且不报错。
 *
 * ## ⚠️ 三、评论文本必填，空值后端 412
 *
 * `Comment` 字段是 `valid:"dbtext,required"`，空评论返回
 * **412 + code 2002 + `invalid_fields`**。前端也拦一道，省一次来回，
 * 但**不要**把这当成"前端替后端校验"——两边都拦，口径一致。
 * （对比：标签那组后端完全不校验，前端就不许补，见 `api/labels.ts` 文件头第一条。）
 */

export interface CommentAuthor {
	id: number;
	username?: string;
	name?: string;
}

export interface TaskComment {
	id: number;
	/** 评论正文，可能含 HTML。 */
	comment: string;
	/** ⚠️ 字段名是 `author`，不是 `created_by`。 */
	author?: CommentAuthor | null;
	created?: string;
	updated?: string;
}

/** 上游 per_page 的默认值与上限都是 50。 */
export const COMMENTS_PER_PAGE = 50;

export function listComments(
	taskId: number,
	client: CaltonClient = apiClient,
): Promise<Paginated<TaskComment>> {
	return client.requestList<TaskComment>('GET', `/tasks/${taskId}/comments`, {
		query: { per_page: COMMENTS_PER_PAGE },
	});
}

/** ⚠️ v1 里 PUT 才是新建。 */
export function createComment(
	taskId: number,
	comment: string,
	client: CaltonClient = apiClient,
): Promise<TaskComment> {
	return client.put<TaskComment>(`/tasks/${taskId}/comments`, { comment });
}

/**
 * ⚠️ v1 里 POST 是更新。
 *
 * 这里只发 `comment` 一个字段是**对的**，不是漏了全量替换：
 * `TaskComment` 上除正文外全是 `readOnly` 字段（author/created/updated/reactions），
 * 没有"回传不全就被清空"的可写列。这跟 Task 的 15 列全量替换不是一回事。
 */
export function updateComment(
	taskId: number,
	commentId: number,
	comment: string,
	client: CaltonClient = apiClient,
): Promise<TaskComment> {
	return client.post<TaskComment>(`/tasks/${taskId}/comments/${commentId}`, { comment });
}

/** ⚠️ 路径里必须带 taskId —— 见文件头第一条。 */
export function deleteComment(
	taskId: number,
	commentId: number,
	client: CaltonClient = apiClient,
): Promise<unknown> {
	return client.delete(`/tasks/${taskId}/comments/${commentId}`);
}

/** 空评论时后端返回的校验错误码（412 携带）。 */
export const ERR_COMMENT_VALIDATION = 2002;
