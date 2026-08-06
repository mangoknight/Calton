import { describe, expect, it } from 'vitest';

import type { Project } from '@/api/projects';
import {
	canAttachUnder,
	canReparent,
	isAdmin,
	PERMISSION_ADMIN,
	PERMISSION_READ,
	PERMISSION_WRITE,
} from './permissions';

function project(max_permission?: number): Project {
	return { id: 1, title: 'P1', max_permission };
}

describe('reparent 三道闸的前端预判', () => {
	it.each([
		[PERMISSION_READ, false],
		[PERMISSION_WRITE, false],
		[PERMISSION_ADMIN, true],
	])('max_permission=%s → isAdmin=%s', (permission, expected) => {
		expect(isAdmin(project(permission))).toBe(expected);
	});

	it('★ 第 2 道闸：write 能改标题，但不能移动项目', () => {
		// 实测：write 非 owner 普通改名 200，detach 到顶层 403
		expect(canReparent(project(PERMISSION_WRITE))).toBe(false);
		expect(canReparent(project(PERMISSION_ADMIN))).toBe(true);
	});

	it('★ 第 3 道闸：对目标父级"能写"不等于"能往里挂"', () => {
		// 实测：owner 对新父级只有 write → 403
		expect(canAttachUnder(project(PERMISSION_WRITE))).toBe(false);
		expect(canAttachUnder(project(PERMISSION_ADMIN))).toBe(true);
	});

	it('max_permission 缺失时按无权限处理，不放行', () => {
		expect(isAdmin(project(undefined))).toBe(false);
		expect(isAdmin(undefined)).toBe(false);
	});

	/**
	 * 用集合判定而不是 `>= Admin`：权限不是有序标量。
	 * 将来若引入 >2 的新权限值，`>=` 会悄悄把它当 Admin 放行，
	 * 集合判定则拒绝 —— 出错方向是"拒绝"而非"越权"。
	 */
	it('未知的更大权限值不被当成 Admin 放行', () => {
		expect(isAdmin(project(3))).toBe(false);
		expect(isAdmin(project(99))).toBe(false);
	});

	it('无权限(-1)不放行', () => {
		expect(isAdmin(project(-1))).toBe(false);
	});
});
