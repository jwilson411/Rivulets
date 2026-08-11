// Browser-mode component test (see agents/agentsPage.svelte.test.ts). This
// route depends on $lib/api/knowledgeBases, $lib/api/agents, $lib/api/teams,
// and $app/paths' resolve() for the per-knowledge-base links.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import KnowledgeBasesPage from './+page.svelte';
import { knowledgeBases, type KnowledgeBase } from '$lib/api/knowledgeBases';
import { agents, type Agent } from '$lib/api/agents';
import { teams, type Team } from '$lib/api/teams';

vi.mock('$lib/api/knowledgeBases', () => ({
	knowledgeBases: {
		list: vi.fn(),
		create: vi.fn(),
		remove: vi.fn()
	}
}));

vi.mock('$lib/api/agents', () => ({
	agents: { list: vi.fn() }
}));

vi.mock('$lib/api/teams', () => ({
	teams: { list: vi.fn() }
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

const researcher: Agent = {
	id: 'agent-1',
	name: 'Researcher',
	description: 'Looks things up',
	instructions: 'Be thorough',
	model: 'anthropic:claude-haiku-4-5-20251001',
	fallback_models: [],
	approved_for_unattended_tools: false,
	agentos_agent_id: 'agentos-1'
};

const supportTeam: Team = { id: 'team-1', name: 'Support', description: null };

const productDocs: KnowledgeBase = {
	id: 'kb-1',
	name: 'Product Docs',
	description: null,
	scope_type: 'agent',
	agent_id: 'agent-1',
	team_id: null,
	document_count: 3
};

const supportPlaybook: KnowledgeBase = {
	id: 'kb-2',
	name: 'Support Playbook',
	description: null,
	scope_type: 'team',
	agent_id: null,
	team_id: 'team-1',
	document_count: 1
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('knowledge-bases/+page.svelte', () => {
	it('renders each knowledge base with its scoped subject and document count', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValue([productDocs]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);

		render(KnowledgeBasesPage);

		await expect.element(page.getByText('Product Docs')).toBeInTheDocument();
		await expect.element(page.getByText(/Agent: Researcher/)).toBeInTheDocument();
		await expect.element(page.getByText(/3\s+documents/)).toBeInTheDocument();
	});

	it('creates an agent-scoped knowledge base via knowledgeBases.create and refreshes', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValueOnce([]).mockResolvedValueOnce([productDocs]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(knowledgeBases.create).mockResolvedValueOnce(productDocs);

		render(KnowledgeBasesPage);

		await page.getByPlaceholder('Knowledge base name').fill('Product Docs');
		await page.getByLabelText('Subject').selectOptions('agent-1');
		await page.getByRole('button', { name: 'Create' }).click();

		expect(knowledgeBases.create).toHaveBeenCalledWith({
			name: 'Product Docs',
			scope_type: 'agent',
			agent_id: 'agent-1',
			team_id: undefined
		});
		await expect.element(page.getByText('Product Docs')).toBeInTheDocument();
	});

	it('deletes a knowledge base via knowledgeBases.remove and refreshes', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValueOnce([productDocs]).mockResolvedValueOnce([]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(knowledgeBases.remove).mockResolvedValueOnce(undefined);

		render(KnowledgeBasesPage);

		await expect.element(page.getByText('Product Docs')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Delete' }).click();

		expect(knowledgeBases.remove).toHaveBeenCalledWith('kb-1');
		await expect
			.element(page.getByText('No knowledge bases yet — create one above.'))
			.toBeInTheDocument();
	});

	it('renders a team-scoped knowledge base with its team name', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValue([supportPlaybook]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);

		render(KnowledgeBasesPage);

		await expect.element(page.getByText('Support Playbook')).toBeInTheDocument();
		await expect.element(page.getByText(/Team: Support/)).toBeInTheDocument();
	});

	it('shows a load error when the initial fetch fails', async () => {
		vi.mocked(knowledgeBases.list).mockRejectedValueOnce(new Error('Failed to load'));
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);

		render(KnowledgeBasesPage);

		await expect.element(page.getByText('Failed to load')).toBeInTheDocument();
		await expect.element(page.getByText('Product Docs')).not.toBeInTheDocument();
	});

	it('does not submit when the name or subject is missing', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValue([]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);

		render(KnowledgeBasesPage);

		await expect
			.element(page.getByText('No knowledge bases yet — create one above.'))
			.toBeInTheDocument();

		// No name and no subject chosen -- the guard in handleCreate should
		// bail before ever calling the API.
		await page.getByRole('button', { name: 'Create' }).click();
		expect(knowledgeBases.create).not.toHaveBeenCalled();

		// A name but still no subject chosen should still be rejected.
		await page.getByPlaceholder('Knowledge base name').fill('Product Docs');
		await page.getByRole('button', { name: 'Create' }).click();
		expect(knowledgeBases.create).not.toHaveBeenCalled();
	});

	it('shows a create error when knowledgeBases.create rejects', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValue([]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(knowledgeBases.create).mockRejectedValueOnce(
			new Error('An OpenAI provider must be configured first')
		);

		render(KnowledgeBasesPage);

		await page.getByPlaceholder('Knowledge base name').fill('Product Docs');
		await page.getByLabelText('Subject').selectOptions('agent-1');
		await page.getByRole('button', { name: 'Create' }).click();

		await expect
			.element(page.getByText('An OpenAI provider must be configured first'))
			.toBeInTheDocument();
	});

	it('shows an action error when deleting a knowledge base fails, leaving the list intact', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValue([productDocs]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(knowledgeBases.remove).mockRejectedValueOnce(
			new Error('Failed to delete knowledge base')
		);

		render(KnowledgeBasesPage);

		await expect.element(page.getByText('Product Docs')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Delete' }).click();

		await expect.element(page.getByText('Failed to delete knowledge base')).toBeInTheDocument();
		await expect.element(page.getByText('Product Docs')).toBeInTheDocument();
	});

	it('resets the chosen subject when the scope is changed', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValue([]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);

		render(KnowledgeBasesPage);

		await page.getByLabelText('Subject').selectOptions('agent-1');
		await expect.element(page.getByLabelText('Subject')).toHaveValue('agent-1');

		await page.getByLabelText('Scope').selectOptions('team');

		await expect.element(page.getByLabelText('Subject')).toHaveValue('');
		await expect.element(page.getByText('Choose a team…')).toBeInTheDocument();
	});

	it('filters the list by name via the search box', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValue([productDocs, supportPlaybook]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);

		render(KnowledgeBasesPage);

		await expect.element(page.getByText('Product Docs')).toBeInTheDocument();
		await expect.element(page.getByText('Support Playbook')).toBeInTheDocument();

		await page.getByPlaceholder('Search knowledge bases…').fill('support');

		await expect.element(page.getByText('Support Playbook')).toBeInTheDocument();
		await expect.element(page.getByText('Product Docs')).not.toBeInTheDocument();
	});
});
