import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { z } from 'zod';

import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { useLogin } from '@/features/auth/queries';
import { useTranslation } from '@/i18n/context';
import { safeRedirect } from '@/lib/redirect';

/**
 * ⚠️ 校验消息存的是 **i18n key**，不是句子。
 *
 * schema 是模块级常量，**只算一次** —— 存句子的话切语言时校验消息不会跟着变，
 * 而且只有校验消息不变，正是那种"大部分翻了、个别没翻"的最难察觉的形状
 * （与 `Sidebar.tsx` 的 NAV 表同一个理由）。翻译发生在渲染时，见下面的 `t(...)`。
 */
const schema = z.object({
	username: z.string().trim().min(1, 'user.auth.usernameRequired'),
	password: z.string().min(1, 'user.auth.passwordRequired'),
	totp_passcode: z.string().trim().optional(),
});

type FormValues = z.infer<typeof schema>;

export function LoginPage() {
	const navigate = useNavigate();
	const [searchParams] = useSearchParams();
	const login = useLogin();
	const t = useTranslation();

	const {
		register,
		handleSubmit,
		formState: { errors },
	} = useForm<FormValues>({
		resolver: zodResolver(schema),
		defaultValues: { username: '', password: '', totp_passcode: '' },
	});

	const onSubmit = handleSubmit((values) => {
		login.mutate(
			{
				username: values.username,
				password: values.password,
				// 没开 TOTP 的账号不能传空串，后端会当成错误的验证码
				...(values.totp_passcode ? { totp_passcode: values.totp_passcode } : {}),
			},
			{ onSuccess: () => navigate(safeRedirect(searchParams.get('redirect')), { replace: true }) },
		);
	});

	return (
		<form onSubmit={onSubmit} className="space-y-5" data-testid="login-page" noValidate>
			<h1 className="ink-heading text-2xl">{t('user.auth.login')}</h1>

			<Field
				label={t('user.auth.username')}
				htmlFor="username"
				error={errors.username?.message ? t(errors.username.message) : undefined}
			>
				<Input
					id="username"
					data-testid="login-username"
					autoComplete="username"
					autoFocus
					{...register('username')}
				/>
			</Field>

			<Field
				label={t('user.auth.password')}
				htmlFor="password"
				error={errors.password?.message ? t(errors.password.message) : undefined}
			>
				<Input
					id="password"
					data-testid="login-password"
					type="password"
					autoComplete="current-password"
					{...register('password')}
				/>
			</Field>

			<Field
				label={t('user.auth.totpTitle')}
				htmlFor="totp"
				error={errors.totp_passcode?.message ? t(errors.totp_passcode.message) : undefined}
			>
				<Input
					id="totp"
					data-testid="login-totp"
					inputMode="numeric"
					autoComplete="one-time-code"
					{...register('totp_passcode')}
				/>
			</Field>

			{login.isError ? (
				<p role="alert" className="text-sm text-xyz-red-6">
					{login.error.message}
				</p>
			) : null}

			<Button
				type="submit"
				data-testid="login-submit"
				className="w-full"
				disabled={login.isPending}
			>
				{login.isPending ? t('misc.loading') : t('user.auth.login')}
			</Button>

			<p className="text-sm text-muted-foreground">
				{t('user.auth.noAccountYet')}
				<Link to="/register" className="text-primary underline-offset-4 hover:underline">
					{t('user.auth.createAccount')}
				</Link>
			</p>
		</form>
	);
}
