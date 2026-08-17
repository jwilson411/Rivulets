// Browser-mode component test for Agents (06-screens.md → Agents +
// Agent sheet, mockups 1i/1j): cards, search, and the sheet that owns all
// configuration — the list page never shows routing radios.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import AgentsPage from './+page.svelte';
import { agents, type Agent } from '$lib/api/agents';
import { providers } from '$lib/api/providers';
import { teams } from '$lib/api/teams';
import { tools } from '$lib/api/tools';

vi.mock('$lib/api/agents', () => ({
	agents: {
		list: vi.fn(),
		create: vi.fn(),
		update: vi.fn(),
		remove: vi.fn(),
		getRoutingRules: vi.fn(),
		setRoutingRules: vi.fn(),
		getPeerPreference: vi.fn(),
		setPeerPreference: vi.fn(),
		listVersions: vi.fn(),
		rollback: vi.fn(),
		getToolIds: vi.fn(),
		getToolScopes: vi.fn(),
		setToolScopes: vi.fn()
	}
}));

vi.mock('$lib/api/providers', () => ({ providers: { list: vi.fn() } }));
vi.mock('$lib/api/teams', () => ({ teams: { list: vi.fn(), get: vi.fn(), update: vi.fn() } }));
vi.mock('$lib/api/tools', () => ({ tools: { list: vi.fn(), listScopes: vi.fn() } }));

afterEach(() => {
	vi.clearAllMocks();
});

const assistant: Agent = {
	id: 'agent-1',
	name: 'Assistant',
	description: 'Generalist. Hands off to specialists.',
	instructions: 'You are the default teammate.',
	model: 'auto',
	fallback_models: [],
	output_schema: null,
	approved_for_unattended_tools: false,
	agentos_agent_id: 'aos-1'
};

const writer: Agent = {
	...assistant,
	id: 'agent-2',
	name: 'Writer',
	description: 'Drafts and edits prose.',
	agentos_agent_id: null
};

function seed(list: Agent[] = [assistant, writer]) {
	vi.mocked(agents.list).mockResolvedValue(list);
	vi.mocked(providers.list).mockResolvedValue([]);
	vi.mocked(teams.list).mockResolvedValue([
		{ id: 'team-1', name: 'Starter Team', description: null }
	]);
	vi.mocked(teams.get).mockResolvedValue({
		id: 'team-1',
		name: 'Starter Team',
		description: null,
		agent_ids: ['agent-1']
	});
	vi.mocked(tools.list).mockResolvedValue([]);
	vi.mocked(tools.listScopes).mockResolvedValue([]);
	vi.mocked(agents.getRoutingRules).mockResolvedValue([]);
	vi.mocked(agents.getToolIds).mockResolvedValue({ tool_ids: [] });
	vi.mocked(agents.getToolScopes).mockResolvedValue({ scopes: [] });
	vi.mocked(agents.getPeerPreference).mockResolvedValue({ capability_tag: null });
	vi.mocked(agents.listVersions).mockResolvedValue([]);
	vi.mocked(agents.setRoutingRules).mockResolvedValue([]);
}

describe('agents/+page.svelte', () => {
	it('renders agent cards with model chip, role, and team — no routing radios', async () => {
		seed();

		render(AgentsPage);

		await expect.element(page.getByText('Assistant')).toBeInTheDocument();
		await expect
			.element(page.getByText('Generalist. Hands off to specialists.'))
			.toBeInTheDocument();
		await expect.element(page.getByText('auto').first()).toBeInTheDocument();
		await expect.element(page.getByText('Starter Team')).toBeInTheDocument();
		await expect.element(page.getByRole('radio')).not.toBeInTheDocument();
	});

	it('flags an agent whose provider is unresolved so it reads as silent, not broken', async () => {
		seed();

		render(AgentsPage);

		await expect.element(page.getByText('Needs a provider')).toBeInTheDocument();
	});

	it('names the search box and filters the cards by query', async () => {
		seed();

		render(AgentsPage);
		await expect.element(page.getByText('Writer')).toBeInTheDocument();

		const search = page.getByRole('searchbox', { name: 'Search agents' });
		await expect.element(search).toBeInTheDocument();
		await expect.element(search).toHaveAttribute('placeholder', 'Search agents');
		await search.fill('assis');

		await expect.element(page.getByText('Assistant')).toBeInTheDocument();
		await expect.element(page.getByText('Writer')).not.toBeInTheDocument();
	});

	it('creates an agent from the sheet with the everyday fields', async () => {
		seed([]);
		vi.mocked(agents.create).mockResolvedValueOnce({ ...writer, id: 'agent-3' });

		render(AgentsPage);
		await page.getByRole('button', { name: 'New agent' }).click();

		await page.getByLabelText('Name').fill('Writer');
		await page.getByLabelText('What this agent does').fill('Drafts and edits prose.');
		await page.getByLabelText('How it should behave').fill('Keep the workspace voice.');
		await page.getByLabelText('Team').selectOptions('team-1');
		await page.getByRole('button', { name: 'Create agent' }).click();

		expect(agents.create).toHaveBeenCalledWith({
			name: 'Writer',
			description: 'Drafts and edits prose.',
			instructions: 'Keep the workspace voice.',
			model: 'auto',
			fallback_models: [],
			output_schema: null,
			tool_ids: [],
			team_ids: ['team-1']
		});
		// "When to speak" defaults to only-when-mentioned.
		expect(agents.setRoutingRules).toHaveBeenCalledWith('agent-3', [
			{ rule_type: 'mention_only', pattern: '', priority: 10 }
		]);
	});

	it('opens an agent card into the edit sheet with its extras fetched just-in-time', async () => {
		seed();

		render(AgentsPage);
		await page.getByRole('button', { name: /Assistant/ }).click();

		expect(agents.getRoutingRules).toHaveBeenCalledWith('agent-1');
		expect(agents.getToolIds).toHaveBeenCalledWith('agent-1');
		await expect.element(page.getByLabelText('Name')).toHaveValue('Assistant');
		await expect.element(page.getByRole('button', { name: 'Save' })).toBeInTheDocument();
	});

	it('saves a keyword "when to speak" rule from the sheet', async () => {
		seed();
		vi.mocked(agents.update).mockResolvedValueOnce(assistant);

		render(AgentsPage);
		await page.getByRole('button', { name: /Assistant/ }).click();
		await page.getByText('More options').click();
		await page.getByText('When the message includes…').click();
		await page.getByLabelText('Keywords, separated by commas').fill('retry, eval');
		await page.getByRole('button', { name: 'Save' }).click();

		expect(agents.setRoutingRules).toHaveBeenCalledWith('agent-1', [
			{ rule_type: 'keyword', pattern: JSON.stringify(['retry', 'eval']), priority: 10 }
		]);
	});

	it('deletes an agent behind a confirm sheet with the copy-deck warning', async () => {
		seed();
		vi.mocked(agents.remove).mockResolvedValueOnce(undefined);

		render(AgentsPage);
		await page.getByRole('button', { name: /Assistant/ }).click();
		await page.getByRole('button', { name: 'Delete agent' }).click();

		expect(agents.remove).not.toHaveBeenCalled();
		await expect
			.element(page.getByText('Conversations stay. This agent will stop answering.'))
			.toBeInTheDocument();
		await page.getByRole('button', { name: 'Delete agent' }).click();

		expect(agents.remove).toHaveBeenCalledWith('agent-1');
	});

	it('shows a quiet error with retry when agents fail to load', async () => {
		vi.mocked(agents.list).mockRejectedValue(new Error('boom'));
		vi.mocked(providers.list).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);
		vi.mocked(tools.list).mockResolvedValue([]);
		vi.mocked(tools.listScopes).mockResolvedValue([]);

		render(AgentsPage);

		await expect.element(page.getByText("Couldn't load agents.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});
});
