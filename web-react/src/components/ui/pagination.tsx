import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/context';

/**
 * 分页控件。F06 表格视图会复用，所以做成受控的纯展示组件。
 *
 * `totalPages` 来自 `x-pagination-total-pages`，不是本地算的 ——
 * 后端在 `result_count == 0` 时会把它强制成 0，所以 0 页是合法状态（= 没有内容）。
 */
export interface PaginationProps {
	page: number;
	totalPages: number;
	/** 本页条数（来自 x-pagination-result-count），用于给出"第 N 页 / 共 M 页"之外的手感。 */
	resultCount?: number;
	onPageChange: (page: number) => void;
	/** 翻页请求进行中：按钮禁用，避免连点把 page 甩过头。 */
	busy?: boolean;
}

export function Pagination({
	page,
	totalPages,
	resultCount,
	onPageChange,
	busy = false,
}: PaginationProps) {
	const t = useTranslation();
	// 只有一页（或空）时没有翻页的必要，整个控件不渲染
	if (totalPages <= 1) return null;

	const canPrev = page > 1;
	const canNext = page < totalPages;

	// ⚠️ "第 N / M 页（本页 X 条）"与 aria-label "分页" **上游没有对应 key**
	// （上游是无限滚动 + 单独的 Previous/Next，没有这种页码摘要）。留中文是有意的，不编 key。
	return (
		<nav
			aria-label="分页"
			data-testid="pagination"
			className="flex shrink-0 items-center justify-between gap-4 border-t border-border px-1 pt-3"
		>
			<p className="text-sm text-muted-foreground" data-testid="pagination-status">
				第 {page} / {totalPages} 页{resultCount === undefined ? null : `（本页 ${resultCount} 条）`}
			</p>

			<div className="flex items-center gap-2">
				<Button
					type="button"
					variant="outline"
					size="sm"
					data-testid="pagination-prev"
					disabled={!canPrev || busy}
					onClick={() => onPageChange(page - 1)}
				>
					{t('misc.previous')}
				</Button>
				<Button
					type="button"
					variant="outline"
					size="sm"
					data-testid="pagination-next"
					disabled={!canNext || busy}
					onClick={() => onPageChange(page + 1)}
				>
					{t('misc.next')}
				</Button>
			</div>
		</nav>
	);
}
