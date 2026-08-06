import { describe, expect, it } from 'vitest';

import en from './lang/en.json';
import zhCN from './lang/zh-CN.json';
import {
	interpolate,
	lookupMessage,
	selectPluralForm,
	translate,
	type Messages,
} from './translate';

const EN = en as unknown as Messages;
const ZH = zhCN as unknown as Messages;

describe('取消息', () => {
	it('按 a.b.c 逐层取', () => {
		expect(lookupMessage({ a: { b: { c: '命中' } } }, 'a.b.c')).toBe('命中');
	});

	it('取不到返回 null', () => {
		expect(lookupMessage({ a: {} }, 'a.b')).toBeNull();
	});

	/**
	 * ★ 点到中间节点（拿到的是对象不是字符串）也算取不到。
	 *
	 * 不判这一下的话，`t('task')` 会把整个子树 String() 成 `[object Object]`
	 * 显示在界面上，而不是退回兜底语言。
	 */
	it('★ key 指向中间节点时返回 null，不是返回那个对象', () => {
		expect(lookupMessage({ task: { title: 'x' } }, 'task')).toBeNull();
	});

	it('中途撞上字符串也返回 null，不抛', () => {
		expect(lookupMessage({ a: '字符串' }, 'a.b.c')).toBeNull();
	});
});

describe('插值', () => {
	it('替换命名参数', () => {
		expect(interpolate('Good Night {username}!', { username: 'Bob' })).toBe('Good Night Bob!');
	});

	it('数字参数转成字符串', () => {
		expect(interpolate('{count} comments', { count: 3 })).toBe('3 comments');
	});

	/**
	 * ★★ `{'…'}` 是**字面量**，不是参数名。
	 *
	 * 语料里真有一条：`user.auth.emailPlaceholder = "e.g. frederic{'@'}calton.io"`。
	 * 不认这个语法的话，界面上会出现 `frederic{'@'}calton.io`。
	 */
	it("★★ {'@'} 这类字面量转义还原成字符本身", () => {
		expect(interpolate("e.g. frederic{'@'}calton.io", {})).toBe('e.g. frederic@calton.io');
	});

	/**
	 * ★ 缺参数时**原样保留** `{name}`，不替换成空串。
	 *
	 * 界面上留着 `{username}` 是刺眼的、会被报上来的 bug；
	 * 悄悄变空串则会一直没人发现。
	 */
	it('★ 缺参数时保留占位符，不静默变成空串', () => {
		expect(interpolate('Hello {username}!', {})).toBe('Hello {username}!');
	});
});

describe('复数分支的选取', () => {
	it('两支：1 取第一支，其余取第二支', () => {
		const forms = ['one item', 'many items'];
		expect(selectPluralForm(forms, 1)).toBe('one item');
		expect(selectPluralForm(forms, 0)).toBe('many items');
		expect(selectPluralForm(forms, 2)).toBe('many items');
	});

	it('三支：0 / 1 / 其余', () => {
		const forms = ['none', 'one', 'many'];
		expect(selectPluralForm(forms, 0)).toBe('none');
		expect(selectPluralForm(forms, 1)).toBe('one');
		expect(selectPluralForm(forms, 5)).toBe('many');
	});
});

describe('整条翻译', () => {
	it('先查当前语言', () => {
		expect(translate({ a: '中文' }, { a: 'english' }, 'a')).toBe('中文');
	});

	/**
	 * ★★ 当前语言缺这条 key 时退回 en。
	 *
	 * 这不是理论情况：上游各语言包的翻译进度不一，**只有 en.json 是全量的**。
	 * 下面那条用真实语料验证它确实会发生。
	 */
	it('★★ 当前语言缺 key 时退回兜底语言', () => {
		expect(translate({}, { a: { b: 'english' } }, 'a.b')).toBe('english');
	});

	it('两份都没有时返回 key 本身，让缺失在界面上可见', () => {
		expect(translate({}, {}, 'no.such.key')).toBe('no.such.key');
	});

	/**
	 * ★★★ **竖线不等于复数**：没给 count 就不拆。
	 *
	 * 判别式样本用的是真实语料里那条 `migrate.csv.delimiters.pipe`，
	 * 它在**描述竖线这个字符**，值是 `"Pipe (|)"`。
	 * 见到竖线就拆的实现会把它显示成 `"Pipe ("`。
	 *
	 * ⚠️ 这条断言的数据不能换成随便一个带竖线的串 —— 必须是**真的会被当成
	 * 复数消息误拆、而语义上根本不是复数**的那种，否则测不出这个区分。
	 */
	it('★★★ 没给 count 时不拆竖线（真实语料：Pipe (|) 不能变成 Pipe (）', () => {
		expect(lookupMessage(EN, 'migrate.csv.delimiters.pipe')).toBe('Pipe (|)');
		expect(translate(EN, EN, 'migrate.csv.delimiters.pipe')).toBe('Pipe (|)');
	});

	/** 给了 count 才按复数拆，并把 count 插进去。 */
	it('★★ 给了 count 才拆竖线（真实语料：{count} comment | {count} comments）', () => {
		expect(translate(EN, EN, 'task.attributes.comment', { count: 1 })).toBe('1 comment');
		expect(translate(EN, EN, 'task.attributes.comment', { count: 3 })).toBe('3 comments');
	});

	/**
	 * ★ count 为 0 时走"复数"那一支 —— 判别式取值：
	 * 如果只测 1 和 2，那么"`count === 1` 取第一支"与"`count <= 1` 取第一支"
	 * 两种实现同解，这条用例分辨不出来（实践第 4 条）。
	 */
	it('★ count 为 0 走复数支（0 与 1 是判别式取值）', () => {
		expect(translate(EN, EN, 'task.attributes.comment', { count: 0 })).toBe('0 comments');
	});
});

describe('真实语料上的性质（不是我们发明的用例）', () => {
	/**
	 * ★★ zh-CN **确实**比 en 少 key —— 这是"兜底必须是 en"那条设计的**前提**。
	 *
	 * 断言前提而不只断言行为（实践第 24 条）：哪天上游把 zh-CN 补全了，
	 * 这条会红，那时该重新想的是"兜底语言还需不需要这么讲究"，
	 * 而不是发现设计的依据早就不成立了却没人知道。
	 */
	it('★★ zh-CN 的 key 是 en 的真子集（兜底取 en 的依据）', () => {
		const flatten = (messages: Messages, prefix = ''): string[] =>
			Object.entries(messages).flatMap(([key, value]) =>
				typeof value === 'string' ? [prefix + key] : flatten(value, `${prefix}${key}.`),
			);

		const enKeys = new Set(flatten(EN));
		const zhKeys = flatten(ZH);

		// zh 里不该有 en 没有的 key
		expect(zhKeys.filter((key) => !enKeys.has(key))).toEqual([]);
		// 而且 zh 确实更少 —— 否则"兜底"这件事在当前语料上根本不会发生
		expect(zhKeys.length).toBeLessThan(enKeys.size);
	});

	/**
	 * ★ 语料里**没有** `@:key` 链接消息、也没有内嵌 HTML。
	 *
	 * `translate` 不支持这两种构造，是基于对语料的实测下的判断，不是猜的。
	 * 这条测试把那个判断钉住：上游哪天引入了链接消息，这里会红，
	 * 提醒的是"该给 translate 加能力了"，而不是等界面上出现一串 `@:common.ok`。
	 */
	it('★ en.json 里没有 @:link 消息，也没有内嵌 HTML（translate 不支持这两种）', () => {
		const values: string[] = [];
		(function walk(node: Messages) {
			for (const value of Object.values(node)) {
				if (typeof value === 'string') values.push(value);
				else walk(value);
			}
		})(EN);

		expect(values.length).toBeGreaterThan(1000);
		expect(values.filter((value) => /@:[a-zA-Z]/.test(value))).toEqual([]);
		expect(values.filter((value) => /<[a-zA-Z]/.test(value))).toEqual([]);
	});
});
