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

const teamPlaybook: KnowledgeBase = {
	id: 'kb-2',
	name: 'Team Playbook',
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
		vi.mocked(knowledgeBases.list).mockResolvedValue([teamPlaybook]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);

		render(KnowledgeBasesPage);

		await expect.element(page.getByText('Team Playbook')).toBeInTheDocument();
		await expect.element(page.getByText(/Team: Support/)).toBeInTheDocument();
		await expect.element(page.getByText(/1\s+document(?!s)/)).toBeInTheDocument();
	});

	it('shows an error when knowledge bases fail to load', async () => {
		vi.mocked(knowledgeBases.list).mockRejectedValueOnce(new Error('Failed to load'));
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);

		render(KnowledgeBasesPage);

		await expect.element(page.getByText('Failed to load')).toBeInTheDocument();
	});

	it('does not submit when the subject select is left blank', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValue([]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);

		render(KnowledgeBasesPage);
		await page.getByPlaceholder('Knowledge base name').fill('Product Docs');
		await page.getByRole('button', { name: 'Create' }).click();

		expect(knowledgeBases.create).not.toHaveBeenCalled();
	});

	it('shows an error when creating a knowledge base fails', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValue([]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(knowledgeBases.create).mockRejectedValueOnce(new Error('Failed to create'));

		render(KnowledgeBasesPage);
		await page.getByPlaceholder('Knowledge base name').fill('Product Docs');
		await page.getByLabelText('Subject').selectOptions('agent-1');
		await page.getByRole('button', { name: 'Create' }).click();

		await expect.element(page.getByText('Failed to create')).toBeInTheDocument();
	});

	it('creates a team-scoped knowledge base after switching the scope select', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValueOnce([]).mockResolvedValueOnce([teamPlaybook]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(knowledgeBases.create).mockResolvedValueOnce(teamPlaybook);

		render(KnowledgeBasesPage);
		await page.getByPlaceholder('Knowledge base name').fill('Team Playbook');
		await page.getByLabelText('Scope').selectOptions('team');
		await page.getByLabelText('Subject').selectOptions('team-1');
		await page.getByRole('button', { name: 'Create' }).click();

		expect(knowledgeBases.create).toHaveBeenCalledWith({
			name: 'Team Playbook',
			scope_type: 'team',
			agent_id: undefined,
			team_id: 'team-1'
		});
	});

	it('shows an error when deleting a knowledge base fails', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValue([productDocs]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(knowledgeBases.remove).mockRejectedValueOnce(new Error('Failed to delete'));

		render(KnowledgeBasesPage);
		await expect.element(page.getByText('Product Docs')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Delete' }).click();

		await expect.element(page.getByText('Failed to delete')).toBeInTheDocument();
	});

	it('filters knowledge bases by name via the search box', async () => {
		vi.mocked(knowledgeBases.list).mockResolvedValue([productDocs, teamPlaybook]);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);

		render(KnowledgeBasesPage);
		await expect.element(page.getByText('Product Docs')).toBeInTheDocument();

		await page.getByPlaceholder('Search knowledge bases…').fill('Playbook');

		await expect.element(page.getByText('Team Playbook')).toBeInTheDocument();
		await expect.element(page.getByText('Product Docs')).not.toBeInTheDocument();
	});
});
