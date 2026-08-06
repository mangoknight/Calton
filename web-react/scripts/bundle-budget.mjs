/**
 * 主 chunk 的体积上界。
 *
 * ⚠️ **这条不是性能优化，是防止无声膨胀。**
 *
 * 它不追求把包做小，只要求"变大这件事必须有人按过一次确认键"。
 * TipTap 那次是我主动报告才被注意到的 —— 下一个人加个大依赖不会有人注意，
 * 而包体一旦涨上去，F14 的 E2E 性能基线就建立在偏慢的产物上，
 * 之后所有对比都以它为参照，纠正成本比现在高得多。
 *
 * 超了怎么办：**先判断这次膨胀是否必要**。必要就把预算调上去并在 commit 里说明
 * 涨了多少、为什么；不必要就把依赖拆出去懒加载（参考任务详情页的 DescriptionEditor）。
 * ⛔ 不要为了让闸门变绿而随手调大数字——那等于把这条防线删掉。
 */

/** 主 chunk 的 gzip 预算（字节）。 */
export const MAIN_CHUNK_GZIP_BUDGET = 240_000;

/**
 * 定这个数的依据（2026-08-04，F13 i18n 落地之后）：
 * 主 chunk gzip 实测 199_725 字节，取 +20% 余量 ≈ 239_670 → 取整 240_000。
 * 余量是给正常迭代留的，不是给"再塞一个大依赖"留的。
 *
 * ## 上一版是 169_566 / 204_000，这次为什么涨
 *
 * 两笔，**分别量过**（同一台机器、同一条 `npm run build`）：
 *
 * | 来源 | 增量 | 说明 |
 * |---|---|---|
 * | F09–F12 合并进来的功能 | +8_113 | 上一版数字记于 2026-08-03，之后主线又并了几张卡；**不是本次改动造成的** |
 * | F13 i18n | **+22_046** | 其中 en.json 本身 20_122，运行时代码约 1_900 |
 *
 * 取证方式：把 i18n 的改动整体 stash 掉再构建一次，得 177_679 字节
 * （= 169_566 + 8_113）；带上 i18n 构建得 199_725 字节。差额即上表第二行。
 *
 * ## 这 22KB 能不能省掉
 *
 * **32 个语言包里只有 en 是静态打进主 chunk 的**，其余 37 个全是懒加载
 * （产物里能看到 `el-GR-*.js` 这类独立 chunk）。en 之所以不能懒加载，
 * 是因为它是**兜底语言**：`translate()` 在任何一次调用里都可能要同步取用它
 * （上游各语言包翻译进度不齐，缺 key 是常态，不是异常路径）。
 *
 * 真要省，唯一的路是"en 也懒加载 + 装载完成前不渲染文案"——
 * 用一次白屏换 20KB。当前判断是不值得，**但这是个可以推翻的判断**，
 * 推翻它不需要改这里的任何数字，改 `i18n/messages.ts` 就行。
 */
export const MAIN_CHUNK_GZIP_MEASURED = 199_725;

/** 主 chunk 的判别：Vite 给入口 chunk 的文件名前缀。 */
export const MAIN_CHUNK_PREFIX = 'index-';

/**
 * 纯判定，便于单测。返回 `ok` 与一句人能看懂的说明。
 *
 * @param {{ name: string, gzipBytes: number }[]} chunks
 * @param {number} budget
 */
export function evaluateBundleBudget(chunks, budget = MAIN_CHUNK_GZIP_BUDGET) {
	const main = chunks.find((chunk) => chunk.name.startsWith(MAIN_CHUNK_PREFIX));

	if (!main) {
		// 找不到主 chunk 说明构建产物形状变了 —— 这本身就该拦下来，
		// 否则闸门会在"什么都没检查"的状态下常绿
		return {
			ok: false,
			reason: 'main-chunk-not-found',
			message: `没有找到主 chunk（前缀 ${MAIN_CHUNK_PREFIX}）。构建产物形状变了？闸门不能在没检查任何东西的情况下放行。`,
		};
	}

	if (main.gzipBytes > budget) {
		const overBy = main.gzipBytes - budget;
		const percent = ((main.gzipBytes / budget - 1) * 100).toFixed(1);
		return {
			ok: false,
			reason: 'over-budget',
			message:
				`主 chunk ${main.name} 的 gzip 体积 ${main.gzipBytes} 字节，超出预算 ${budget} 字节 ` +
				`（多 ${overBy} 字节，+${percent}%）。\n` +
				`先判断这次膨胀是否必要：必要就调高 MAIN_CHUNK_GZIP_BUDGET 并在 commit 里说明原因；` +
				`不必要就把新依赖拆成懒加载 chunk（参考 TaskDetailPage 里的 DescriptionEditor）。`,
		};
	}

	return {
		ok: true,
		reason: null,
		message: `主 chunk ${main.name} gzip ${main.gzipBytes} 字节，预算 ${budget} 字节。`,
	};
}
