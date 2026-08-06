import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * 表单字段外壳：label + 控件 + 错误文案，并把 aria-invalid / aria-describedby 接好。
 * 表单校验消息必须能被读屏读到，这里统一处理，页面里不要各写各的。
 */
export function Field({
	label,
	htmlFor,
	error,
	children,
	className,
}: {
	label: string;
	htmlFor: string;
	error?: string;
	children: React.ReactNode;
	className?: string;
}) {
	const errorId = `${htmlFor}-error`;

	return (
		<div className={cn('space-y-1.5', className)}>
			<label htmlFor={htmlFor} className="text-sm font-medium text-foreground">
				{label}
			</label>
			{children}
			{/*
			 * `data-testid` 取 `{htmlFor}-error`，让测试能**按字段**定位错误，
			 * 而不是按错误文案（`getByText('请输入用户名')`）。
			 * 校验消息属于 i18n 的迁移范围，按文案查的用例会在换措辞时整批红，
			 * 而"这个字段报没报错"这件事与文案怎么写无关。
			 */}
			{error ? (
				<p id={errorId} data-testid={errorId} role="alert" className="text-sm text-xyz-red-6">
					{error}
				</p>
			) : null}
		</div>
	);
}
