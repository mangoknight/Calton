import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach } from 'vitest';

import zhCN from '@/i18n/lang/zh-CN.json';
import { primeLocaleMessages } from '@/i18n/messages';
import type { Messages } from '@/i18n/translate';
import { useUIStore } from '@/store/ui';
import { server } from './msw';

/**
 * 单测一律跑 **zh-CN**（F13）。
 *
 * ⚠️ 两件事都得做，缺一不可：
 *
 * 1. **同步塞进语言包**。真实装载是动态 import（只有 en 静态打包，见
 *    `i18n/messages.ts` 的包体预算说明），异步会给每条断言加一层竞态。
 * 2. **显式设定 locale**。不设的话走浏览器语言，而 jsdom 的
 *    `navigator.language` 是 **en-US** —— 于是所有已迁到 `t()` 的组件
 *    在测试里显示英文，而在真实的中文浏览器里显示中文。
 *    那种"测试和真实环境语言不同"的偏差，排查起来极贵。
 */
primeLocaleMessages('zh-CN', zhCN as unknown as Messages);

beforeEach(() => {
	// afterEach 只清了 localStorage，zustand 的内存状态还在，所以每条用例前重设
	useUIStore.setState({ locale: 'zh-CN' });
});

/**
 * jsdom 自带 AbortController/AbortSignal，而 Request/fetch 来自 Node 的 undici，
 * undici 要求 signal 是它同一 realm 的 AbortSignal —— React Router v7 每次导航都会
 * 用 jsdom 的 signal 构造 Request，于是抛
 * "RequestInit: Expected signal to be an instance of AbortSignal"。
 * 这里把 signal 摘掉。先做特性检测：哪天 jsdom/undici 对齐了，这段自动不生效。
 *
 * ⚠️ **已知偏差**：client.ts 是暴露了 signal 能力的（RequestOptions.signal），
 * 但本环境下这个补丁让它静默变成空操作 —— 也就是说"401 刷新重试期间取消在途请求"
 * "组件卸载时中止请求"这类 abort 语义在 jsdom 里**测了也是假绿**，单测通过不代表
 * abort 行为正确。真实覆盖记在 F14（生产构建 E2E），tester 已登记
 * "abort × 单飞刷新竞态"用例。
 */
function patchRequestSignalIfIncompatible() {
	const controller = new AbortController();
	try {
		new Request('http://localhost/', { signal: controller.signal });
		return;
	} catch {
		// 不兼容，继续打补丁
	}

	const BaseRequest = globalThis.Request;
	class JsdomCompatRequest extends BaseRequest {
		constructor(input: RequestInfo | URL, init?: RequestInit) {
			super(input, init?.signal ? { ...init, signal: undefined } : init);
		}
	}
	globalThis.Request = JsdomCompatRequest as unknown as typeof Request;
}

patchRequestSignalIfIncompatible();

/**
 * jsdom **没有排版引擎**，所以一切"返回几何量"的 DOM API 它要么给零、要么根本没有。
 * ProseMirror（TipTap 的底座）做光标定位时要用它们，撞上缺失的就抛。
 *
 * ## 这修的是什么
 *
 * 修之前：`vitest run` **782 passed / 0 failed，退出码却是 1** —— 65 条
 * "Unhandled Error"。CI 只看退出码，于是那道闸门**恒红**，而恒红的闸门等于没有闸门：
 * 它会被习惯性忽略，然后某天真正的失败混进去没人看见（实践第 30 条）。
 *
 * ## 65 条错误是**一个根因、两个缺失的 API**（实测，不是推断）
 *
 * | 抛的地方 | 缺的 API | 条数 |
 * |---|---|---|
 * | `singleRect` ← `coordsAtPos` ← `scrollToSelection` | `Range.getClientRects` | 33 |
 * | `posAtCoords` ← mousedown 处理器 | `document.elementFromPoint` | 26 |
 *
 * 实测 jsdom 的现状：`Element.getClientRects` **有**（返回空列表）、
 * `Element.getBoundingClientRect` **有**（全零），而 `Range` 上这两个、
 * 以及 `document.elementFromPoint` / `caretRangeFromPoint` **都没有**。
 * 也就是说 jsdom 自己对"没有排版"的表达方式是**空列表 / 全零**，不是抛错。
 * 这里补的就是让 Range 与 elementFromPoint 说同一种话。
 *
 * ## 为什么不是"全局吞掉未处理异常"
 *
 * 那是把闸门拆了。这里补的是**运行环境缺的能力**：补完之后 ProseMirror 那条
 * 代码路径会真的跑完（`singleRect` 拿到空列表就回落到 `getBoundingClientRect`；
 * `posAtCoords` 拿到 null 会走 `inRect` 判断并返回 null，调用方 `mousedown`
 * 对 null 有处理）—— 这些回落分支都是**上游自己写的**，我们没有改变它的行为。
 * 我们自己代码里真正的未处理异常，照样会让退出码变 1。
 *
 * ## ⚠️ 已知偏差（与上面 signal 那条同一性质，必须一起读）
 *
 * 补出来的几何量**全是零**。所以任何**依赖真实坐标**的行为
 * （光标落点、点击位置命中哪个节点、滚动到选区）在 jsdom 里**测了也是假绿**。
 * 富文本的这类行为真实覆盖记在 **F14（生产构建 E2E）**。
 * ⛔ 不要因为这里"补上了"就以为坐标相关的逻辑有单测覆盖。
 *
 * 三个都做特性检测：哪天 jsdom 自己实现了，这段自动不生效。
 */
function patchMissingLayoutApis() {
	const ZERO_RECT = {
		x: 0,
		y: 0,
		top: 0,
		left: 0,
		right: 0,
		bottom: 0,
		width: 0,
		height: 0,
		toJSON() {
			return this;
		},
	} as DOMRect;

	if (typeof Range.prototype.getClientRects !== 'function') {
		// 与 jsdom 给 Element 的表达一致：没有排版 ⇒ 空列表（不是抛错）
		Range.prototype.getClientRects = function getClientRects() {
			return Object.assign([], { item: () => null }) as unknown as DOMRectList;
		};
	}

	if (typeof Range.prototype.getBoundingClientRect !== 'function') {
		// singleRect 在空列表时会回落到这里，所以它必须存在
		Range.prototype.getBoundingClientRect = () => ZERO_RECT;
	}

	if (typeof document.elementFromPoint !== 'function') {
		// 没有排版就没有"某坐标下的元素"。null 是 posAtCoords 明确处理的取值。
		document.elementFromPoint = () => null;
	}
}

patchMissingLayoutApis();

// onUnhandledRequest: 'error' —— 忘了 mock 的请求当场红，别让它悄悄打真实网络
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));

afterEach(async () => {
	server.resetHandlers();
	cleanup();
	// tokenStore 有内存缓存，光清 localStorage 不够，用例之间会串登录态
	const { apiClient } = await import('@/api/client');
	apiClient.tokens.set(null);
	localStorage.clear();
	document.documentElement.classList.remove('dark');
});

afterAll(() => server.close());
