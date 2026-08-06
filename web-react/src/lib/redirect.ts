/**
 * 把 ?redirect= 规约成安全的站内相对路径。
 *
 * ⚠️ **使用前提（别删这段）**：返回值只喂给 React Router 的 `navigate()`，
 * **永远不要喂给 `location.href` / `location.assign` / `<a href>`**。
 * navigate() 只在路由表里查路径，不会真的发起跨站跳转；换成 location.href
 * 这类真跳转的消费点，本函数的强度就不够了，必须重新审。
 *
 * 挡掉的形状：
 *  - 不以 / 开头的绝对 URL（https://evil.com）
 *  - 协议相对 URL（//evil.com）—— 浏览器会当成跨站
 *  - 反斜杠变体（/\evil.com、\\evil.com）—— 部分浏览器把 \ 规范化成 /，
 *    当前消费点是 navigate() 所以不可利用，但先堵上，免得将来换消费点变成真漏洞
 *  - 回跳登录/注册页 —— 会形成来回弹
 */
export function safeRedirect(target: string | null): string {
	if (!target) return '/';
	if (!target.startsWith('/') || target.startsWith('//')) return '/';
	if (target.includes('\\')) return '/';
	if (target.startsWith('/login') || target.startsWith('/register')) return '/';
	return target;
}
