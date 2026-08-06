import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { canModifyComment, isBlankComment } from './comment-permissions';

const PERMISSIONS_GO = resolve(process.cwd(), '..', 'pkg/models/task_comment_permissions.go');

const AUTHOR = 901;
const OTHER = 902;

function comment(authorId: number | null) {
	return { author: authorId === null ? null : { id: authorId } };
}

/**
 * ★★ 把"两个条件的与"这条规则钉在 Go 源码上。
 *
 * 只记住其中一条都会出错，而且两种错的方向相反：
 * 只记"仅作者" → 被降成只读的作者看到按钮、点了 403；
 * 只记"有写权限" → 项目管理员能篡改别人的发言。
 */
describe('评论改删规则与 Go 源码对账', () => {
	const source = existsSync(PERMISSIONS_GO) ? readFileSync(PERMISSIONS_GO, 'utf8') : '';

	it('能读到 task_comment_permissions.go（读不到则以下对账是假绿）', () => {
		expect(existsSync(PERMISSIONS_GO)).toBe(true);
	});

	it('★★ Go 侧确实是"任务可写"与"是作者"两个条件的与', () => {
		const start = source.indexOf('func (tc *TaskComment) canUserModifyTaskComment');
		const body = source.slice(start, source.indexOf('func (tc *TaskComment) CanDelete'));

		// ① 任务写权限：不满足直接 return false
		expect(body).toMatch(/canWriteTask, err := t\.CanWrite\(s, a\)/);
		expect(body).toMatch(/if !canWriteTask \{\s*return false, nil\s*\}/);
		// ② 作者本人
		expect(body).toMatch(/return a\.GetID\(\) == savedComment\.AuthorID, nil/);
	});

	it('★ CanUpdate 与 CanDelete 走的是同一个判断（别给它们不同的 UI 口径）', () => {
		expect(source).toMatch(
			/CanDelete\([^)]*\) \(bool, error\) \{\s*return tc\.canUserModifyTaskComment/,
		);
		expect(source).toMatch(
			/CanUpdate\([^)]*\) \(bool, error\) \{\s*return tc\.canUserModifyTaskComment/,
		);
	});

	/**
	 * ★ 作者字段在响应里叫 `author`，`AuthorID` 的 json tag 是 `-`。
	 * 写成 `created_by` 会永远取到 undefined —— "仅作者可改"退化成"谁都不能改"，
	 * 按钮全不显示且不报错。
	 */
	it('★ 响应里的作者字段是 author，AuthorID 不出现在 JSON 里', () => {
		const model = resolve(process.cwd(), '..', 'pkg/models/task_comments.go');
		const modelSource = readFileSync(model, 'utf8');
		expect(modelSource).toMatch(/Author\s+\*user\.User\s+`xorm:"-" json:"author"/);
		expect(modelSource).toMatch(/AuthorID int64\s+`xorm:"not null" json:"-"`/);
	});
});

describe('canModifyComment', () => {
	it('★ 作者 + 有写权限 → 可以', () => {
		expect(canModifyComment(comment(AUTHOR), AUTHOR, true)).toBe(true);
	});

	/** ★ 只记"仅作者"会在这里出错：作者被降成只读后仍显示按钮，点了 403。 */
	it('★ 作者但只读 → 不可以（防"能点、一点就 403"）', () => {
		expect(canModifyComment(comment(AUTHOR), AUTHOR, false)).toBe(false);
	});

	/** ★ 只记"有写权限"会在这里出错：管理员可静默篡改他人发言。 */
	it('★ 非作者但有写权限 → 不可以（防管理员篡改他人发言）', () => {
		expect(canModifyComment(comment(OTHER), AUTHOR, true)).toBe(false);
	});

	it('非作者且只读 → 不可以', () => {
		expect(canModifyComment(comment(OTHER), AUTHOR, false)).toBe(false);
	});

	/** ★ 判不出作者/当前用户时**失败关闭**：不给改，而不是放行或崩掉。 */
	it.each([
		['author 为 null', comment(null), AUTHOR as number | undefined],
		['当前用户未知', comment(AUTHOR), undefined],
		['两者都缺', comment(null), undefined],
	])('★ %s 时失败关闭', (_name, target, currentUserId) => {
		expect(canModifyComment(target, currentUserId, true)).toBe(false);
	});

	it('author 缺字段（undefined）同样失败关闭', () => {
		expect(canModifyComment({}, AUTHOR, true)).toBe(false);
	});
});

describe('isBlankComment', () => {
	it.each([null, undefined, '', '   ', '\n\t '])('%s 判为空', (value) => {
		expect(isBlankComment(value)).toBe(true);
	});

	it.each(['a', ' 有内容 ', '<p>x</p>'])('%s 判为非空', (value) => {
		expect(isBlankComment(value)).toBe(false);
	});

	/**
	 * ★ 口径只到"空白"为止。别顺手加长度下限之类后端没有的规则 ——
	 * 那会造成"UI 拦住、API 却接受"的分歧，而前端不在对拍范围内，
	 * 这种分歧没有任何自动化能发现。
	 */
	it('★ 单个字符是合法评论（不加后端没有的长度规则）', () => {
		expect(isBlankComment('好')).toBe(false);
	});
});
