import { Link } from 'react-router-dom';

import { useTranslation } from '@/i18n/context';

export function NotFound() {
	const t = useTranslation();

	return (
		<section className="p-6" data-testid="not-found">
			<h1 className="text-lg font-semibold text-foreground">{t('404.title')}</h1>
			<p className="mt-2 text-sm text-muted-foreground">{t('404.text')}</p>
			<Link
				to="/"
				className="mt-2 inline-block text-sm text-primary underline-offset-4 hover:underline"
			>
				{t('navigation.overview')}
			</Link>
		</section>
	);
}
