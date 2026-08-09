// Thin fetch wrapper for the App Server REST API. Every call is same-origin
// relative to `/api/v1` — see vite.config.ts for how that resolves in dev vs. prod.

export class ApiError extends Error {
	status: number;

	constructor(status: number, message: string) {
		super(message);
		this.status = status;
	}
}

// A single item from FastAPI/Pydantic's 422 validation-error array, e.g.
// {"type": "string_pattern_mismatch", "loc": ["body", "name"], "msg": "String should match pattern '^[a-z][a-z0-9-]{1,63}$'"}
interface ValidationErrorItem {
	type?: unknown;
	loc?: unknown;
	msg?: unknown;
}

function fieldLabel(loc: unknown): string {
	if (!Array.isArray(loc) || loc.length === 0) return 'Value';
	const last = loc[loc.length - 1];
	if (typeof last !== 'string') return 'Value';
	return last.charAt(0).toUpperCase() + last.slice(1);
}

// Pydantic's raw messages for some error types leak the underlying regex or
// implementation detail, which reads as a bug report rather than guidance —
// translate those into plain language instead of showing them verbatim.
function friendlyValidationMessage(item: ValidationErrorItem): string {
	const field = fieldLabel(item.loc);
	if (item.type === 'string_pattern_mismatch' && field === 'Name') {
		return 'Name must be lowercase letters, numbers, and hyphens only, starting with a letter (e.g. test-workflow).';
	}
	if (typeof item.msg === 'string') return `${field}: ${item.msg}`;
	return `${field} is invalid.`;
}

// FastAPI's HTTPException responses are JSON: {"detail": "plain language message"}.
// Validation failures (422) instead return {"detail": [{...pydantic error...}, ...]}.
// Falling back to the raw body keeps unexpected error shapes visible instead of
// silently swallowing them.
function extractErrorMessage(body: string): string {
	try {
		const parsed: unknown = JSON.parse(body);
		if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
			const detail = (parsed as { detail: unknown }).detail;
			if (typeof detail === 'string') return detail;
			if (Array.isArray(detail) && detail.length > 0) {
				return detail
					.map((item) => friendlyValidationMessage(item as ValidationErrorItem))
					.join(' ');
			}
		}
	} catch {
		// not JSON — fall through to the raw body
	}
	return body;
}

async function request<T>(path: string, init: RequestInit, token?: string): Promise<T> {
	const headers = new Headers(init.headers);
	headers.set('Content-Type', 'application/json');
	if (token) headers.set('Authorization', `Bearer ${token}`);

	const response = await fetch(`/api/v1${path}`, { ...init, headers });
	if (!response.ok) {
		const body = await response.text();
		throw new ApiError(response.status, extractErrorMessage(body) || response.statusText);
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
	put: <T>(path: string, body: unknown, token?: string) =>
		request<T>(path, { method: 'PUT', body: JSON.stringify(body) }, token),
	delete: <T>(path: string, token?: string) => request<T>(path, { method: 'DELETE' }, token)
};
