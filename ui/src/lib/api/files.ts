// File upload/download resource client (FR-10.1, FR-10.2,
// api-design.md#files). Not built on top of client.ts's `api` helper: that
// helper always JSON-encodes the request body and sets
// Content-Type: application/json, neither of which works for a multipart
// file upload (the browser must set its own Content-Type with the
// multipart boundary), and downloads need the response as a Blob, not
// parsed JSON.

import { ApiError } from './client';
import { auth } from './auth.svelte';

export interface UploadedFile {
	file_id: string;
	content_hash: string;
	filename: string;
	mime_type: string;
	size_bytes: number;
}

function authHeaders(): Headers {
	const headers = new Headers();
	const token = auth.token;
	if (token) headers.set('Authorization', `Bearer ${token}`);
	return headers;
}

export const files = {
	async upload(file: globalThis.File): Promise<UploadedFile> {
		const formData = new FormData();
		formData.append('upload', file);
		const response = await fetch('/api/v1/files/upload', {
			method: 'POST',
			body: formData,
			headers: authHeaders()
		});
		if (!response.ok) {
			const body = await response.text();
			throw new ApiError(response.status, body || response.statusText);
		}
		return (await response.json()) as UploadedFile;
	},

	// Triggers a browser download by fetching the file as a Blob and
	// clicking a throwaway object-URL anchor, rather than a plain
	// `<a href="/api/v1/files/{id}">` link -- the download endpoint requires
	// a Bearer token, which native `<a>` navigation can't attach as a
	// header. (The SSE stream endpoint accepts the token as a query param
	// instead, since EventSource has no alternative at all; a download does
	// have one via fetch, so it isn't worth extending that same
	// query-string-token tradeoff here too.)
	async download(fileId: string, filename: string): Promise<void> {
		const response = await fetch(`/api/v1/files/${fileId}`, { headers: authHeaders() });
		if (!response.ok) {
			const body = await response.text();
			throw new ApiError(response.status, body || response.statusText);
		}
		const blob = await response.blob();
		const url = URL.createObjectURL(blob);
		try {
			const link = document.createElement('a');
			link.href = url;
			link.download = filename;
			link.click();
		} finally {
			URL.revokeObjectURL(url);
		}
	}
};
