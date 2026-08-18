// Browser-mode component test for the knowledge base detail (06-screens.md
// → Knowledge bases, mockup 2h): the big dropzone, document rows with
// plain-language status pills, and Remove.

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
import { agents } from '$lib/api/agents';
import { teams } from '$lib/api/teams';

vi.mock('$app/state', () => ({
	page: { params: { id: 'kb-1' }, url: new URL('http://localhost/knowledge-bases/kb-1') }
}));

vi.mock('$app/paths', () => ({
	resolve: (path: string) => path
}));

vi.mock('$lib/api/knowledgeBases', () => ({
	knowledgeBases: {
		get: vi.fn(),
		listDocuments: vi.fn(),
		ingestDocument: vi.fn(),
		removeDocument: vi.fn()
	}
}));

vi.mock('$lib/api/files', () => ({ files: { upload: vi.fn() } }));
vi.mock('$lib/api/agents', () => ({ agents: { list: vi.fn() } }));
vi.mock('$lib/api/teams', () => ({ teams: { list: vi.fn() } }));

afterEach(() => {
	vi.clearAllMocks();
});

const launchNotes: KnowledgeBase = {
	id: 'kb-1',
	name: 'Launch notes',
	description: null,
	scope_type: 'team',
	agent_id: null,
	team_id: 'team-1',
	document_count: 1
};

const ingestedDoc: KnowledgeBaseDocument = {
	id: 'doc-1',
	knowledge_base_id: 'kb-1',
	file_id: 'file-abc12345',
	filename: 'launch-notes.md',
	status: 'ingested',
	error_message: null,
	chunk_count: 12
};

function seed(docs: KnowledgeBaseDocument[] = [ingestedDoc]) {
	vi.mocked(knowledgeBases.get).mockResolvedValue(launchNotes);
	vi.mocked(knowledgeBases.listDocuments).mockResolvedValue(docs);
	vi.mocked(agents.list).mockResolvedValue([]);
	vi.mocked(teams.list).mockResolvedValue([
		{ id: 'team-1', name: 'Starter Team', description: null }
	]);
}

describe('knowledge-bases/[id]/+page.svelte', () => {
	it('shows the base, who it belongs to, and the dropzone', async () => {
		seed();

		render(KnowledgeBaseDetailPage);

		await expect.element(page.getByText('Launch notes')).toBeInTheDocument();
		await expect.element(page.getByText('Belongs to Starter Team')).toBeInTheDocument();
		await expect.element(page.getByText('Drop markdown, text, or JSON')).toBeInTheDocument();
	});

	it('shows document rows with a plain-language Ready pill and chunk count', async () => {
		seed();

		render(KnowledgeBaseDetailPage);

		// Filename comes from the document API (#467), not session memory —
		// this is the same payload a refresh would get.
		await expect.element(page.getByText('launch-notes.md')).toBeInTheDocument();
		await expect.element(page.getByText('Ready')).toBeInTheDocument();
		await expect.element(page.getByText('12 chunks')).toBeInTheDocument();
	});

	it('says "Couldn\'t read" for a failed document, with its error', async () => {
		seed([
			{
				...ingestedDoc,
				id: 'doc-2',
				status: 'failed',
				error_message: 'Unsupported encoding',
				chunk_count: 0
			}
		]);

		render(KnowledgeBaseDetailPage);

		await expect.element(page.getByText("Couldn't read")).toBeInTheDocument();
		await expect.element(page.getByText('Unsupported encoding')).toBeInTheDocument();
	});

	it('uploads then ingests a picked file (upload and ingest are two steps)', async () => {
		seed([]);
		vi.mocked(files.upload).mockResolvedValueOnce({
			file_id: 'file-new',
			content_hash: 'hash',
			filename: 'notes.md',
			mime_type: 'text/markdown',
			size_bytes: 10
		});
		vi.mocked(knowledgeBases.ingestDocument).mockResolvedValueOnce(ingestedDoc);

		const { container } = await render(KnowledgeBaseDetailPage);
		await expect.element(page.getByText('Drop markdown, text, or JSON')).toBeInTheDocument();

		const input = container.querySelector('input[type="file"]') as HTMLInputElement;
		await page
			.elementLocator(input)
			.upload(new File(['# notes'], 'notes.md', { type: 'text/markdown' }));

		await vi.waitFor(() =>
			expect(knowledgeBases.ingestDocument).toHaveBeenCalledWith('kb-1', 'file-new')
		);
		expect(files.upload).toHaveBeenCalledTimes(1);
	});

	it('removes a document', async () => {
		seed();
		vi.mocked(knowledgeBases.removeDocument).mockResolvedValueOnce(undefined);

		render(KnowledgeBaseDetailPage);
		await page.getByRole('button', { name: 'Remove' }).click();

		expect(knowledgeBases.removeDocument).toHaveBeenCalledWith('kb-1', 'doc-1');
	});

	it('shows a quiet error with retry when the base fails to load', async () => {
		vi.mocked(knowledgeBases.get).mockRejectedValue(new Error('boom'));
		vi.mocked(knowledgeBases.listDocuments).mockResolvedValue([]);
		vi.mocked(agents.list).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);

		render(KnowledgeBaseDetailPage);

		await expect.element(page.getByText("Couldn't load this knowledge base.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});
});
