import type { Project } from '@/api/projects';

/**
 * 权限枚举（终稿 §2.4）：Read=0, Write=1, Admin=2，无权限 -1。
 *
 * ★ 移动项目（reparent）是**三道闸**，不是"能写就能移"（tester 实测逐格验证）：
 *
 *   1. 新 parent > 0 且有变化 → 对新父级 CanWrite（较弱，被第 3 道覆盖）
 *   2. parent 有变化          → 对【被移动的项目】IsAdmin
 *   3. 新 parent > 0          → 对【新父级】IsAdmin
 *      detach 到顶层不走第 3 道（没有新父级），但仍要过第 2 道
 *
 * 也就是说：**能看见、甚至能改标题（write 就够），不等于能移动它**；
 * **对目标父级能写，也不等于能往里挂**。
 *
 * 这不是文档洁癖，是两个 CVE 的补丁（GHSA-2vq4-854f-5c72 / CVE-2026-35595，
 * GHSA-44v6-7fxq-vgf4 / CVE-2026-55064）：递归权限 CTE 会从任一自己拥有的祖先
 * 级联 Admin，所以把共享子项目挪到攻击者自己的根下就能拿到该子项目的 Admin；
 * 而 detach 到顶层会切断原 owner 的继承权限链。
 *
 * UI 的责任：把注定 403 的选项**提前禁掉并说明原因**，而不是让用户提交后吃一个 403。
 */

export const PERMISSION_READ = 0;
export const PERMISSION_WRITE = 1;
export const PERMISSION_ADMIN = 2;

/**
 * ⚠️ 用**精确相等的允许集合**判定，不用 `>= PERMISSION_ADMIN`。
 *
 * 两者今天等价（Admin 是最大值 2），但 `>=` 是 T11 显式论证过要拒绝的写法：
 * 权限不是有序标量，一旦引入新的权限值（比如某个 >2 的特殊角色），`>=` 会
 * 悄悄把它当成 Admin 放行，而集合判定会拒绝——出错方向是"拒绝"而不是"越权"。
 * 终稿 §2.4 的口径就是集合：CanUpdate / CanDelete / IsAdmin → {Admin(2)}。
 */
const ADMIN_PERMISSIONS: readonly number[] = [PERMISSION_ADMIN];

export function isAdmin(project: Project | undefined): boolean {
	return (
		project?.max_permission !== undefined && ADMIN_PERMISSIONS.includes(project.max_permission)
	);
}

/** 第 2 道闸：改动 parent 需要对**被移动的项目**有 Admin。 */
export function canReparent(project: Project | undefined): boolean {
	return isAdmin(project);
}

/** 第 3 道闸：挂到某个父级下需要对**该父级**有 Admin。 */
export function canAttachUnder(parent: Project): boolean {
	return isAdmin(parent);
}
