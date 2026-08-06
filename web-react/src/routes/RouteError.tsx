import { isRouteErrorResponse, useRouteError } from 'react-router-dom';

export function RouteError() {
	const error = useRouteError();

	const message = isRouteErrorResponse(error)
		? `${error.status} ${error.statusText}`
		: error instanceof Error
			? error.message
			: '未知错误';

	return (
		<section className="p-6" data-testid="route-error" role="alert">
			<h1 className="text-lg font-semibold text-foreground">出错了</h1>
			<p className="mt-2 text-sm text-muted-foreground">{message}</p>
		</section>
	);
}
