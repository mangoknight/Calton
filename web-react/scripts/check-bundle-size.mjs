#!/usr/bin/env node
/**
 * 构建后的包体闸门。由 `npm run build` 在 vite build 之后调用。
 * 判定逻辑在 `bundle-budget.mjs`（纯函数，有单测），这里只负责读产物。
 */
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';

import { evaluateBundleBudget } from './bundle-budget.mjs';

const distAssets = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist', 'assets');

let files;
try {
	files = readdirSync(distAssets).filter((name) => name.endsWith('.js'));
} catch {
	console.error(`[bundle] 读不到 ${distAssets} —— 先跑 vite build。`);
	process.exit(1);
}

const chunks = files.map((name) => ({
	name,
	gzipBytes: gzipSync(readFileSync(join(distAssets, name))).length,
}));

const result = evaluateBundleBudget(chunks);

for (const chunk of [...chunks].sort((a, b) => b.gzipBytes - a.gzipBytes)) {
	console.log(`[bundle] ${chunk.name}  gzip ${chunk.gzipBytes} B`);
}

if (!result.ok) {
	console.error(`\n[bundle] ✗ ${result.message}`);
	process.exit(1);
}

console.log(`[bundle] ✓ ${result.message}`);
