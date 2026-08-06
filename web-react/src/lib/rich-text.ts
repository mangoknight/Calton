/**
 * 富文本描述的归一化（F08b）。
 *
 * ## 为什么需要归一化
 *
 * TipTap 的文档模型里"空"不是空串：清光内容后 `editor.getHTML()` 返回的是
 * **`<p></p>`**（一个空段落），不是 `''`。直接把它发出去的后果是：
 * 编辑器里看着是空的，库里 `description` 却是个非空字符串 ——
 * 于是"这个任务有没有描述"在别处的判断全是错的，而且列表里可能渲染出一个空行。
 *
 * ⚠️ Task 的 `description` 传空串**就是清空**（走标准全量替换）。
 * 这跟 Project 相反 —— Project 的 description 一旦有值就永远清不掉，
 * 那是 AC-6 的已知例外。**别把 Project 的经验带到 Task 上。**
 * 正因为 Task 这边清得掉，`<p></p>` 没归一化就变成了"用户以为清空了、其实没有"。
 */

/**
 * TipTap 空文档的 `getHTML()` 产物。
 * `rich-text.test.ts` 会**启动真实编辑器**核对这个常量，而不是照文档抄。
 */
export const EMPTY_DOC_HTML = '<p></p>';

/** 只含空白/空段落的几种等价形态，都算空。 */
const EMPTY_PATTERNS = [/^\s*$/, /^<p>(\s|&nbsp;|<br\s*\/?>)*<\/p>$/i];

export function isEmptyDescription(html: string | null | undefined): boolean {
	if (html === null || html === undefined) return true;
	const trimmed = html.trim();
	return EMPTY_PATTERNS.some((pattern) => pattern.test(trimmed));
}

/**
 * 编辑器内容 → 可以发给后端的值。
 * 空文档归一成 `''`（Task 上这就是"清空"），其余原样保留。
 *
 * **不做任何净化/重排**：上游存的就是 TipTap 生成的 HTML，
 * 动它会让存取往返不一致（F08b 验收要求往返不丢格式）。
 */
export function normalizeDescription(html: string | null | undefined): string {
	if (isEmptyDescription(html)) return '';
	return html as string;
}

/**
 * 后端值 → 塞进编辑器的初始内容。
 * 空值给空串，让 TipTap 自己建空文档；给 `<p></p>` 也行，但空串更直白。
 */
export function toEditorContent(description: string | null | undefined): string {
	return isEmptyDescription(description) ? '' : (description as string);
}

/**
 * 内容是否真的变了。
 *
 * 比较前先归一化：从 `''` 打开编辑器、什么都没做就失焦，
 * `getHTML()` 会给出 `<p></p>` —— 不归一化的话这会被当成一次修改，
 * 于是**每次点进描述框再点走都发一次全量替换请求**。
 */
export function hasDescriptionChanged(
	original: string | null | undefined,
	current: string | null | undefined,
): boolean {
	return normalizeDescription(original) !== normalizeDescription(current);
}
