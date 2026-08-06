import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { SUPPORTED_LOCALES } from './locales';

/**
 * 语言包与上游的一致性守卫（F13）。
 *
 * ## 为什么这条守卫值得存在
 *
 * 前端文案是**用户可见契约的一部分**：同一个界面元素上游叫什么、我们就得叫什么。
 * 所以语言包是**逐字节复制**上游的，不是"参考着写的"。
 * 而"复制过一次"和"现在还一致"是两回事 —— 中间只要有人顺手改一个中文措辞、
 * 或者补一个上游没有的 key，这份契约就悄悄分叉了，而且**没有任何现象**：
 * 界面照常显示，测试照常绿。
 *
 * 这条守卫把"现在还一致"变成可执行的判据。
 *
 * ## ⚠️ 它红了怎么办
 *
 * **默认动作是把上游的重新复制过来**，不是改这条测试的期望：
 *
 * ```
 * cp frontend/src/i18n/lang/*.json web-react/src/i18n/lang/
 * ```
 *
 * 只有在"我们有意偏离上游文案"时才放宽，且必须在这里写明偏离了哪个 key、为什么
 * （照抄上游是有边界的，但边界得写下来，不能靠沉默）。
 */

const OUR_LANG_DIR = dirname(fileURLToPath(import.meta.url)) + '/lang';
/** 上游语言包（Vue 前端）。相对仓库根：`frontend/src/i18n/lang`。 */
const UPSTREAM_LANG_DIR = join(OUR_LANG_DIR, '../../../../frontend/src/i18n/lang');

function listLangFiles(dir: string): string[] {
	return readdirSync(dir)
		.filter((name) => name.endsWith('.json'))
		.sort();
}

describe('语言包与上游一致', () => {
	/**
	 * ★ 先断言**上游那份读得到**。
	 *
	 * 不断言这一条的话，路径写错会让下面所有用例退化成"两个空集合相等"——
	 * 一条什么都没比的测试，而且永远是绿的（实践第 28 条：验证工具最危险的
	 * 失效模式是把自己的缺陷报告成被验证对象没问题）。
	 */
	it('★ 上游语言包目录存在且非空（否则下面的对比是空对空）', () => {
		const upstream = listLangFiles(UPSTREAM_LANG_DIR);
		expect(upstream.length).toBeGreaterThan(30);
		expect(upstream).toContain('en.json');
		expect(upstream).toContain('zh-CN.json');
	});

	it('★ 文件清单与上游完全一致', () => {
		expect(listLangFiles(OUR_LANG_DIR)).toEqual(listLangFiles(UPSTREAM_LANG_DIR));
	});

	/**
	 * ★★ 逐字节相同。
	 *
	 * 比"key 集合相同"更强，因为分叉最常见的形式不是加减 key，
	 * 而是**改一句译文** —— 那种改动 key 集合看不出来。
	 */
	it.each(listLangFiles(UPSTREAM_LANG_DIR))('★★ %s 与上游逐字节相同', (name) => {
		const ours = readFileSync(join(OUR_LANG_DIR, name));
		const upstream = readFileSync(join(UPSTREAM_LANG_DIR, name));
		expect(ours.equals(upstream)).toBe(true);
	});

	/**
	 * ★ 语言清单里的每个代码都得有语言包。
	 *
	 * 少了的话，用户在切换器里选中它 → 动态 import 失败 → 静默退回英文。
	 * 那是一个只有那个语言的用户会遇到、而且不会有人报的 bug。
	 */
	it('★ SUPPORTED_LOCALES 里的每个语言都有对应的语言包文件', () => {
		const files = new Set(listLangFiles(OUR_LANG_DIR));
		const missing = Object.keys(SUPPORTED_LOCALES).filter((code) => !files.has(`${code}.json`));
		expect(missing).toEqual([]);
	});

	/**
	 * SUPPORTED_LOCALES 也照抄上游 —— 逐条比对上游 `i18n/index.ts` 里的那张表。
	 * 上游 `lang/` 下的文件数（38）**多于**表里的语言数（32），
	 * 多出来的是尚未接入的翻译；这里断言的是"我们的表 = 上游的表"，不是"表 = 文件"。
	 */
	it('★ SUPPORTED_LOCALES 与上游 i18n/index.ts 里的那张表逐条一致', () => {
		const upstreamIndex = readFileSync(join(UPSTREAM_LANG_DIR, '../index.ts'), 'utf8');
		const block = upstreamIndex.slice(
			upstreamIndex.indexOf('export const SUPPORTED_LOCALES'),
			upstreamIndex.indexOf('} as const'),
		);

		// 上游写法：	'zh-CN': '简体中文',
		const upstreamPairs = [...block.matchAll(/^\t'([^']+)':\s*'([^']*)',$/gm)].map(
			([, code, label]) => [code, label] as const,
		);

		// 空对空守卫：正则没匹配到时，下面的相等断言会退化成无意义的比较
		expect(upstreamPairs.length).toBeGreaterThan(30);
		expect(Object.entries(SUPPORTED_LOCALES)).toEqual(upstreamPairs.map(([c, l]) => [c, l]));
	});
});
