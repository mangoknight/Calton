import { describe, expect, it } from 'vitest';

import { CaltonError } from '@/api/errors';
import type { Label } from '@/api/labels';
import { canManageLabel, labelWriteErrorMessage } from './permissions';

function label(creatorId: number | undefined): Label {
	return {
		id: 950,
		title: 'X-alpha',
		created_by: creatorId === undefined ? null : { id: creatorId, username: 'someone' },
	};
}

describe('canManageLabel（改/删 = 仅创建者）', () => {
	it('自己建的可以改', () => {
		expect(canManageLabel(label(900), 900)).toBe(true);
	});

	it('★ 别人建的不能改 —— 哪怕它出现在你的可见列表里', () => {
		// 语料 label.update.other_owner_403：954 alice 能读也能用，但改它是 403。
		// 这条是"读/用 vs 改/删"分界线在前端的落点。
		expect(canManageLabel(label(999), 900)).toBe(false);
	});

	it('★ created_by 缺失时失败关闭（不给按钮），而不是放行', () => {
		// 放行的话按钮点下去必然 403，用户只会反复试
		expect(canManageLabel(label(undefined), 900)).toBe(false);
	});

	it('★ 当前用户还没加载出来时失败关闭', () => {
		// undefined 与"任何 id"比较都为 false，但显式钉住：
		// 若实现写成 `label.created_by?.id === currentUserId`，两边同为 undefined 时会
		// 意外相等 —— created_by 缺失 + 用户未加载会一起把按钮放出来。
		expect(canManageLabel(label(900), undefined)).toBe(false);
		expect(canManageLabel(label(undefined), undefined)).toBe(false);
	});
});

describe('labelWriteErrorMessage（读写口径相反，文案必须分两支）', () => {
	function error(status: number, body: unknown) {
		return new CaltonError(status, body, 'fallback');
	}

	it('★ 404/8002 说"已不存在"（写路径才泄露存在性）', () => {
		const message = labelWriteErrorMessage(
			error(404, { code: 8002, message: 'This label does not exist.' }),
		);
		expect(message).toContain('不存在');
		// 不能把后端英文原文直接甩给用户
		expect(message).not.toContain('does not exist');
	});

	it('★ 403 说"仅创建者可改"，且与 404 的文案不同', () => {
		const forbidden = labelWriteErrorMessage(error(403, { code: 0, message: 'Forbidden' }));
		const missing = labelWriteErrorMessage(error(404, { code: 8002, message: 'x' }));

		expect(forbidden).toContain('创建者');
		// ☠ 这条断言的全部意义在于"两支不同"。统一成一句万能文案时它会红，
		//   而只断言 `toContain('创建者')` 的话，统一文案里带上"创建者"三个字就能蒙混过去。
		expect(forbidden).not.toBe(missing);
	});

	it('其他错误原样透出后端消息，不吞错', () => {
		expect(labelWriteErrorMessage(error(500, { code: 1, message: '服务器开小差' }))).toBe(
			'服务器开小差',
		);
	});
});
