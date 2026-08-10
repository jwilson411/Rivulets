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
});
