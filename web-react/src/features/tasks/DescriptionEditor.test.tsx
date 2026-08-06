import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { EMPTY_DOC_HTML } from '@/lib/rich-text';
import { renderWithProviders } from '@/test/render';
import { DescriptionEditor } from './DescriptionEditor';

/**
 * TipTap 在 jsdom 里是能跑的（ProseMirror 只依赖 DOM），但输入要走 contenteditable，
 * 不能用 `fireEvent.change`。用 userEvent 点进去再打字。
 */
function setup(props: Partial<Parameters<typeof DescriptionEditor>[0]> = {}) {
	const onSave = vi.fn();
	// ⚠️ 必须走 `renderWithProviders`：组件用了 `t()`，而 `useI18n` 在 Provider 之外
	// **会抛**（那是有意的 fail-fast）。F13 迁移这个组件时，裸 `render` 让 16 条一起红 ——
	// 这正是那道 fail-fast 要的效果：它指名道姓地说"没挂 Provider"，
	// 而不是让界面悄悄显示成英文。
	const utils = renderWithProviders(
		<DescriptionEditor description={props.description ?? ''} onSave={onSave} {...props} />,
	);
	return { onSave, ...utils };
}

function editorEl() {
	return screen.getByTestId('description-editor');
}

afterEach(() => {
	vi.useRealTimers();
});

describe('描述编辑器：渲染与往返', () => {
	it('把后端的 HTML 渲染进编辑器', async () => {
		setup({ description: '<p>原有描述</p>' });
		await waitFor(() => expect(editorEl()).toHaveTextContent('原有描述'));
	});

	/** ★ 验收要求：存取往返不丢格式。 */
	it('★ 富文本结构往返不丢（粗体/列表/标题）', async () => {
		const html = '<h2>标题</h2><p>正文 <strong>粗</strong></p><ul><li>项</li></ul>';
		const { onSave } = setup({ description: html });

		await waitFor(() => expect(editorEl()).toHaveTextContent('标题'));

		// 打一个字再失焦，回传的内容里原有结构必须还在
		await userEvent.click(editorEl());
		await userEvent.type(editorEl(), '尾');
		await userEvent.tab();

		await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
		const saved = onSave.mock.calls[0]![0] as string;
		expect(saved).toContain('<h2>');
		expect(saved).toContain('<strong>');
		expect(saved).toContain('<ul>');
		expect(saved).toContain('<li>');
	});

	it('空描述时渲染空编辑器，不显示 <p></p> 字面量', async () => {
		setup({ description: null });
		await waitFor(() => expect(editorEl()).toBeInTheDocument());
		expect(editorEl()).not.toHaveTextContent('<p>');
	});
});

describe('★ 保存时机：失焦保存 + 防抖兜底，不在 onChange 直发', () => {
	/**
	 * ★ 本任务最重要的一条。description 走全量替换，每次保存都携带整个 Task 的
	 * 15 个可写列；onChange 直发意味着每敲一个字发一次完整对象，
	 * 并发写会互相覆盖（慢的那个把快的盖回去）。
	 */
	it('★ 只打字、不失焦也不等防抖 —— 一个请求都不发', async () => {
		const { onSave } = setup({ description: '' });

		await userEvent.click(editorEl());
		await userEvent.type(editorEl(), '正在输入的内容');

		expect(onSave).not.toHaveBeenCalled();
	});

	it('★ 失焦时保存一次（不是每个字一次）', async () => {
		const { onSave } = setup({ description: '' });

		await userEvent.click(editorEl());
		await userEvent.type(editorEl(), '一二三四五');
		await userEvent.tab();

		await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
		expect(onSave.mock.calls[0]![0]).toContain('一二三四五');
	});

	/** ★ 内容没变时失焦不该发请求，否则点进来再点走都发一次全量替换。 */
	it('★ 点进去什么都没改就点走，不发请求', async () => {
		const { onSave } = setup({ description: '<p>没动过</p>' });

		await waitFor(() => expect(editorEl()).toHaveTextContent('没动过'));
		await userEvent.click(editorEl());
		await userEvent.tab();

		expect(onSave).not.toHaveBeenCalled();
	});

	/** ★ 从空值打开同样不该发 —— getHTML() 给的是 `<p></p>`，不归一化就会误判成修改。 */
	it('★ 从空描述点进去再点走，不发请求（<p></p> 不算修改）', async () => {
		const { onSave } = setup({ description: null });

		await waitFor(() => expect(editorEl()).toBeInTheDocument());
		await userEvent.click(editorEl());
		await userEvent.tab();

		expect(onSave).not.toHaveBeenCalled();
	});

	/**
	 * ★ 防抖兜底：用户一直不失焦（比如写完就切走标签页）也要能存上。
	 * 用很短的 autosaveMs + 真实计时器，避免 fake timers 与 userEvent 的已知打架。
	 */
	it('★ 一直不失焦时，防抖兜底会自己保存', async () => {
		const { onSave } = setup({ description: '', autosaveMs: 30 });

		await userEvent.click(editorEl());
		await userEvent.type(editorEl(), '不失焦');

		await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1), { timeout: 1000 });
		expect(onSave.mock.calls[0]![0]).toContain('不失焦');
	});

	/** ★ 防抖是"停下来才发"，连续输入期间不该一次次发。 */
	it('★ 连续输入期间防抖被不断重置，只在停下来之后发一次', async () => {
		const { onSave } = setup({ description: '', autosaveMs: 60 });

		await userEvent.click(editorEl());
		for (const char of '一二三四五六') {
			await userEvent.type(editorEl(), char);
		}

		await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1), { timeout: 1000 });
		// 六个字符若各发一次，这里就是 6
		expect(onSave).toHaveBeenCalledTimes(1);
	});

	it('失焦保存后，待发的防抖不再重复发一次', async () => {
		const { onSave } = setup({ description: '', autosaveMs: 40 });

		await userEvent.click(editorEl());
		await userEvent.type(editorEl(), 'abc');
		await userEvent.tab();

		await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
		await new Promise((resolve) => setTimeout(resolve, 120));
		expect(onSave).toHaveBeenCalledTimes(1);
	});

	it('显式点"保存描述"按钮也能存', async () => {
		const { onSave } = setup({ description: '' });

		await userEvent.click(editorEl());
		await userEvent.type(editorEl(), '手动保存');
		await userEvent.click(screen.getByTestId('save-description'));

		await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
	});

	it('保存之后再次失焦不重复发（内容已不脏）', async () => {
		const { onSave } = setup({ description: '' });

		await userEvent.click(editorEl());
		await userEvent.type(editorEl(), 'abc');
		await userEvent.tab();
		await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));

		await userEvent.click(editorEl());
		await userEvent.tab();

		expect(onSave).toHaveBeenCalledTimes(1);
	});
});

describe('★ 清空描述', () => {
	/**
	 * ★ Task 的 description 传**空串**就是清空（走标准全量替换）。
	 * ⚠️ 与 Project 相反 —— Project 的 description 一旦有值永远清不掉（AC-6 例外）。
	 * 发 `<p></p>` 的话，编辑器里看着空了、库里其实非空。
	 */
	it('★ 清光内容后保存的是空串，不是 <p></p>', async () => {
		const { onSave } = setup({ description: '<p>要被删掉</p>' });

		await waitFor(() => expect(editorEl()).toHaveTextContent('要被删掉'));
		await userEvent.click(editorEl());
		await userEvent.keyboard('{Control>}a{/Control}{Backspace}');
		await userEvent.tab();

		await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
		expect(onSave.mock.calls[0]![0]).toBe('');
		expect(onSave.mock.calls[0]![0]).not.toBe(EMPTY_DOC_HTML);
	});
});

describe('只读', () => {
	/**
	 * ★ **没 disabled 时必须是可编辑的**——与下面那条成对，两侧都断言。
	 *
	 * ## ⚠️ 这条的来历里有一个我判断错了的过程，如实记在这里
	 *
	 * 起因是做变异验证：把 `useEditor({ editable: !disabled })` 改成
	 * `editable: false`，期待有用例变红。**结果单测与 F14 浏览器用例全绿**，
	 * 于是我一度判定"整个仓库没有任何东西守着编辑器的可编辑性"。
	 *
	 * **那个判断是错的，因为那个变异根本没生效**：组件里还有一个
	 * `useEffect(() => editor?.setEditable(!disabled))`，
	 * 它在挂载后**立刻把初始值覆盖回来**——编辑器自始至终都是可编辑的，
	 * 所以没有任何东西需要变红（第 21 条：变异没落到运行时；
	 * 第 29 条：装置说的谎方向是反的，它让我以为"断言不承重"）。
	 *
	 * 把两处**一起**改成恒只读之后再跑：**9 条红**，其中 8 条是本来就有的
	 * （打字→内容不变→保存不触发→等 1 秒超时）。**覆盖并不缺。**
	 *
	 * 那这条为什么还留着？因为它把同一个故障的信号从**8 条各等 1 秒的超时**
	 * 变成**一条 6ms 的、指名道姓的红**。前者只会告诉你"保存没发生"，
	 * 得再查一层才知道是编辑器只读；后者直接说出是哪个属性坏了。
	 */
	it('★ 没有 disabled 时编辑器可编辑（与下面那条成对，两侧都断言）', async () => {
		setup({ description: '<p>可改</p>' });

		await waitFor(() => expect(editorEl()).toHaveTextContent('可改'));
		expect(editorEl()).toHaveAttribute('contenteditable', 'true');
		expect(screen.getByTestId('save-description')).toBeEnabled();
	});

	it('disabled 时编辑器不可编辑，按钮禁用', async () => {
		setup({ description: '<p>只读</p>', disabled: true });

		await waitFor(() => expect(editorEl()).toHaveTextContent('只读'));
		expect(editorEl()).toHaveAttribute('contenteditable', 'false');
		expect(screen.getByTestId('save-description')).toBeDisabled();
	});

	it('disabled 时即使失焦也不发请求', async () => {
		const { onSave } = setup({ description: '<p>只读</p>', disabled: true });

		await waitFor(() => expect(editorEl()).toHaveTextContent('只读'));
		await userEvent.click(editorEl());
		await userEvent.tab();

		expect(onSave).not.toHaveBeenCalled();
	});
});
