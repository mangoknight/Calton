import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import tailwindConfig from '../../tailwind.config';
import snapshot from '../../tokens.nexus.snapshot.json';

/**
 * F01 验收：design token 与入库的 Nexus 快照一致。
 *
 * 断言分两层，缺一层都会留下大洞：
 *  ① **映射层** —— tailwind.config.ts 的 theme.extend。断言的是 config 的实际导出
 *     而不是 tokens.ts，因为最终生效的是 config；有人绕过 tokens.ts 直接往 config
 *     里塞颜色也要红。同时锁顶层键集合，否则新增一整组（spacing 之类）能溜过去。
 *  ② **取值层** —— index.css 里 :root / .dark 的 CSS 变量。①里的值大多是
 *     `var(--color-blue-6)` 这种引用，只锁映射的话，把 --color-blue-6 改成红色
 *     所有测试照样绿。.dark 那套尤其不会有人手工核。
 */

type Plain = Record<string, unknown>;

const extend = (tailwindConfig.theme?.extend ?? {}) as Plain;

/** 递归收集 `a.b.c = value` 形式的叶子，便于给出可读的 diff。 */
function flatten(value: unknown, prefix = ''): Record<string, string> {
	if (Array.isArray(value)) return { [prefix]: value.join(', ') };
	if (value !== null && typeof value === 'object') {
		return Object.entries(value as Plain).reduce<Record<string, string>>((acc, [k, v]) => {
			return { ...acc, ...flatten(v, prefix ? `${prefix}.${k}` : k) };
		}, {});
	}
	return { [prefix]: String(value) };
}

const TOKEN_GROUPS = ['colors', 'borderRadius', 'fontFamily', 'maxWidth'] as const;
/** token 组之外，theme.extend 里还允许存在的键（行为性配置，不属于设计 token）。 */
const NON_TOKEN_KEYS = ['keyframes', 'animation'] as const;

const { _provenance, cssVariables, ...expectedGroups } = snapshot as Plain;

describe('① 映射层：tailwind.config.ts 与快照一致', () => {
	it('快照本身覆盖且只覆盖四组 token', () => {
		expect(Object.keys(expectedGroups).sort()).toEqual([...TOKEN_GROUPS].sort());
		expect(_provenance).toBeDefined();
	});

	it('theme.extend 的顶层键集合被锁死 —— 新增一整组配置也要红', () => {
		expect(Object.keys(extend).sort()).toEqual([...TOKEN_GROUPS, ...NON_TOKEN_KEYS].sort());
	});

	it.each(TOKEN_GROUPS)('theme.extend.%s 与快照逐键逐值相等', (group) => {
		const actual = flatten(extend[group]);
		const expected = flatten(expectedGroups[group]);

		// 先比键集合：多/少 token 时给出的失败信息比整对象 diff 好读
		expect(Object.keys(actual).sort()).toEqual(Object.keys(expected).sort());
		expect(actual).toEqual(expected);
	});

	it('darkMode 用 class 切换（Portal 组件依赖 <html> 上的 .dark）', () => {
		expect(tailwindConfig.darkMode).toBe('class');
	});
});

describe('② 取值层：index.css 的 CSS 变量与快照一致', () => {
	// 直接读源文件：jsdom 下 import.meta.url 是 http 协议，而 `?raw` 又被
	// vitest 的 css:false 吞成空串 —— 两条路都不通，老实用 fs。
	// vitest 的 cwd 就是 web-react/。
	const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');

	/** :root / .dark 块里没有嵌套花括号，取到第一个 } 即可。 */
	function readVars(selector: string): Record<string, string> {
		const start = css.indexOf(`${selector} {`);
		expect(start, `index.css 里找不到 ${selector} 块`).toBeGreaterThanOrEqual(0);

		const body = css.slice(start + selector.length + 2, css.indexOf('}', start));
		const vars: Record<string, string> = {};
		for (const match of body.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
			vars[match[1]] = match[2].trim();
		}
		return vars;
	}

	const expectedVars = cssVariables as Record<string, Record<string, string>>;

	it.each([':root', '.dark'])('%s 的变量逐条与快照相等', (selector) => {
		const actual = readVars(selector);
		const expected = expectedVars[selector];

		expect(Object.keys(actual).sort()).toEqual(Object.keys(expected).sort());
		expect(actual).toEqual(expected);
	});

	it('两套主题各自定义了完整的一批变量（防结构性错位）', () => {
		expect(Object.keys(expectedVars[':root']).length).toBeGreaterThan(40);
		expect(Object.keys(expectedVars['.dark']).length).toBeGreaterThan(40);
	});
});
