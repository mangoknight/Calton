import { AlertTriangle } from 'lucide-react';

import type { Project } from '@/api/projects';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { useCurrentUser } from '@/features/auth/queries';
import { useDeleteProject } from '@/features/projects/mutations';
import { collectSubtree, foreignOwnedProjects } from '@/features/projects/tree';
import { useTranslation } from '@/i18n/context';

/**
 * 删除确认。级联行为已由 tester 在 Go 参考服务上实测定案：
 *
 *  1. **完全递归级联**：整棵子树一起删，不是只删一层，也不会把子项目提升为顶层。
 *  2. **任务是物理删除**：`tasks` 表虽然有 `deleted_at`（软删机制存在），但项目级联走硬删，
 *     库里 0 行残留。**没有回收站语义，删了就是没了** —— 文案必须说"不可恢复"。
 *  3. ★ **级联跨越所有权边界**：alice 删掉自己的 P3，挂在 P3 下、由 bob 拥有的项目和
 *     bob 的任务会被一起硬删，接口返回 200 且**不给任何提示**。
 *
 * 第 3 条是唯一一条会让用户"删掉自己本来无权删的东西"的路径，而 API 不拦也不提示，
 * 只能由 UI 把后果说出来 —— 这也是本弹窗要拿整棵子树而不只是直接子项目的原因。
 */
export function DeleteProjectDialog({
	project,
	allProjects,
	onOpenChange,
}: {
	project: Project | null;
	allProjects: Project[];
	onOpenChange: (open: boolean) => void;
}) {
	const remove = useDeleteProject();
	const { data: currentUser } = useCurrentUser();
	const t = useTranslation();

	if (!project) return null;

	const subtree = collectSubtree(allProjects, project.id);
	const descendants = subtree.filter((item) => item.id !== project.id);
	const foreign = foreignOwnedProjects(subtree, currentUser?.id);

	return (
		<Dialog open onOpenChange={onOpenChange}>
			<DialogContent data-testid="project-delete-dialog">
				<DialogTitle className="ink-heading text-lg">{t('project.delete.header')}</DialogTitle>
				<DialogDescription>
					确定删除「{project.title}」吗？项目下的任务将被永久删除，不可恢复（没有回收站）。
					{descendants.length > 0
						? `该项目下的 ${descendants.length} 个子项目及其任务也会被一并永久删除。`
						: ''}
				</DialogDescription>

				{foreign.length > 0 ? (
					<div
						role="alert"
						data-testid="foreign-owner-warning"
						className="mt-3 rounded-md border border-xyz-red-5 bg-xyz-red-1 p-3"
					>
						<p className="flex items-center gap-2 text-sm font-medium text-xyz-red-7">
							<AlertTriangle className="size-4" aria-hidden />
							以下 {foreign.length} 个项目属于其他成员，将一并永久删除
						</p>
						<ul className="mt-2 space-y-1">
							{foreign.map((item) => (
								<li key={item.id} className="text-sm text-xyz-red-7">
									{item.title}
									{item.owner?.username ? `（${item.owner.username}）` : ''}
								</li>
							))}
						</ul>
					</div>
				) : null}

				{remove.isError ? (
					<p role="alert" className="mt-3 text-sm text-xyz-red-6">
						{remove.error.message}
					</p>
				) : null}

				<div className="mt-4 flex justify-end gap-2">
					<Button
						type="button"
						variant="outline"
						data-testid="project-delete-cancel"
						onClick={() => onOpenChange(false)}
					>
						{t('misc.cancel')}
					</Button>
					<Button
						type="button"
						variant="destructive"
						data-testid="project-delete-confirm"
						disabled={remove.isPending}
						onClick={() => remove.mutate(project.id, { onSuccess: () => onOpenChange(false) })}
					>
						{remove.isPending ? t('misc.loading') : t('misc.delete')}
					</Button>
				</div>
			</DialogContent>
		</Dialog>
	);
}
