import type { Config } from 'tailwindcss';
import typography from '@tailwindcss/typography';
import animate from 'tailwindcss-animate';

import { borderRadius, colors, fontFamily, maxWidth } from './src/design/tokens';

export default {
	darkMode: 'class',
	content: ['./index.html', './src/**/*.{ts,tsx}'],
	theme: {
		extend: {
			colors,
			borderRadius,
			fontFamily,
			maxWidth,
			keyframes: {
				'accordion-down': {
					from: { height: '0' },
					to: { height: 'var(--radix-accordion-content-height)' },
				},
				'accordion-up': {
					from: { height: 'var(--radix-accordion-content-height)' },
					to: { height: '0' },
				},
			},
			animation: {
				'accordion-down': 'accordion-down 0.2s ease-out',
				'accordion-up': 'accordion-up 0.2s ease-out',
			},
		},
	},
	plugins: [typography, animate],
} satisfies Config;
