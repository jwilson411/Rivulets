// Thin fetch wrapper for the App Server REST API
// (docs/architecture/api-design.md). Every call is same-origin relative to
// `/api/v1` — see vite.config.ts for how that resolves in dev vs. prod.

export class ApiError extends Error {
	status: number;

	constructor(status: number, message: string) {
		super(message);
		this.status = status;
	}
}

async function request<T>(path: string, init: RequestInit, token?: string): Promise<T> {
	const headers = new Headers(init.headers);
	headers.set('Content-Type', 'application/json');
	if (token) headers.set('Authorization', `Bearer ${token}`);

	const response = await fetch(`/api/v1${path}`, { ...init, headers });
	if (!response.ok) {
		const body = await response.text();
		throw new ApiError(response.status, body || response.statusText);
	}
	if (response.status === 204) return undefined as T;
	return (await response.json()) as T;
}

export const api = {
	get: <T>(path: string, token?: string) => request<T>(path, { method: 'GET' }, token),
	post: <T>(path: string, body: unknown, token?: string) =>
		request<T>(path, { method: 'POST', body: JSON.stringify(body) }, token),
	patch: <T>(path: string, body: unknown, token?: string) =>
		request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }, token),
	delete: <T>(path: string, token?: string) => request<T>(path, { method: 'DELETE' }, token)
};
