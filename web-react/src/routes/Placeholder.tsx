/**
 * F01 只铺骨架：每个 Phase 1 页面先落一个占位，标明由哪个任务实现。
 * 后续任务替换掉对应占位即可，路由表不用再动。
 */
export function Placeholder({ title, owner }: { title: string; owner: string }) {
	return (
		<section className="p-6" data-testid="placeholder">
			<h1 className="text-lg font-semibold text-foreground">{title}</h1>
			<p className="mt-2 text-sm text-muted-foreground">待 {owner} 实现</p>
		</section>
	);
}
