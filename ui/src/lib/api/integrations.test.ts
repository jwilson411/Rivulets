import { afterEach, describe, expect, it, vi } from 'vitest';
import { integrations } from './integrations';

vi.mock('./auth.svelte', () => ({
	auth: { token: 'test-token' }
}));

afterEach(() => {
	vi.unstubAllGlobals();
});

function mockFetch(body: unknown, status = 200) {
	const responseBody = body === null ? null : JSON.stringify(body);
	const fetchMock = vi.fn().mockResolvedValue(new Response(responseBody, { status }));
	vi.stubGlobal('fetch', fetchMock);
	return fetchMock;
}

describe('integrations', () => {
	it('list() GETs /integrations', async () => {
		const fetchMock = mockFetch([]);

		const result = await integrations.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/integrations');
		expect(init.method).toBe('GET');
		expect(result).toEqual([]);
	});

	it('googleOAuthApp() GETs /integrations/google/oauth-app', async () => {
		const fetchMock = mockFetch({ provider: 'google', client_id: '', has_client_secret: false });

		await integrations.googleOAuthApp();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/integrations/google/oauth-app');
		expect(init.method).toBe('GET');
	});

	it('saveGoogleOAuthApp() PUTs the client', async () => {
		const fetchMock = mockFetch({ provider: 'google', client_id: 'abc', has_client_secret: true });

		await integrations.saveGoogleOAuthApp({ client_id: 'abc', client_secret: 's' });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/integrations/google/oauth-app');
		expect(init.method).toBe('PUT');
		expect(init.body).toBe(JSON.stringify({ client_id: 'abc', client_secret: 's' }));
	});

	it('connectGoogle() POSTs /integrations/google/connect', async () => {
		const fetchMock = mockFetch({ authorization_url: 'https://accounts.google.com/o' });

		const result = await integrations.connectGoogle('Work');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/integrations/google/connect');
		expect(init.method).toBe('POST');
		expect(result.authorization_url).toContain('accounts.google.com');
	});

	it('disconnect() DELETEs /integrations/:id', async () => {
		const fetchMock = mockFetch(null, 204);

		await integrations.disconnect('acc-1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/integrations/acc-1');
		expect(init.method).toBe('DELETE');
	});
});
