import { getSchema } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { DOMSerializer } from '@tiptap/pm/model';
import { describe, expect, it } from 'vitest';

import {
	EMPTY_DOC_HTML,
	hasDescriptionChanged,
	isEmptyDescription,
	normalizeDescription,
	toEditorContent,
} from './rich-text';

/**
 * ★★ 不照着文档抄"空文档是 `<p></p>`"，而是**问真实的 TipDap schema 要答案**。
 *
 * 这个常量错了不会报错：`normalizeDescription` 会认不出空文档，
 * 于是"清空描述"变成发一个非空字符串给后端 —— 编辑器里看着空，库里不空。
 * TipTap 升级改了空文档形态时，这条会红。
 */
describe('空文档形态与真实 TipTap 一致', () => {
	function emptyDocHtml(): string {
		const schema = getSchema([StarterKit]);
		const doc = schema.topNodeType.createAndFill()!;
		const fragment = DOMSerializer.fromSchema(schema).serializeFragment(doc.content);
		const container = document.createElement('div');
		container.appendChild(fragment);
		return container.innerHTML;
	}

	it('★★ EMPTY_DOC_HTML 与 StarterKit 真实产出的空文档相同', () => {
		expect(emptyDocHtml()).toBe(EMPTY_DOC_HTML);
	});

	it('★ 真实空文档能被 isEmptyDescription 认出来', () => {
		expect(isEmptyDescription(emptyDocHtml())).toBe(true);
	});
});

describe('isEmptyDescription', () => {
	it.each([null, undefined, '', '   ', '<p></p>', '<p> </p>', '<p><br></p>', '<p>&nbsp;</p>'])(
		'%s 判为空',
		(value) => {
			expect(isEmptyDescription(value)).toBe(true);
		},
	);

	it.each(['<p>a</p>', '<p><strong>x</strong></p>', '<ul><li>x</li></ul>', 'plain'])(
		'%s 判为非空',
		(value) => {
			expect(isEmptyDescription(value)).toBe(false);
		},
	);
});

describe('normalizeDescription', () => {
	/**
	 * ★ Task 的 description 传空串**就是清空**（标准全量替换）。
	 * ⚠️ 这跟 Project 相反 —— Project 的 description 一旦有值永远清不掉（AC-6 例外）。
	 * 两条成对写在这里，免得有人把 Project 的经验带过来。
	 */
	it('★ 空文档归一成空串（Task 上空串即清空）', () => {
		expect(normalizeDescription(EMPTY_DOC_HTML)).toBe('');
		expect(normalizeDescription('')).toBe('');
		expect(normalizeDescription(null)).toBe('');
	});

	it('★ 非空内容原样保留，不做净化/重排（往返必须不丢格式）', () => {
		const html = '<h2>标题</h2><p>正文 <strong>粗</strong> <em>斜</em></p><ul><li>项</li></ul>';
		expect(normalizeDescription(html)).toBe(html);
	});

	it('内容里的空段落不算整体为空', () => {
		expect(normalizeDescription('<p></p><p>有字</p>')).toBe('<p></p><p>有字</p>');
	});
});

describe('toEditorContent', () => {
	it('空值给空串，让编辑器自建空文档', () => {
		expect(toEditorContent(null)).toBe('');
		expect(toEditorContent(EMPTY_DOC_HTML)).toBe('');
	});

	it('非空值原样进编辑器', () => {
		expect(toEditorContent('<p>x</p>')).toBe('<p>x</p>');
	});

	/** ★ 往返：后端值 → 编辑器 → 归一化，非空内容必须逐字回到原样。 */
	it('★ 往返不丢格式', () => {
		const html = '<p>保留 <code>代码</code> 与 <a href="https://x">链接</a></p>';
		expect(normalizeDescription(toEditorContent(html))).toBe(html);
	});
});

describe('hasDescriptionChanged', () => {
	/**
	 * ★ 这条防的是"点进描述框再点走就发一次全量替换"。
	 * 从空值打开编辑器、什么都不做，getHTML() 给的是 `<p></p>`；
	 * 不归一化比较的话它 !== ''，于是每次失焦都发请求。
	 */
	it.each([
		[null, EMPTY_DOC_HTML],
		['', EMPTY_DOC_HTML],
		[EMPTY_DOC_HTML, ''],
		[undefined, ''],
	])('★ %s → %s 视为没变（不触发保存）', (before, after) => {
		expect(hasDescriptionChanged(before, after)).toBe(false);
	});

	it.each([
		[null, '<p>新</p>'],
		['<p>旧</p>', '<p>新</p>'],
		// ★ 清空是一次真实变更，必须触发保存
		['<p>旧</p>', EMPTY_DOC_HTML],
		['<p>旧</p>', ''],
	])('%s → %s 视为有变更', (before, after) => {
		expect(hasDescriptionChanged(before, after)).toBe(true);
	});
});
