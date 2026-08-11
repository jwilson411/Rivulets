// Node-environment tests for the knowledge base resource client (see
// budgets.test.ts for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { knowledgeBases } from './knowledgeBases';

vi.mock('./auth.svelte', () => ({
	auth: { token: 'test-token' }
}));

afterEach(() => {
	vi.unstubAllGlobals();
});

function mockFetch(body: unknown, status = 200) {
	// A 204 Response may not carry a body -- JSON.stringify(null) would
	// otherwise produce the string "null", and the Response constructor
	// rejects a non-null body on a null-body status code.
	const responseBody = body === null ? null : JSON.stringify(body);
	const fetchMock = vi.fn().mockResolvedValue(new Response(responseBody, { status }));
	vi.stubGlobal('fetch', fetchMock);
	return fetchMock;
}

describe('knowledgeBases', () => {
	it('list() GETs /knowledge-bases', async () => {
		const fetchMock = mockFetch([{ id: 'kb1' }]);

		const result = await knowledgeBases.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/knowledge-bases');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'kb1' }]);
	});

	it('get() GETs /knowledge-bases/:id', async () => {
		const fetchMock = mockFetch({ id: 'kb1' });

		const result = await knowledgeBases.get('kb1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/knowledge-bases/kb1');
		expect(init.method).toBe('GET');
		expect(result).toEqual({ id: 'kb1' });
	});

	it('create() POSTs the definition to /knowledge-bases', async () => {
		const fetchMock = mockFetch({ id: 'kb1' });

		await knowledgeBases.create({ name: 'Product Docs', scope_type: 'agent', agent_id: 'agent-1' });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/knowledge-bases');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(
			JSON.stringify({ name: 'Product Docs', scope_type: 'agent', agent_id: 'agent-1' })
		);
	});

	it('update() PATCHes /knowledge-bases/:id with the patch', async () => {
		const fetchMock = mockFetch({ id: 'kb1', name: 'Renamed' });

		await knowledgeBases.update('kb1', { name: 'Renamed' });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/knowledge-bases/kb1');
		expect(init.method).toBe('PATCH');
		expect(init.body).toBe(JSON.stringify({ name: 'Renamed' }));
	});

	it('remove() DELETEs /knowledge-bases/:id', async () => {
		const fetchMock = mockFetch(null, 204);

		await knowledgeBases.remove('kb1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/knowledge-bases/kb1');
		expect(init.method).toBe('DELETE');
	});

	it('listDocuments() GETs /knowledge-bases/:id/documents', async () => {
		const fetchMock = mockFetch([{ id: 'doc1' }]);

		const result = await knowledgeBases.listDocuments('kb1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/knowledge-bases/kb1/documents');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'doc1' }]);
	});

	it('ingestDocument() POSTs the file_id to /knowledge-bases/:id/documents', async () => {
		const fetchMock = mockFetch({ id: 'doc1', status: 'pending' });

		const result = await knowledgeBases.ingestDocument('kb1', 'file-1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/knowledge-bases/kb1/documents');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({ file_id: 'file-1' }));
		expect(result).toEqual({ id: 'doc1', status: 'pending' });
	});

	it('removeDocument() DELETEs /knowledge-bases/:id/documents/:documentId', async () => {
		const fetchMock = mockFetch(null, 204);

		await knowledgeBases.removeDocument('kb1', 'doc1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/knowledge-bases/kb1/documents/doc1');
		expect(init.method).toBe('DELETE');
	});
});
