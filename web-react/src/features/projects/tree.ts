import type { Project } from '@/api/projects';

/**
 * 扁平项目列表 → 树。
 *
 * ★ 核心约束：**后端不保证 parent 链无环**。`parent_project_id` 只是一个 int 字段，
 * A→B→A 这种数据一旦出现，天真的递归会直接把浏览器标签页挂死。
 * 这里在**建树阶段**就把环剔出来（而不是靠渲染层限深硬扛），保证返回的 roots
 * 一定是有限深度的真树，渲染层可以放心递归。
 *
 * 处理策略：
 *  - 从"顶层项目"（parent_project_id 为 0 / 指向不存在的项目）出发向下建树
 *  - 凡是从任何顶层都到不了的节点，必然处在环里（或挂在环上）→ 收进 `cycles`
 *  - 不静默丢弃：cycles 里的项目会被 UI 平铺出来并告警，否则用户会觉得项目"凭空消失"
 */

export interface ProjectNode {
	project: Project;
	children: ProjectNode[];
	depth: number;
}

export interface ProjectTree {
	roots: ProjectNode[];
	/** 因为 parent 成环而无法挂进树的项目，按 id 升序。 */
	cycles: Project[];
}

/** 顶层的判定：0 是上游表示"无父项目"的值；负数与 NaN 一并当顶层处理。 */
function isTopLevel(project: Project, byId: Map<number, Project>): boolean {
	const parentId = project.parent_project_id;
	if (!parentId || parentId <= 0) return true;
	// 父项目不在返回集里（无权限/已归档被过滤）——挂不上去，按顶层渲染，
	// 否则整棵子树都会消失
	return !byId.has(parentId);
}

/**
 * `GET /projects` 里会混进**负 ID 的伪项目** —— 它们是 saved filter 的投影
 * （如 id=-2 "My Open Tasks"，parent_project_id=0），实测确认存在。
 * 不过滤的话它们会作为顶层节点混在真实项目里冒出来。
 * 伪项目的呈现归 F11 保存过滤器管，不进项目树（team lead 裁决）。
 */
export function isPseudoProject(project: Project): boolean {
	return project.id < 0;
}

export function buildProjectTree(input: Project[]): ProjectTree {
	const projects = input.filter((project) => !isPseudoProject(project));

	const byId = new Map<number, Project>();
	for (const project of projects) byId.set(project.id, project);

	const childrenOf = new Map<number, Project[]>();
	const roots: Project[] = [];

	for (const project of projects) {
		if (isTopLevel(project, byId)) {
			roots.push(project);
			continue;
		}
		// isTopLevel 已排除 0/null/undefined，走到这里一定是个正整数
		const parentId = project.parent_project_id as number;
		const siblings = childrenOf.get(parentId);
		if (siblings) siblings.push(project);
		else childrenOf.set(parentId, [project]);
	}

	/**
	 * 从顶层向下展开。
	 *
	 * **不死循环的真正依据**：展开只从顶层项目出发，而环里的节点根本没有顶层祖先，
	 * 所以永远走不到它们——环不是"被检测出来的"，是压根进不来。visited 同时兼作
	 * "谁被挂进树了"的记录，剩下的就是环内节点。
	 *
	 * 下面那行 `!visited.has(child.id)` 是条廉价的保险绳，**实测不是承重的**：
	 * 去掉它 13 条用例照样全绿（每个项目只有一个 parent，同一节点不会被挂两次）。
	 * 留着是为了万一将来有人改了 roots 的选取逻辑，别直接退化成死循环。
	 * 如实记一笔，免得后人误以为它有测试保护。
	 */
	const visited = new Set<number>();

	function expand(project: Project, depth: number): ProjectNode {
		visited.add(project.id);
		const children = (childrenOf.get(project.id) ?? [])
			.filter((child) => !visited.has(child.id))
			.map((child) => expand(child, depth + 1));

		return { project, children, depth };
	}

	// sortTree 会递归排每一层（含根层），所以这里不用先排一次
	const tree = roots.map((root) => expand(root, 0));
	sortTree(tree);

	const cycles = projects.filter((project) => !visited.has(project.id)).sort((a, b) => a.id - b.id);

	return { roots: tree, cycles };
}

function sortTree(nodes: ProjectNode[]): void {
	nodes.sort((a, b) => {
		const positionDiff = (a.project.position ?? 0) - (b.project.position ?? 0);
		if (positionDiff !== 0) return positionDiff;
		const titleDiff = a.project.title.localeCompare(b.project.title);
		return titleDiff !== 0 ? titleDiff : a.project.id - b.project.id;
	});
	for (const node of nodes) sortTree(node.children);
}

/**
 * 收集一个项目及其**全部后代**（用于删除前算清影响面）。
 *
 * 删除是完全递归的硬删除（实测：三层 P→C1→C2，删 P 后三个全部 404），
 * 所以"会删掉什么"必须按整棵子树算，不能只看直接子项目。
 * 同样用 visited 防成环数据把这里变成死循环 —— 这个函数的入口是任意节点，
 * 不像建树那样只从顶层进入，所以这里的环防护**是承重的**。
 */
export function collectSubtree(projects: Project[], rootId: number): Project[] {
	const childrenOf = new Map<number, Project[]>();
	for (const project of projects) {
		const parentId = project.parent_project_id;
		if (!parentId || parentId <= 0) continue;
		const siblings = childrenOf.get(parentId);
		if (siblings) siblings.push(project);
		else childrenOf.set(parentId, [project]);
	}

	const root = projects.find((project) => project.id === rootId);
	if (!root) return [];

	const visited = new Set<number>([rootId]);
	const result: Project[] = [root];

	for (let i = 0; i < result.length; i += 1) {
		for (const child of childrenOf.get(result[i].id) ?? []) {
			if (visited.has(child.id)) continue;
			visited.add(child.id);
			result.push(child);
		}
	}

	return result;
}

/** 子树里不属于指定用户的项目 —— 删除会连它们一起硬删，UI 必须显著警示。 */
export function foreignOwnedProjects(subtree: Project[], currentUserId?: number): Project[] {
	if (currentUserId === undefined) return [];
	// owner 缺失时不当作"别人的"，避免在数据不全时误报把用户吓住
	return subtree.filter((project) => project.owner && project.owner.id !== currentUserId);
}

/** 拍平成渲染顺序（先序），给"展开的树"做列表渲染时用。 */
export function flattenTree(nodes: ProjectNode[]): ProjectNode[] {
	return nodes.flatMap((node) => [node, ...flattenTree(node.children)]);
}
