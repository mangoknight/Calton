import type { Label } from '@/api/labels';
import type { CaltonError } from '@/api/errors';
import { ERR_LABEL_DOES_NOT_EXIST } from '@/api/labels';

/**
 * 标签权限（tester 实测定案，见 `_labels.yaml` 文件头的三分表）。
 *
 * ## 为什么这里只有"能不能改/删"一个函数，没有"能不能看/用"
 *
 * 因为"能看/能用"**不需要前端判断** —— `GET /labels` 返回什么就是能看什么，
 * 而"能用（挂到任务上）"的判据与"能看"完全相同（`tasklabel.add.readable_others_label_ok`：
 * alice 把 bob 建的标签挂到自己任务上是 **201**）。给"用"再加一道创建者过滤，
 * 就会把协作项目里的共享标签变成"选择器里能选、一点就 403"。
 *
 * 需要前端判断的只有"改/删"这一档，它**严格更窄**：仅限创建者。
 * 两处 UI 必须由两个独立的东西驱动（列表 = 接口返回的全集，按钮 = 本函数），
 * 用同一个判断驱动两处，两个方向的错都会静默发生。
 */

/**
 * 能否修改/删除这个标签本身 —— **仅限创建者**。
 *
 * 判不出创建者时**失败关闭**（返回 false）：宁可少给一个按钮，
 * 也不要给出一个点下去必然 403 的按钮。`created_by` 在某些端点的回显体里确实是 null
 * （`tasklabel.bulk.response_echoes_input_unhydrated`），所以这个分支不是假想。
 *
 * `currentUserId` 未加载完成（undefined）时同样返回 false —— 用户信息还没到，
 * 此刻放行等于按"未知身份"给权限。
 */
export function canManageLabel(label: Label, currentUserId: number | undefined): boolean {
	if (currentUserId === undefined) return false;
	const creatorId = label.created_by?.id;
	if (creatorId === undefined) return false;
	return creatorId === currentUserId;
}

/**
 * 写路径（改/删）的错误文案。
 *
 * ⚠️ 404 与 403 在这里是**两件不同的事**，不能统一成一句：
 *   - 404 / 8002：标签真的没了（多半是别人刚删掉，而本地列表还是旧的）→ 刷新就好
 *   - 403：标签还在，但你不是创建者 → 刷新也没用，得让创建者来改
 * 统一成"操作失败"会让第一种情况的用户一直重试，第二种情况的用户一直刷新。
 *
 * ⚠️ 读路径**不要**用这个函数：读不存在的标签返回的是 **403 而不是 404**
 * （`label.read_one.missing_is_403_not_404`），套进来会把"不存在"说成"不是创建者"。
 * 本页面不走读单个标签的路径，故不提供对应映射；将来要用先回语料确认口径。
 */
export function labelWriteErrorMessage(error: CaltonError): string {
	if (error.code === ERR_LABEL_DOES_NOT_EXIST) {
		return '这个标签已经不存在了，可能已被删除。刷新后重试。';
	}
	if (error.status === 403) {
		return '只有标签的创建者才能修改或删除它。';
	}
	return error.message;
}
