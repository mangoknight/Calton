import { Outlet } from 'react-router-dom';

import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';

/**
 * ⚠️ `h-screen` 而不是 `min-h-screen`：**这一个词决定了页面里所有 sticky 还有没有用。**
 *
 * `min-h-screen` 只给下界，外壳会跟着内容一起长高，于是下面每一层
 * `flex-1 min-h-0 overflow-auto` 的 clientHeight 都等于 scrollHeight ——
 * **一个都不会变成真正的滚动容器**，真正在滚的是 `<html>`。
 * 而 `position: sticky` 只相对**自己那个 overflow 祖先**粘；祖先永远不滚，它就永远不粘。
 *
 * 实测（A3 探针，60 行表格 / 视口 720）：整条链 clientH == scrollH == 4676，
 * 只有 documentElement 是 720/4959；滚动文档 600px 后表头与首行**各移动 -600px**，
 * 即 `thead.sticky top-0` 完全没生效。
 *
 * ⛔ 不要改回 `min-h-screen` —— 它看起来更"安全"（内容再多也不会被裁掉），
 * 但代价是全站 sticky 失效，而且**没有任何单测会红**：jsdom 没有排版，这些几何量全是 0。
 * 守它的是 `e2e/test_table_layout.py`。
 */
export function AppShell() {
	return (
		<div className="flex h-screen flex-col overflow-hidden bg-background">
			<TopBar />
			<div className="flex min-h-0 flex-1">
				<Sidebar />
				<main className="min-w-0 flex-1 overflow-y-auto" data-testid="app-main">
					<Outlet />
				</main>
			</div>
		</div>
	);
}
