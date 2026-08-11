// Browser-mode component test (see channels/[id]/channelPage.svelte.test.ts
// for the $app/state + $app/paths mocking pattern this route needs, since
// it reads its knowledge base id from page.params.id).

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import KnowledgeBaseDetailPage from './+page.svelte';
import {
	knowledgeBases,
	type KnowledgeBase,
	type KnowledgeBaseDocument
} from '$lib/api/knowledgeBases';
import { files } from '$lib/api/files';

vi.mock('$app/state', () => ({
	page: { params: { id: 'kb-1' }, url: new URL('http://localhost/knowledge-bases/kb-1') }
}));

vi.mock('$app/paths', () => ({
	resolve: (path: string, params?: Record<string, string>) => {
		let out = path;
		if (params) {
			for (const [key, value] of Object.entries(params)) out = out.replace(`[${key}]`, value);
		}
		return out;
	}
}));

vi.mock('$lib/api/knowledgeBases', () => ({
	knowledgeBases: {
		get: vi.fn(),
		listDocuments: vi.fn(),
		ingestDocument: vi.fn(),
		removeDocument: vi.fn()
	}
}));

vi.mock('$lib/api/files', () => ({
	files: { upload: vi.fn() }
}));

const productDocs: KnowledgeBase = {
	id: 'kb-1',
	name: 'Product Docs',
	description: null,
	scope_type: 'agent',
	agent_id: 'agent-1',
	team_id: null,
	document_count: 1
};

const ingestedDoc: KnowledgeBaseDocument = {
	id: 'doc-1',
	knowledge_base_id: 'kb-1',
	file_id: 'file-1',
	status: 'ingested',
	error_message: null,
	chunk_count: 4
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('knowledge-bases/[id]/+page.svelte', () => {
	it('renders the knowledge base name and its ingested documents', async () => {
		vi.mocked(knowledgeBases.get).mockResolvedValue(productDocs);
		vi.mocked(knowledgeBases.listDocuments).mockResolvedValue([ingestedDoc]);

		render(KnowledgeBaseDetailPage);

		await expect.element(page.getByText('Product Docs')).toBeInTheDocument();
		await expect.element(page.getByText('Ingested')).toBeInTheDocument();
		await expect.element(page.getByText('4 chunks')).toBeInTheDocument();
	});

	it('removes a document via knowledgeBases.removeDocument and refreshes', async () => {
		vi.mocked(knowledgeBases.get).mockResolvedValue(productDocs);
		vi.mocked(knowledgeBases.listDocuments)
			.mockResolvedValueOnce([ingestedDoc])
			.mockResolvedValueOnce([]);
		vi.mocked(knowledgeBases.removeDocument).mockResolvedValueOnce(undefined);

		render(KnowledgeBaseDetailPage);

		await expect.element(page.getByText('Ingested')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Remove' }).click();

		expect(knowledgeBases.removeDocument).toHaveBeenCalledWith('kb-1', 'doc-1');
		await expect
			.element(page.getByText('No documents ingested yet — add one above.'))
			.toBeInTheDocument();
	});

	it('shows a failed document with its error message', async () => {
		vi.mocked(knowledgeBases.get).mockResolvedValue(productDocs);
		vi.mocked(knowledgeBases.listDocuments).mockResolvedValue([
			{ ...ingestedDoc, status: 'failed', error_message: 'No OpenAI provider configured' }
		]);

		render(KnowledgeBaseDetailPage);

		await expect.element(page.getByText('Failed')).toBeInTheDocument();
		await expect.element(page.getByText('No OpenAI provider configured')).toBeInTheDocument();
	});

	it('shows a load error instead of the knowledge base when loading fails', async () => {
		vi.mocked(knowledgeBases.get).mockRejectedValueOnce(new Error('Knowledge base not found'));
		vi.mocked(knowledgeBases.listDocuments).mockResolvedValue([]);

		render(KnowledgeBaseDetailPage);

		await expect.element(page.getByText('Knowledge base not found')).toBeInTheDocument();
	});

	it('uploads a document via the file input, shows the ingesting state, and refreshes the list', async () => {
		vi.mocked(knowledgeBases.get).mockResolvedValue(productDocs);
		vi.mocked(knowledgeBases.listDocuments)
			.mockResolvedValueOnce([])
			.mockResolvedValueOnce([ingestedDoc]);
		let resolveUpload: (value: {
			file_id: string;
			content_hash: string;
			filename: string;
			mime_type: string;
			size_bytes: number;
		}) => void = () => {};
		vi.mocked(files.upload).mockReturnValue(
			new Promise((resolve) => {
				resolveUpload = resolve;
			})
		);
		vi.mocked(knowledgeBases.ingestDocument).mockResolvedValueOnce(ingestedDoc);

		const { container } = await render(KnowledgeBaseDetailPage);
		await expect
			.element(page.getByText('No documents ingested yet — add one above.'))
			.toBeInTheDocument();

		const input = container.querySelector('input[type="file"]') as HTMLInputElement;
		await page
			.elementLocator(input)
			.upload(new File(['hello'], 'notes.txt', { type: 'text/plain' }));

		await expect.element(page.getByText('Ingesting…')).toBeInTheDocument();

		resolveUpload({
			file_id: 'file-1',
			content_hash: 'hash',
			filename: 'notes.txt',
			mime_type: 'text/plain',
			size_bytes: 5
		});

		await expect.element(page.getByText('Ingested')).toBeInTheDocument();
		expect(knowledgeBases.ingestDocument).toHaveBeenCalledWith('kb-1', 'file-1');
	});

	it('shows an upload error when ingestion fails', async () => {
		vi.mocked(knowledgeBases.get).mockResolvedValue(productDocs);
		vi.mocked(knowledgeBases.listDocuments).mockResolvedValue([]);
		vi.mocked(files.upload).mockRejectedValueOnce(new Error('File type not supported'));

		const { container } = await render(KnowledgeBaseDetailPage);
		await expect
			.element(page.getByText('No documents ingested yet — add one above.'))
			.toBeInTheDocument();

		const input = container.querySelector('input[type="file"]') as HTMLInputElement;
		await page
			.elementLocator(input)
			.upload(new File(['hello'], 'notes.txt', { type: 'text/plain' }));

		await expect.element(page.getByText('File type not supported')).toBeInTheDocument();
		expect(knowledgeBases.ingestDocument).not.toHaveBeenCalled();
	});

	it('shows an action error when removing a document fails', async () => {
		vi.mocked(knowledgeBases.get).mockResolvedValue(productDocs);
		vi.mocked(knowledgeBases.listDocuments).mockResolvedValue([ingestedDoc]);
		vi.mocked(knowledgeBases.removeDocument).mockRejectedValueOnce(
			new Error('Failed to remove document')
		);

		render(KnowledgeBaseDetailPage);

		await expect.element(page.getByText('Ingested')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Remove' }).click();

		await expect.element(page.getByText('Failed to remove document')).toBeInTheDocument();
		await expect.element(page.getByText('Ingested')).toBeInTheDocument();
	});
});
