import js from '@eslint/js';
import prettier from 'eslint-config-prettier';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
	// generated.ts 由 npm run gen:api 生成，不手改也不 lint
	{ ignores: ['dist', 'coverage', 'node_modules', '.cache', 'src/api/generated.ts'] },
	{
		extends: [js.configs.recommended, ...tseslint.configs.recommended],
		files: ['**/*.{ts,tsx}'],
		languageOptions: {
			ecmaVersion: 2022,
			globals: globals.browser,
		},
		plugins: {
			'react-hooks': reactHooks,
			'react-refresh': reactRefresh,
		},
		rules: {
			...reactHooks.configs.recommended.rules,
			'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
			'@typescript-eslint/no-unused-vars': [
				'error',
				{ argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
			],
		},
	},
	{
		// shadcn 生成的组件按上游约定同时导出组件与 cva variants，
		// 拆开会与后续 `shadcn add` 覆盖冲突。
		files: ['src/components/ui/**/*.tsx'],
		rules: { 'react-refresh/only-export-components': 'off' },
	},
	{
		files: ['**/*.test.{ts,tsx}', 'src/test/**/*.{ts,tsx}'],
		languageOptions: { globals: { ...globals.browser, ...globals.node } },
	},
	{
		files: ['*.config.{ts,js}'],
		languageOptions: { globals: globals.node },
	},
	prettier,
);
