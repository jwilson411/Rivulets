// Browser-mode component test for Knowledge bases (06-screens.md →
// Knowledge bases): rows with who each base belongs to, creation in a
// sheet, deletion behind a confirm sheet.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import KnowledgeBasesPage from './+page.svelte';
import { knowledgeBases, type KnowledgeBase } from '$lib/api/knowledgeBases';
import { agents } from '$lib/api/agents';
import { teams } from '$lib/api/teams';

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
	knowledgeBases: { list: vi.fn(), create: vi.fn(), remove: vi.fn() }
}));

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
	document_count: 2
};

function seed(kbs: KnowledgeBase[] = [launchNotes]) {
	vi.mocked(knowledgeBases.list).mockResolvedValue(kbs);
	vi.mocked(agents.list).mockResolvedValue([]);
	vi.mocked(teams.list).mockResolvedValue([
		{ id: 'team-1', name: 'Starter Team', description: null }
	]);
}

describe('knowledge-bases/+page.svelte', () => {
	it('lists bases with who they belong to and their document count', async () => {
		seed();

		render(KnowledgeBasesPage);

		await expect.element(page.getByText('Launch notes')).toBeInTheDocument();
		await expect
			.element(page.getByText('Belongs to Starter Team · 2 documents'))
			.toBeInTheDocument();
	});

	it('creates a team-scoped base from the sheet', async () => {
		seed();
		vi.mocked(knowledgeBases.create).mockResolvedValueOnce(launchNotes);

		render(KnowledgeBasesPage);
		await page.getByRole('button', { name: 'New knowledge base' }).click();

		await page.getByLabelText('Name').fill('Launch notes');
		await page.getByLabelText('Who it belongs to').selectOptions('team-1');
		await page.getByRole('button', { name: 'Create knowledge base' }).click();

		expect(knowledgeBases.create).toHaveBeenCalledWith({
			name: 'Launch notes',
			scope_type: 'team',
			agent_id: undefined,
			team_id: 'team-1'
		});
	});

	it('deletes a base behind a confirm sheet', async () => {
		seed();
		vi.mocked(knowledgeBases.remove).mockResolvedValueOnce(undefined);

		render(KnowledgeBasesPage);
		await page.getByRole('button', { name: 'Delete', exact: true }).click();

		expect(knowledgeBases.remove).not.toHaveBeenCalled();
		await page.getByRole('button', { name: 'Delete knowledge base' }).click();

		expect(knowledgeBases.remove).toHaveBeenCalledWith('kb-1');
	});

	it('shows the empty state when there are no bases', async () => {
		seed([]);

		render(KnowledgeBasesPage);

		await expect.element(page.getByText('No knowledge bases yet.')).toBeInTheDocument();
	});

	it('shows a quiet error with retry when bases fail to load', async () => {
		vi.mocked(knowledgeBases.list).mockRejectedValue(new Error('boom'));
		vi.mocked(agents.list).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);

		render(KnowledgeBasesPage);

		await expect.element(page.getByText("Couldn't load knowledge bases.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});
});
