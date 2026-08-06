import StarterKit from '@tiptap/starter-kit';
import { EditorContent, useEditor } from '@tiptap/react';
import { useEffect, useRef } from 'react';

import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/context';
import { hasDescriptionChanged, normalizeDescription, toEditorContent } from '@/lib/rich-text';
import { cn } from '@/lib/utils';

/**
 * 描述编辑器（F08b）。TipTap 2 + StarterKit，与上游同款，HTML 结构天然兼容。
 *
 * ## 保存时机：失焦保存 + 防抖兜底，**不在 onChange 里直发**
 *
 * `description` 走的是 `POST /tasks/{id}` 这条**全量替换**路径，每次保存都要
 * 携带整个 Task 的 15 个可写列。onChange 直发意味着每敲一个字发一次完整对象：
 * 请求量先不说，真正的问题是**并发写互相覆盖** —— 前一次请求带着旧快照在路上，
 * 后一次带着新快照，返回顺序不保证，慢的那个会把快的覆盖回去。
 *
 * 所以：失焦时保存（主路径），另加一个防抖兜底（用户长时间不失焦也能存上）。
 * 两条路都走同一个 `save()`，内容没变则不发请求。
 *
 * 并发安全还有一层来自 F08a：`useUpdateTask` 的 mutationFn 在**发请求那一刻**
 * 才从缓存取完整对象作为底，而不是在渲染时捕获 —— 底始终是最新的。
 */
export const DESCRIPTION_AUTOSAVE_MS = 2000;

export function DescriptionEditor({
	description,
	disabled = false,
	onSave,
	autosaveMs = DESCRIPTION_AUTOSAVE_MS,
}: {
	description: string | null | undefined;
	disabled?: boolean;
	onSave: (description: string) => void;
	autosaveMs?: number;
}) {
	const t = useTranslation();
	/** 最近一次"已经保存过/服务端给的"值，用来判断脏不脏。 */
	const savedRef = useRef(normalizeDescription(description));
	const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	const editor = useEditor({
		extensions: [StarterKit],
		content: toEditorContent(description),
		editable: !disabled,
		editorProps: {
			attributes: {
				class: 'prose prose-sm max-w-none min-h-24 p-3 focus:outline-none',
				'data-testid': 'description-editor',
				'aria-label': '任务描述',
			},
		},
	});

	/** 内容没变就不发请求 —— 否则点进来再点走都会发一次全量替换。 */
	function save() {
		if (!editor || disabled) return;
		const next = normalizeDescription(editor.getHTML());
		if (!hasDescriptionChanged(savedRef.current, next)) return;
		savedRef.current = next;
		onSave(next);
	}

	function scheduleAutosave() {
		if (timerRef.current) clearTimeout(timerRef.current);
		timerRef.current = setTimeout(save, autosaveMs);
	}

	// 服务端值变了（首次加载完成、或别处改过后重取）时同步进编辑器。
	// 用户正在编辑时不要打断：只在内容与已保存值一致时才回填。
	useEffect(() => {
		if (!editor) return;
		const incoming = normalizeDescription(description);
		if (incoming === savedRef.current) return;
		if (hasDescriptionChanged(savedRef.current, editor.getHTML())) return;

		savedRef.current = incoming;
		editor.commands.setContent(toEditorContent(description), false);
	}, [description, editor]);

	useEffect(() => {
		editor?.setEditable(!disabled);
	}, [disabled, editor]);

	// 卸载时把待发的防抖清掉：定时器打到已卸载的组件上没有意义，
	// 而且此时 editor 已销毁，getHTML() 会抛
	useEffect(() => {
		return () => {
			if (timerRef.current) clearTimeout(timerRef.current);
		};
	}, []);

	if (!editor) return null;

	return (
		<div className="space-y-2" data-testid="description-field">
			<div className="flex items-center justify-between">
				<span className="text-sm font-medium text-foreground">
					{t('task.attributes.description')}
				</span>
				<Button
					type="button"
					variant="outline"
					size="sm"
					data-testid="save-description"
					disabled={disabled}
					onClick={() => {
						if (timerRef.current) clearTimeout(timerRef.current);
						save();
					}}
				>
					{t('misc.save')}
				</Button>
			</div>

			<div
				className={cn(
					'rounded-md border border-input bg-background',
					disabled && 'opacity-60',
					'focus-within:ring-2 focus-within:ring-ring',
				)}
				onBlur={(event) => {
					// 焦点还在编辑器内部移动时不算失焦
					if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
					if (timerRef.current) clearTimeout(timerRef.current);
					save();
				}}
			>
				<EditorContent editor={editor} onInput={scheduleAutosave} />
			</div>
		</div>
	);
}
