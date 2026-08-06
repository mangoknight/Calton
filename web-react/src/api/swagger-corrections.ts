/**
 * 上游 swagger 标注与真实路由不符的地方（以 routes.go 为准，team lead 已证实）。
 *
 * `src/api/generated.ts` 是从 swagger 生成的，**这三处会跟着错**。生成的类型在这几个
 * 端点上不可信，必须以本文件为准。后端侧的修正登记在 contract/swagger-corrections.yaml。
 *
 * 之所以做成常量而不是只写注释：写注释挡不住有人照着生成的类型去调，
 * 常量被 import 才会在改动时暴露出来。
 */

export const SWAGGER_CORRECTIONS = {
	/** swagger 里没写，但路由真实存在。 */
	missingFromSwagger: ['GET /token/test', 'GET /projects/{project}/tasks'] as const,

	/**
	 * ★ 最容易踩的一条：label 更新的真实动词是 POST，swagger 写成 PUT。
	 * 照 swagger 用 PUT 调会 404。
	 * 与 v1 的整体规律一致 —— PUT 新建、POST 全量替换更新（终稿 §1.1）。
	 */
	labelUpdate: { method: 'POST', path: '/labels/{label}', swaggerSaid: 'PUT' } as const,
} as const;

/** F10 标签管理页更新标签时用这个，别照 generated.ts 的 PUT。 */
export function labelUpdatePath(labelId: number | string) {
	return `/labels/${labelId}`;
}

export const LABEL_UPDATE_METHOD = 'POST';
