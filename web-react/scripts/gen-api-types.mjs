#!/usr/bin/env node
/**
 * 从 swagger 生成 API 类型：src/api/generated.ts
 *
 * 契约来源优先级（第一个存在的胜出）：
 *   1. CALTON_SWAGGER 环境变量指定的路径
 *   2. server/contract/calton-v1-corrected.json —— ★ 修正版，最终以它为准
 *      （原始 swagger 有三处标注错误，最坑的是 label 更新真实是 POST 却标成 PUT）
 *   3. server/contract/calton-v1-swagger.json   —— T06 固化的原始契约
 *   4. pkg/swagger/swagger.json                  —— Go 版现产出，兜底
 *
 * 用到 2 以外的来源时会打警告：那说明生成的类型在已知的三处上是错的，
 * 必须以 src/api/swagger-corrections.ts 为准。
 *
 * 上游 swagger 是 2.0，openapi-typescript v7 只吃 OpenAPI 3.x，中间过一道
 * swagger2openapi 转换。转换产物写到 .cache/ 便于人工比对，不入库。
 */
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import openapiTS, { astToString } from 'openapi-typescript';
import swagger2openapi from 'swagger2openapi';

import {
	CONTRACT_DIR_REL,
	CORRECTED_CONTRACT_FILENAME,
	CORRECTED_MARKER,
	evaluateContractSource,
	RAW_CONTRACT_FILENAME,
} from './contract-source.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, '..');
const repoRoot = resolve(webRoot, '..');

const CONTRACT_DIR = resolve(repoRoot, CONTRACT_DIR_REL);

/** 修正版契约的路径 —— 只有用它生成出来的类型才是可信的（文件名见 contract-source.mjs）。 */
const CORRECTED = resolve(CONTRACT_DIR, CORRECTED_CONTRACT_FILENAME);

const CANDIDATES = [
	process.env.CALTON_SWAGGER,
	CORRECTED,
	resolve(CONTRACT_DIR, RAW_CONTRACT_FILENAME),
	resolve(repoRoot, 'pkg/swagger/swagger.json'),
].filter(Boolean);

const source = CANDIDATES.find((path) => existsSync(path));
if (!source) {
	console.error(`找不到 swagger 契约，依次找过：\n  ${CANDIDATES.join('\n  ')}`);
	process.exit(1);
}

const sourceLabel = relative(repoRoot, source);

// 来源信息也走 stderr：CI 里它属于诊断信息，别混进正常输出
console.warn(`契约来源: ${sourceLabel}`);

const rawText = await readFile(source, 'utf8');
const sha256 = createHash('sha256').update(rawText).digest('hex');
const raw = JSON.parse(rawText);

/**
 * ★ 判断"是不是修正版"以**契约自带的标记**为准，不看文件名。
 *
 * 修正版契约会把 info.version 标成以 `-corrected` 结尾（后端契约测试
 * server/tests/contract/test_contract.py 有同名断言，两边校验同一个事实）。
 *
 * 为什么不能只看文件名：路径写错、文件被换、将来有人重命名，任何一种都能让
 * 一份未修正的契约顶着正确的文件名混进来，而生成的 generated.ts 不会有任何异样。
 * 我自己就踩过一次 —— 验证闸门时把 pkg/swagger 复制成 calton-v1-corrected.json，
 * 产物的 provenance 就跟着谎称 is_corrected_contract=true。改成看标记后这种情况直接报错。
 */
/**
 * ★ 回退闸门：契约目录一旦存在，用了**任何非修正版**契约都直接失败。
 *
 * 判定逻辑抽在 contract-source.mjs 的 evaluateContractSource（纯函数，与文件系统解耦），
 * 四个场景有常驻回归测试 —— 之前这几个场景只有我手工跑过，属于"没有测试的防线"。
 *
 * 为什么用未修正的契约同样危险（tester 实测的 operation 差异）：
 *   corrected 比 raw 多：GET /projects/{project}/tasks、GET /token/test、POST /labels/{label}
 *   corrected 比 raw 少：PUT /labels/{id}
 * 用 raw 生成会得到一个**服务端根本不存在的 `PUT /labels/{id}`**（v1 动词是倒置的，
 * 实际是 POST），F10 照它调必然 404；同时**缺** `GET /projects/{project}/tasks`
 * ——TaskCollection 三入口之一，F05b 的 List 视图要用。
 * 这两件事 tsc 全绿、没有红灯，所以必须在生成阶段就拦下。
 */
const { isCorrected, ok, reason } = evaluateContractSource({
	infoVersion: raw.info?.version,
	contractDirExists: existsSync(CONTRACT_DIR),
	allowFallback: process.env.ALLOW_SWAGGER_FALLBACK === '1',
});

if (!ok) {
	console.error(
		`✗ server/contract/ 已存在，但用的契约没有修正版标记：${sourceLabel}\n` +
			`  期望 info.version 以 "${CORRECTED_MARKER}" 结尾，实际为 ${JSON.stringify(raw.info?.version)}。\n` +
			`  期望使用 ${relative(repoRoot, CORRECTED)}。\n` +
			'  用非修正版生成会得到服务端不存在的 PUT /labels/{id}（实际是 POST /labels/{label}），\n' +
			'  并且缺少 GET /projects/{project}/tasks —— 两者都不会让 tsc 变红，只会在运行时 404。\n' +
			'  确认要临时这么做，请显式设置 ALLOW_SWAGGER_FALLBACK=1。',
	);
	process.exit(1);
}

if (reason === 'explicitly-allowed') {
	console.warn(`⚠️  ALLOW_SWAGGER_FALLBACK=1：已显式允许使用非修正版契约 ${sourceLabel}。`);
}

if (!isCorrected) {
	console.warn(
		'⚠️  用的不是修正版契约，生成的类型在已知三处标注错误上是错的：\n' +
			'    GET /token/test 与 GET /projects/{project}/tasks 漏标；\n' +
			'    label 更新真实是 POST /labels/{label}（swagger 标成 PUT，照调会 404）。\n' +
			'    这三处以 src/api/swagger-corrections.ts 为准。',
	);
}

/**
 * swagger2openapi 的 patch 会替我们补齐不合规的字段（Go 的 swag 把 info.version
 * 留成了 null）。补了什么必须留痕并入库 —— 这样"patch 补的东西变了"会在
 * code review 里表现成 diff，而不是无人察觉。
 */
let openapi = raw;
let warnings = [];
if (raw.swagger === '2.0') {
	const result = await swagger2openapi.convertObj(raw, { patch: true, warnOnly: true });
	warnings = result.options?.warnings ?? [];
	openapi = result.openapi;
}
for (const warning of warnings) console.warn(`  swagger2openapi patch: ${warning}`);

/**
 * ⚠️ 实测：patch 把 info.version 从 null 改成了 ''，但 warnings **返回空数组** ——
 * 光记 warnings 是记不住这次改动的，空列表永远不会产生 diff。
 * 所以这里额外把 info 的实际前后值落盘，让"补的东西变了"真的能在 review 里看见。
 */
const infoPatches = Object.fromEntries(
	['version', 'title']
		.map((key) => [key, raw.info?.[key] ?? null, openapi.info?.[key] ?? null])
		.filter(([, before, after]) => before !== after)
		.map(([key, before, after]) => [key, { before, after }]),
);

await mkdir(resolve(webRoot, '.cache'), { recursive: true });
await writeFile(resolve(webRoot, '.cache/openapi-3.json'), JSON.stringify(openapi, null, 2));

const pathCount = Object.keys(openapi.paths ?? {}).length;
const generatedOn = new Date().toISOString().slice(0, 10);

// patch 警告清单入库：内容变了就是 diff
await writeFile(
	resolve(webRoot, 'src/api/generated.provenance.json'),
	`${JSON.stringify(
		{
			_note:
				'由 npm run gen:api 生成，勿手改。patch 警告清单入库是为了让"转换器补的东西变了"在 code review 里成为 diff。',
			source: sourceLabel,
			source_sha256: sha256,
			generated_on: generatedOn,
			is_corrected_contract: isCorrected,
			path_count: pathCount,
			swagger2openapi_patch_warnings: warnings,
			// warnings 实测为空也要记 info 的实际改动，否则这次 patch 无痕
			swagger2openapi_info_patches: infoPatches,
		},
		null,
		2,
	)}\n`,
);

const banner = `/**
 * 本文件由 \`npm run gen:api\` 生成，请勿手改。
 *
 * 契约来源: ${sourceLabel}
 * sha256:   ${sha256}
 * 生成日期: ${generatedOn}
 * path 数:  ${pathCount}
 * 修正版契约: ${isCorrected ? '是' : '否 —— 已知三处标注错误未修正，以 src/api/swagger-corrections.ts 为准'}
 * swagger2openapi patch: 警告 ${warnings.length} 条、info 字段改动 ${Object.keys(infoPatches).length} 处（详见 src/api/generated.provenance.json）
 */
`;

const ast = await openapiTS(openapi, { alphabetize: true });
const out = resolve(webRoot, 'src/api/generated.ts');
await mkdir(dirname(out), { recursive: true });
await writeFile(out, banner + astToString(ast));

console.log(
	`已写入 src/api/generated.ts（${pathCount} 条 path，patch 警告 ${warnings.length} 条）`,
);
