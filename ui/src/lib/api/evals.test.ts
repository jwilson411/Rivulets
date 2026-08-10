// Node-environment tests for the eval-suite resource client (see
// teams.test.ts for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { evals } from './evals';

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

describe('evals', () => {
	it('listSuites() GETs /evals/suites', async () => {
		const fetchMock = mockFetch([{ id: 's1' }]);

		const result = await evals.listSuites();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/evals/suites');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 's1' }]);
	});

	it('createSuite() POSTs the suite definition to /evals/suites', async () => {
		const fetchMock = mockFetch({ id: 's1' });

		await evals.createSuite({ name: 'Regression', agent_id: 'a1' });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/evals/suites');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({ name: 'Regression', agent_id: 'a1' }));
	});

	it('updateSuite() PATCHes /evals/suites/:id with the patch', async () => {
		const fetchMock = mockFetch({ id: 's1' });

		await evals.updateSuite('s1', { name: 'Renamed' });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/evals/suites/s1');
		expect(init.method).toBe('PATCH');
		expect(init.body).toBe(JSON.stringify({ name: 'Renamed' }));
	});

	it('deleteSuite() DELETEs /evals/suites/:id', async () => {
		const fetchMock = mockFetch(null, 204);

		await evals.deleteSuite('s1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/evals/suites/s1');
		expect(init.method).toBe('DELETE');
	});

	it('listCases() GETs /evals/suites/:id/cases', async () => {
		const fetchMock = mockFetch([{ id: 'c1' }]);

		const result = await evals.listCases('s1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/evals/suites/s1/cases');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'c1' }]);
	});

	it('createCase() POSTs the case definition to /evals/suites/:id/cases', async () => {
		const fetchMock = mockFetch({ id: 'c1' });

		await evals.createCase('s1', {
			name: 'greets politely',
			input_content: 'hello',
			judge_type: 'substring',
			expected_output: 'hi'
		});

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/evals/suites/s1/cases');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(
			JSON.stringify({
				name: 'greets politely',
				input_content: 'hello',
				judge_type: 'substring',
				expected_output: 'hi'
			})
		);
	});

	it('deleteCase() DELETEs /evals/suites/:id/cases/:caseId', async () => {
		const fetchMock = mockFetch(null, 204);

		await evals.deleteCase('s1', 'c1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/evals/suites/s1/cases/c1');
		expect(init.method).toBe('DELETE');
	});

	it('run() POSTs to /evals/suites/:id/run', async () => {
		const fetchMock = mockFetch({ id: 'r1', status: 'running' });

		const result = await evals.run('s1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/evals/suites/s1/run');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({}));
		expect(result).toEqual({ id: 'r1', status: 'running' });
	});

	it('listRuns() GETs /evals/suites/:id/runs', async () => {
		const fetchMock = mockFetch([{ id: 'r1' }]);

		const result = await evals.listRuns('s1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/evals/suites/s1/runs');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'r1' }]);
	});

	it('listResults() GETs /evals/suites/:id/runs/:runId/results', async () => {
		const fetchMock = mockFetch([{ id: 'res1' }]);

		const result = await evals.listResults('s1', 'r1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/evals/suites/s1/runs/r1/results');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'res1' }]);
	});
});
