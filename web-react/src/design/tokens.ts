/**
 * XYZ design tokens —— 与 Nexus (web/default/tailwind.colors.js) 保持同源。
 *
 * 规则（沿用 Nexus）：
 *  - 主梯度 blue/gray/orange 引用 CSS 变量 `var(--color-*)`，随 :root / .dark 翻转
 *  - 独立 accent（图表/状态色）直接写 hex，不随主题变化
 *  - shadcn 语义色映射到 `hsl(var(--*))`
 *
 * 本文件是 `tailwind.config.ts` 的唯一 token 来源，且被 `tokens.nexus.snapshot.json`
 * 锁定 —— 改这里必须同步改快照（见 src/design/tokens.test.ts）。
 */

export const xyzColors = {
	/* =============== 主梯度（CSS 变量驱动，支持暗色反转） =============== */
	'xyz-blue': {
		1: 'var(--color-blue-1)',
		2: 'var(--color-blue-2)',
		3: 'var(--color-blue-3)',
		4: 'var(--color-blue-4)',
		5: 'var(--color-blue-5)',
		6: 'var(--color-blue-6)',
		'6h': 'var(--color-blue-6h, #3451e6)',
		7: 'var(--color-blue-7)',
		8: 'var(--color-blue-8)',
		9: 'var(--color-blue-9)',
		10: 'var(--color-blue-10)',
	},
	'xyz-gray': {
		1: 'var(--color-gray-1)',
		2: 'var(--color-gray-2)',
		3: 'var(--color-gray-3)',
		4: 'var(--color-gray-4)',
		5: 'var(--color-gray-5)',
		6: 'var(--color-gray-6)',
		7: 'var(--color-gray-7)',
		8: 'var(--color-gray-8)',
		9: 'var(--color-gray-9)',
		10: 'var(--color-gray-10)',
		11: 'var(--color-gray-11)',
	},
	'xyz-white': {
		1: 'rgba(255, 255, 255, 0.08)',
		2: 'rgba(255, 255, 255, 0.10)',
		3: 'rgba(255, 255, 255, 0.20)',
		4: 'rgba(255, 255, 255, 0.30)',
		5: 'rgba(255, 255, 255, 0.40)',
		6: 'rgba(255, 255, 255, 0.50)',
		7: 'rgba(255, 255, 255, 0.60)',
		8: 'rgba(255, 255, 255, 0.80)',
		9: 'rgba(255, 255, 255, 0.90)',
		10: '#ffffff',
	},
	'xyz-orange': {
		5: '#f97316',
		6: 'var(--color-orange-6)',
		warn: '#ff921c',
	},

	/* =============== 独立 accent（chart/icon/状态色） =============== */
	'xyz-emerald': { 6: '#10b981', 7: '#059669' },
	'xyz-cyan': { 1: '#e6fffb', 3: '#87e8de', 5: '#06b6d4', 6: '#13c2c2', 7: '#08979c' },
	'xyz-green': {
		1: '#f6ffed',
		5: '#b7eb8f',
		6: '#52c41a',
		7: '#389e0d',
		500: '#22c55e',
		semantic: '#21ba45',
	},
	'xyz-red': {
		1: '#fff2f0',
		5: '#ffccc7',
		6: '#ef4444',
		7: '#dc2626',
		warn: '#ff4d4f',
		danger: '#ff3212',
		accent: '#ff6321',
	},
	'xyz-amber': { 1: '#fffbe6', 5: '#ffe58f', 6: '#f59e0b', 7: '#d97706', warn: '#faad14' },
	'xyz-purple': { 1: '#f9f0ff', 3: '#d3adf7', 6: '#8b5cf6', 7: '#722ed1', 500: '#a855f7' },
	'xyz-pink': { 6: '#ec4899' },
	'xyz-rose': { 6: '#f43f5e' },
	'xyz-fuchsia': { 6: '#d946ef' },
	'xyz-indigo': { 6: '#6366f1' },
	'xyz-teal': { 6: '#14b8a6' },
	'xyz-violet': { 5: '#a78bfa' },

	/* =============== 扩展 blue 与特殊场景 =============== */
	'xyz-blue-soft': '#597ef7',
	'xyz-blue-tag': '#1677ff',
	'xyz-blue-brand': '#3b82f6',
	'xyz-blue-accent': '#2563eb',
	'xyz-blue-link': '#2185d0',

	/* =============== 图表/中立辅助色 =============== */
	'xyz-chart-axis': '#A3AED0',
	'xyz-disabled': '#d9d9d9',
	'xyz-code-bg': '#0a0f1a',
	'xyz-marketing-deep': '#1e2a4a',
} as const;

export const shadcnColors = {
	border: 'hsl(var(--border))',
	input: 'hsl(var(--input))',
	ring: 'hsl(var(--ring))',
	background: 'hsl(var(--background))',
	foreground: 'hsl(var(--foreground))',
	primary: {
		DEFAULT: 'hsl(var(--primary))',
		foreground: 'hsl(var(--primary-foreground))',
	},
	secondary: {
		DEFAULT: 'hsl(var(--secondary))',
		foreground: 'hsl(var(--secondary-foreground))',
	},
	destructive: {
		DEFAULT: 'hsl(var(--destructive))',
		foreground: 'hsl(var(--destructive-foreground))',
	},
	muted: {
		DEFAULT: 'hsl(var(--muted))',
		foreground: 'hsl(var(--muted-foreground))',
	},
	accent: {
		DEFAULT: 'hsl(var(--accent))',
		foreground: 'hsl(var(--accent-foreground))',
	},
	popover: {
		DEFAULT: 'hsl(var(--popover))',
		foreground: 'hsl(var(--popover-foreground))',
	},
	card: {
		DEFAULT: 'hsl(var(--card))',
		foreground: 'hsl(var(--card-foreground))',
	},
	sidebar: {
		DEFAULT: 'hsl(var(--sidebar-background))',
		foreground: 'hsl(var(--sidebar-foreground))',
		primary: 'hsl(var(--sidebar-primary))',
		'primary-foreground': 'hsl(var(--sidebar-primary-foreground))',
		accent: 'hsl(var(--sidebar-accent))',
		'accent-foreground': 'hsl(var(--sidebar-accent-foreground))',
		border: 'hsl(var(--sidebar-border))',
		ring: 'hsl(var(--sidebar-ring))',
	},
} as const;

export const borderRadius = {
	lg: 'var(--radius)',
	md: 'calc(var(--radius) - 2px)',
	sm: 'calc(var(--radius) - 4px)',
} as const;

export const fontFamily: Record<string, string[]> = {
	mono: ["'JetBrains Mono'", 'monospace'],
	code: ["'Courier Prime'", "'JetBrains Mono'", 'monospace'],
};

export const maxWidth = {
	xyz: '1460px',
} as const;

export const colors = { ...xyzColors, ...shadcnColors };

/** 快照比对的 token 面：只含"设计 token"，不含 keyframes/animation 等行为性配置。 */
export const designTokens = { colors, borderRadius, fontFamily, maxWidth };
