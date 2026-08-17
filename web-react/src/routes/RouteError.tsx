import { isRouteErrorResponse, useRouteError } from 'react-router-dom';

export function RouteError() {
	const error = useRouteError();

	const message = isRouteErrorResponse(error)
		? `${error.status} ${error.statusText}`
		: error instanceof Error
			? error.message
			: '未知错误';

	return (
		<section className="mx-auto max-w-md px-6 py-20 text-center" data-testid="route-error" role="alert">
			<h1 className="ink-heading text-2xl">出错了</h1>
			<p className="mt-3 text-sm text-muted-foreground">{message}</p>
		</section>
	);
}
