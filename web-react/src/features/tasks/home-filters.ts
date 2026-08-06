/**
 * Home 页三个分区的取数口径（F12）。
 *
 * ## ⚠️ 一、`now/d`、`now/w` 是**按调用方时区**做墙钟截断，但默认时区是 **UTC**
 *
 * 后端 `filters/datemath.py` 的文件头把两类截断分得很清楚：
 *
 *   `/d` `/w` `/M` `/y`  截断**墙钟**，在**调用方时区**里round（"Rounding happens in
 *                        the caller's timezone and the result is then converted to UTC"）
 *   `/h` `/m` `/s`       截断**绝对时间**（Go 的 `Time.Truncate`，从 UTC 零点数起）
 *
 * 也就是说 F12 用到的 `/d` 与 `/w` **是本地语义的** —— 前提是"调用方时区"对。
 * 而 `Options.location` 的默认值是 **UTC**：
 * **不发 `filter_timezone`，"今日"就是 UTC 的今日。** 在 +08:00 下，
 * 那是本地时间今天早上 8 点到明天早上 8 点 —— 用户会发现"今天到期"里混着昨天的任务、
 * 又缺了今晚的。所以下面每个查询都**必须**带上 `filter_timezone`。
 *
 * 附带纠正一个常见说法：`/h` 不落本地整点只发生在**非整小时偏移**的时区
 * （+05:30 印度、+05:45 尼泊尔）。+08:00 是整小时偏移，`/h` 在那里是对齐的。
 * 无论如何 F12 不用 `/h`。
 *
 * ## ⚠️ 二、周起点是**周一**
 *
 * `Options.start_of_week` 默认 `MONDAY`，且注释写明 "Calton only ever sets location"
 * —— 即上游从不改这个值。所以 `now/w` 是**周一零点**，不是周日。
 * 文案上说"本周"要与这个一致，别按周日起算去写说明。
 *
 * ## ⚠️ 三、不做本地时间预览
 *
 * 与 F11a 同一个理由：真值在服务端。前端另算一遍"本周是几号到几号"必然出现
 * 两套算法的偏差（时区、周起点、DST），而 datemath 的解析错误又被上游丢弃、
 * 拿不到服务端的解释。所以只发表达式、只展示服务端返回的任务，不预览区间。
 */

/** 浏览器的 IANA 时区名，作为 `filter_timezone` 发给后端。 */
export function browserTimezone(): string {
	return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/**
 * 今日到期且未完成。
 *
 * 区间取 `[now/d, now/d+1d)`：`>` 配 `<` 是半开区间，
 * 不用 `>=`/`<=` 是为了避免恰好落在午夜的任务被两天同时收进去。
 */
export const TODAY_FILTER = 'done = false && due_date > now/d && due_date < now/d+1d';

/** 本周到期且未完成。周一零点起算，见文件头第二条。 */
export const THIS_WEEK_FILTER = 'done = false && due_date > now/w && due_date < now/w+1w';

/**
 * 收藏的伪项目 id。
 *
 * ⚠️ **这是个 id，不是路由。** `parseRouteId` 只接受正整数，
 * 负数进不了 `/projects/:projectId/:view` —— 这是**有意的**：
 * 那条路由背后是"项目 + 四视图"，而伪项目没有 view。
 * 所以 Home 页的收藏区**就地渲染**，不往项目视图页跳。
 *
 * 换算关系（`permissions/pseudo.py`）：
 *   -1        收藏（固定值，没有对应的数据库行）
 *   < -1      saved filter，`filter_id = project_id * -1 - 1`（filter 1 ↔ -2）
 * 公式取 `-N-1` 而不是 `-N`，正是为了给 -1 让位。
 */
export const FAVORITES_PSEUDO_PROJECT_ID = -1;
