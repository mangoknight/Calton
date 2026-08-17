import { Link } from 'react-router-dom';

import { useTranslation } from '@/i18n/context';

export function NotFound() {
	const t = useTranslation();

	return (
		<section className="mx-auto max-w-md px-6 py-20 text-center" data-testid="not-found">
			<h1 className="ink-heading text-2xl">{t('404.title')}</h1>
			<p className="mt-3 text-sm text-muted-foreground">{t('404.text')}</p>
			<Link
				to="/"
				className="mt-4 inline-block text-sm text-primary underline-offset-4 hover:underline"
			>
				{t('navigation.overview')}
			</Link>
		</section>
	);
}
