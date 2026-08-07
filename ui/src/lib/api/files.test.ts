// Node-environment tests for the file upload/download client. Unlike the
// other resource clients this one bypasses client.ts's `api` wrapper (see
// files.ts's header comment for why), so it's tested against raw fetch
// directly. download() also touches browser-only globals (document, the
// Blob-URL object) that don't exist in vitest's "server" node environment,
// so those are stubbed locally rather than pulling this file into the
// browser project just for two calls.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './client';
import { files } from './files';

vi.mock('./auth.svelte', () => ({
	auth: { token: 'test-token' }
}));

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('files.upload', () => {
	it('POSTs a multipart form to /api/v1/files/upload with the Authorization header', async () => {
		const responseBody = {
			file_id: 'f1',
			content_hash: 'abc',
			filename: 'a.txt',
			mime_type: 'text/plain',
			size_bytes: 3
		};
		const fetchMock = vi
			.fn()
			.mockResolvedValue(new Response(JSON.stringify(responseBody), { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);
		const file = new File(['abc'], 'a.txt', { type: 'text/plain' });

		const result = await files.upload(file);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/files/upload');
		expect(init.method).toBe('POST');
		expect(init.body).toBeInstanceOf(FormData);
		expect((init.body as FormData).get('upload')).toBe(file);
		expect((init.headers as Headers).get('Authorization')).toBe('Bearer test-token');
		expect(result).toEqual(responseBody);
	});

	it('does not set a Content-Type header, leaving the browser to set the multipart boundary', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await files.upload(new File(['x'], 'x.txt'));

		const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect((init.headers as Headers).has('Content-Type')).toBe(false);
	});

	it('throws ApiError with the raw response body on failure', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('too large', { status: 413 }));
		vi.stubGlobal('fetch', fetchMock);

		await expect(files.upload(new File(['x'], 'x.txt'))).rejects.toMatchObject(
			new ApiError(413, 'too large')
		);
	});
});

describe('files.download', () => {
	function stubDom() {
		const link = { href: '', download: '', click: vi.fn() };
		const createElement = vi.fn().mockReturnValue(link);
		vi.stubGlobal('document', { createElement });
		vi.stubGlobal('URL', {
			createObjectURL: vi.fn().mockReturnValue('blob:mock-url'),
			revokeObjectURL: vi.fn()
		});
		return { link, createElement };
	}

	it('fetches the file with auth, then clicks a throwaway download link', async () => {
		const blob = new Blob(['file contents']);
		const fetchMock = vi.fn().mockResolvedValue(new Response(blob, { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);
		const { link } = stubDom();

		await files.download('f1', 'report.pdf');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/files/f1');
		expect((init.headers as Headers).get('Authorization')).toBe('Bearer test-token');
		expect(link.download).toBe('report.pdf');
		expect(link.href).toBe('blob:mock-url');
		expect(link.click).toHaveBeenCalledOnce();
		expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');
	});

	it('throws ApiError and never touches the DOM when the fetch fails', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('not found', { status: 404 }));
		vi.stubGlobal('fetch', fetchMock);
		const { createElement } = stubDom();

		await expect(files.download('missing', 'x.pdf')).rejects.toMatchObject(
			new ApiError(404, 'not found')
		);
		expect(createElement).not.toHaveBeenCalled();
	});
});
