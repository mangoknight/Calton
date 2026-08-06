import { apiClient, type CaltonClient } from './client';
import type { Paginated } from './pagination';

/**
 * 标签端点（终稿 §5.1 的"标签 (9)"组）。字段 snake_case 直吃。
 *
 * 全组口径来自 tester 在 Go 参考服务上的 33 条实测语料
 * （`server/harness/corpus-incoming/corpus/_labels.yaml`，全部 `evidence: measured`）。
 * 下面三条是本组最容易被"顺手改好"的地方，逐条抄在这里，改动前先回去看语料。
 *
 * ## ⚠️ 一、`PUT /labels` 没有任何标题校验
 *
 * 空标题、甚至空 JSON 体，都返回 **201**（`label.create.empty_title_is_accepted`、
 * `label.create.empty_body_is_accepted`）。这与项目/任务完全不同 —— 项目空标题是 400/3001。
 *
 * **前端不许替后端补这道校验。** 补了之后 UI 拦住用户、而 API 是接受的，
 * 两者行为不一致；且前端不在对拍范围内，这种分歧没有任何自动化能发现。
 * 产品若确实要校验，走终稿 §5.3 的例外清单，不要在这里悄悄加。
 *
 * ## ⚠️ 二、权限是三分的，不是两分的
 *
 * 分界线在"读/用 vs 改/删"，**不在"自己的 vs 别人的"**：
 *
 *   读（GET）        自己建的 ∪ 挂在你能看见的任务上的
 *   用（挂到任务）    **同上** —— 可见即可用，不要求是创建者
 *   改/删（标签本身）  **仅限创建者**
 *
 * 所以"能选来挂"的集合**真包含**"能编辑"的集合，两处 UI 必须由两个独立判断驱动。
 * 用同一个判断驱动两处的后果：要么协作者的共享标签在选择器里消失，
 * 要么用户的标签被别人改名 —— 两种错都不抛异常、不进日志。
 * 见 `features/labels/permissions.ts`。
 *
 * ## ⚠️ 三、读路径与写路径对"不存在"的口径**相反**
 *
 *   GET    /labels/9999  → **403** {"code":0,"message":"You don't have the permission to see this"}
 *   POST   /labels/9999  → **404** {"code":8002,"message":"This label does not exist."}
 *   DELETE /labels/9999  → **404** {"code":8002,...}
 *
 * 读路径不泄露存在性，写路径泄露。这不是笔误，是上游实况
 * （`label.read_one.missing_is_403_not_404` 与 `label.update.missing_is_404` 配对取证）。
 * 错误文案必须能容纳这两种，别统一成一句。见 `labelWriteErrorMessage`。
 */

/** 标签的创建者。⚠️ 内嵌的是**完整 user 对象**而不是 id，且**不含 email**（标签可被协作者读到）。 */
export interface LabelUser {
	id: number;
	username?: string;
	/** 实测为空串而不是缺失。 */
	name?: string;
	created?: string;
	updated?: string;
}

export interface Label {
	id: number;
	title: string;
	/** ⚠️ 未设置时是**空串**，不是 null。 */
	description?: string;
	/** ⚠️ **不带前导 `#`**（实测形如 `"e8e8e8"`）。与 `<input type="color">` 的格式差一个 `#`。 */
	hex_color?: string;
	/**
	 * ⚠️ 判"能不能改/删"只能靠它。
	 * 类型带 null 是防御性的：bulk 端点的回显体里 `created_by` 确实是 null
	 * （`tasklabel.bulk.response_echoes_input_unhydrated`），万一哪个读路径也这样，
	 * 权限判断要**失败关闭**（判不出创建者 = 不给改），而不是崩掉或放行。
	 */
	created_by?: LabelUser | null;
	created?: string;
	updated?: string;
}

/**
 * 写标签的完整请求体。
 *
 * ⚠️ **三列全部必填不是风格洁癖** —— `POST /labels/{id}` 是**全量替换**而不是 PATCH
 * （`label.update.is_full_replacement`：只提交 `title`，`description` 与 `hex_color`
 * 双双被重置成空串，且接口照样 200、返回体"看着对"）。
 *
 * 把三列声明成必填，是让"漏一列"变成**编译期错误**而不是运行期的静默清空 ——
 * 后者在 UI 上的表现是"改个名字，颜色和描述没了"，而且没有任何报错。
 */
export interface LabelWritePayload {
	title: string;
	description: string;
	hex_color: string;
}

/**
 * 标签列表 = 当前用户的**可见集合**（自己建的 ∪ 挂在自己能看见的任务上的）。
 *
 * ⚠️ 返回的标签里**有一部分是别人建的、你改不了** —— 列表本身不做创建者过滤
 * （过滤了会让协作者贴的标签凭空消失）。哪些能改由 `canManageLabel` 单独判。
 *
 * 该端点走通用 ReadAll，**发分页头**（语料里断言了 `X-Pagination-Result-Count`），
 * 所以不在 `unpaginated-endpoints.ts` 豁免名单里，缺头照旧抛 ContractViolationError。
 */
export function listLabels(
	params: { page?: number; per_page?: number; s?: string } = {},
	client: CaltonClient = apiClient,
): Promise<Paginated<Label>> {
	return client.requestList<Label>('GET', '/labels', {
		query: { per_page: LABELS_PER_PAGE, ...params },
	});
}

/** 上游 per_page 的默认值与上限都是 50，超了会被静默截断。 */
export const LABELS_PER_PAGE = 50;

/**
 * ⚠️ v1 里 **PUT 才是新建**。
 * 且**没有校验** —— 空标题、空 body 都返回 201，见文件头第一条。
 */
export function createLabel(payload: LabelWritePayload, client: CaltonClient = apiClient) {
	return client.put<Label>('/labels', payload);
}

/** ⚠️ v1 里 **POST 是全量替换更新**。`payload` 必须是完整的三列，见 `LabelWritePayload`。 */
export function updateLabel(
	labelId: number,
	payload: LabelWritePayload,
	client: CaltonClient = apiClient,
) {
	return client.post<Label>(`/labels/${labelId}`, payload);
}

export function deleteLabel(labelId: number, client: CaltonClient = apiClient) {
	return client.delete<{ message: string }>(`/labels/${labelId}`);
}

/** 标签不存在时写路径返回的错误码（404 携带）。读路径不用它 —— 读不存在的是 403/0。 */
export const ERR_LABEL_DOES_NOT_EXIST = 8002;

/**
 * 以下三个函数服务**任务上的标签**（挂/摘），由 F08c 的标签选择器使用。
 * 上面那组服务标签本身的 CRUD（F10 管理页）。两组职责不同，别互相套用：
 *
 * ⛔ **选择器的候选集不要用 `canManageLabel` 过滤。**
 * 按文件头第二条，"能用"的集合真包含"能改"的集合 —— 用改/删那一档去筛选择器，
 * 会让协作者的共享标签从选择器里消失（用户明明能挂却选不到，且无任何提示）。
 * 选择器里唯一该做的过滤是"已挂上的不再出现在候选里"，那是去重不是权限。
 */

/** 任务当前挂着的标签。 */
export function listTaskLabels(
	taskId: number,
	client: CaltonClient = apiClient,
): Promise<Paginated<Label>> {
	return client.requestList<Label>('GET', `/tasks/${taskId}/labels`, {
		query: { per_page: LABELS_PER_PAGE },
	});
}

/**
 * 打标签。⚠️ v1 里 PUT 才是新建，body 的键是 **`label_id`**
 * （`LabelTask.LabelID` 的 json tag，label_task.go:39）。
 */
export function addLabelToTask(
	taskId: number,
	labelId: number,
	client: CaltonClient = apiClient,
): Promise<unknown> {
	return client.put(`/tasks/${taskId}/labels`, { label_id: labelId });
}

/** 摘标签。标签 id 走路径段。 */
export function removeLabelFromTask(
	taskId: number,
	labelId: number,
	client: CaltonClient = apiClient,
): Promise<unknown> {
	return client.delete(`/tasks/${taskId}/labels/${labelId}`);
}
