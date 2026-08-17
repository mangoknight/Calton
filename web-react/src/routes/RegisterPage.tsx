import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { Link, useNavigate } from 'react-router-dom';
import { z } from 'zod';

import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { useLogin, useRegister } from '@/features/auth/queries';
import { useTranslation } from '@/i18n/context';

/**
 * 长度限制照抄契约里的 v1.UserRegister：username 3-250、password 8-72、email ≤250。
 *
 * ⚠️ 消息存的是 **i18n key**，不是句子（理由见 `LoginPage.tsx` 同处注释）。
 *
 * ⚠️ **用户名长度那两条上游没有对应 key**，所以留的是中文原文 —— `t()` 找不到
 * 这个 key 时会**原样返回它**，正好显示成这句中文。这是有意的，不是漏迁：
 * 编一个上游没有的 key 塞进语言包会让 `lang-parity` 守卫红。
 * 密码/邮箱那几条上游有 key，用 key。
 */
const schema = z.object({
	username: z.string().trim().min(3, '用户名至少 3 个字符').max(250, '用户名最长 250 个字符'),
	email: z.string().trim().email('user.auth.emailInvalid').max(250, '邮箱最长 250 个字符'),
	password: z.string().min(8, 'user.auth.passwordNotMin').max(72, 'user.auth.passwordNotMax'),
});

type FormValues = z.infer<typeof schema>;

export function RegisterPage() {
	const navigate = useNavigate();
	const registerUser = useRegister();
	const login = useLogin();
	const t = useTranslation();

	const {
		register,
		handleSubmit,
		getValues,
		formState: { errors },
	} = useForm<FormValues>({
		resolver: zodResolver(schema),
		defaultValues: { username: '', email: '', password: '' },
	});

	const onSubmit = handleSubmit((values) => {
		// 注册接口返回的是 user 对象不是 token，所以注册成功后要自己再登录一次
		registerUser.mutate(values, {
			onSuccess: () => {
				const { username, password } = getValues();
				login.mutate({ username, password }, { onSuccess: () => navigate('/', { replace: true }) });
			},
		});
	});

	const pending = registerUser.isPending || login.isPending;
	const error = registerUser.error ?? login.error;

	return (
		<form onSubmit={onSubmit} className="space-y-5" data-testid="register-page" noValidate>
			<h1 className="ink-heading text-2xl">{t('user.auth.createAccount')}</h1>

			<Field
				label={t('user.auth.username')}
				htmlFor="username"
				error={errors.username?.message ? t(errors.username.message) : undefined}
			>
				<Input
					id="username"
					data-testid="register-username"
					autoComplete="username"
					autoFocus
					{...register('username')}
				/>
			</Field>

			<Field
				label={t('user.auth.email')}
				htmlFor="email"
				error={errors.email?.message ? t(errors.email.message) : undefined}
			>
				<Input
					id="email"
					data-testid="register-email"
					type="email"
					autoComplete="email"
					{...register('email')}
				/>
			</Field>

			<Field
				label={t('user.auth.password')}
				htmlFor="password"
				error={errors.password?.message ? t(errors.password.message) : undefined}
			>
				<Input
					id="password"
					data-testid="register-password"
					type="password"
					autoComplete="new-password"
					{...register('password')}
				/>
			</Field>

			{error ? (
				<p role="alert" className="text-sm text-xyz-red-6">
					{error.message}
				</p>
			) : null}

			<Button type="submit" data-testid="register-submit" className="w-full" disabled={pending}>
				{pending ? t('misc.loading') : t('user.auth.createAccount')}
			</Button>

			<p className="text-sm text-muted-foreground">
				{t('user.auth.alreadyHaveAnAccount')}
				<Link to="/login" className="text-primary underline-offset-4 hover:underline">
					{t('user.auth.login')}
				</Link>
			</p>
		</form>
	);
}
