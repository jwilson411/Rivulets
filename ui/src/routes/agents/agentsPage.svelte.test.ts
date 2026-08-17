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

const starterTeam = {
	id: 'team-1',
	name: 'Starter Team',
	description: null,
	agent_ids: ['agent-1']
};

const testTeam = {
	id: 'team-2',
	name: 'Test Team',
	description: null,
	agent_ids: ['agent-1']
};

function seed(
	list: Agent[] = [assistant, writer],
	teamDetails: (typeof starterTeam)[] = [starterTeam]
) {
	vi.mocked(agents.list).mockResolvedValue(list);
	vi.mocked(providers.list).mockResolvedValue([]);
	vi.mocked(teams.list).mockResolvedValue(
		teamDetails.map(({ id, name, description }) => ({ id, name, description }))
	);
	vi.mocked(teams.get).mockImplementation(async (id: string) => {
		const team = teamDetails.find((item) => item.id === id);
		if (!team) throw new Error(`unknown team ${id}`);
		return team;
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
		await page.getByRole('checkbox', { name: 'Starter Team' }).click();
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

	it('groups agent tools with display names and descriptions (#422)', async () => {
		seed();
		vi.mocked(tools.list).mockResolvedValue([
			{
				id: 'tool-search',
				name: 'web_search',
				description: 'Search the web via Brave Search and return titles, URLs, and snippets.',
				tool_type: 'builtin',
				source_path: null,
				sensitive: false,
				required_scope: null,
				available: true,
				display_name: 'Web search',
				group: 'chat'
			},
			{
				id: 'tool-http',
				name: 'http_request',
				description: 'Make an HTTP request and return the response status and truncated body.',
				tool_type: 'builtin',
				source_path: null,
				sensitive: true,
				required_scope: 'sensitive_tools:manage',
				available: true,
				display_name: 'HTTP request',
				group: 'chat'
			},
			{
				id: 'tool-py',
				name: 'execute_python',
				description: 'Execute Python code in a sandboxed environment.',
				tool_type: 'builtin',
				source_path: null,
				sensitive: true,
				required_scope: 'sensitive_tools:manage',
				available: true,
				display_name: 'Execute Python',
				group: 'files'
			},
			{
				id: 'tool-pref',
				name: 'update_agent_peer_preference',
				description: 'Set which machine this agent prefers to run on.',
				tool_type: 'builtin',
				source_path: null,
				sensitive: false,
				required_scope: 'agents_teams:manage',
				available: true,
				display_name: 'Update agent peer preference',
				group: 'workspace_admin'
			}
		]);

		render(AgentsPage);
		await page.getByRole('button', { name: /Assistant/ }).click();
		await page.getByText('More options').click();

		await expect.element(page.getByText('No tools until you pick some.')).toBeInTheDocument();
		await expect.element(page.getByText('Chat', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('Files', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('Workspace admin', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByRole('checkbox', { name: /Web search/ })).toBeInTheDocument();
		await expect.element(page.getByRole('checkbox', { name: /HTTP request/ })).toBeInTheDocument();
		await expect
			.element(
				page.getByText('Search the web via Brave Search and return titles, URLs, and snippets.')
			)
			.toBeInTheDocument();
		await expect.element(page.getByText('web_search', { exact: true })).not.toBeInTheDocument();
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

	it('keeps every team membership when the sheet saves (#409)', async () => {
		seed([assistant], [starterTeam, testTeam]);
		vi.mocked(agents.update).mockResolvedValueOnce(assistant);

		render(AgentsPage);
		await page.getByRole('button', { name: /Assistant/ }).click();

		await expect.element(page.getByRole('checkbox', { name: 'Starter Team' })).toBeChecked();
		await expect.element(page.getByRole('checkbox', { name: 'Test Team' })).toBeChecked();
		await page.getByRole('button', { name: 'Save' }).click();

		expect(agents.update).toHaveBeenCalledWith(
			'agent-1',
			expect.objectContaining({ team_ids: ['team-1', 'team-2'] })
		);
		expect(agents.setRoutingRules).not.toHaveBeenCalled();
	});

	it('shows generated When to speak rules and does not wipe them on Save (#409)', async () => {
		seed();
		vi.mocked(agents.getRoutingRules).mockResolvedValue([
			{
				id: 'r1',
				rule_type: 'keyword',
				pattern: JSON.stringify(['specialist', 'expert', 'coder', 'researcher', 'writer']),
				priority: 5
			},
			{ id: 'r2', rule_type: 'semantic', pattern: JSON.stringify(['help']), priority: 1 }
		]);
		vi.mocked(agents.update).mockResolvedValueOnce(assistant);

		render(AgentsPage);
		await page.getByRole('button', { name: /Assistant/ }).click();

		await expect.element(page.getByText('Keep the current rules')).toBeInTheDocument();
		await expect
			.element(
				page.getByText('When the message includes specialist, expert, coder, researcher, writer')
			)
			.toBeInTheDocument();
		await expect.element(page.getByText('When the message is about help')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Save' }).click();

		expect(agents.update).toHaveBeenCalled();
		expect(agents.setRoutingRules).not.toHaveBeenCalled();
	});

	it('asks before replacing a generated When to speak set (#409)', async () => {
		seed();
		vi.mocked(agents.getRoutingRules).mockResolvedValue([
			{ id: 'r1', rule_type: 'keyword', pattern: JSON.stringify(['specialist']), priority: 5 },
			{ id: 'r2', rule_type: 'semantic', pattern: JSON.stringify(['help']), priority: 1 }
		]);
		vi.mocked(agents.update).mockResolvedValueOnce(assistant);

		render(AgentsPage);
		await page.getByRole('button', { name: /Assistant/ }).click();
		await page.getByText('Always').click();
		await page.getByRole('button', { name: 'Save' }).click();

		expect(agents.update).not.toHaveBeenCalled();
		await expect.element(page.getByText('Replace When to speak?')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Replace rules' }).click();

		expect(agents.setRoutingRules).toHaveBeenCalledWith('agent-1', [
			{ rule_type: 'always', pattern: '', priority: 10 }
		]);
	});

	it('surfaces a leftover generated regex with the keyword list (#410)', async () => {
		seed();
		vi.mocked(agents.getRoutingRules).mockResolvedValue([
			{
				id: 'rule-regex',
				rule_type: 'regex',
				pattern: '(?i)(\\d{5}-\\d{4}|[a-zA-Z]{2,})',
				priority: 8
			},
			{
				id: 'rule-kw',
				rule_type: 'keyword',
				pattern: JSON.stringify(['draft', 'rewrite', 'prose']),
				priority: 5
			}
		]);

		render(AgentsPage);
		await page.getByRole('button', { name: /Writer/ }).click();

		await expect.element(page.getByText('Keep the current rules')).toBeInTheDocument();
		await expect
			.element(page.getByText('When the message includes draft, rewrite, prose'))
			.toBeInTheDocument();
		await expect
			.element(page.getByText('When the message matches (?i)(\\d{5}-\\d{4}|[a-zA-Z]{2,})'))
			.toBeInTheDocument();
	});

	it('saves a keyword "when to speak" rule from the sheet', async () => {
		seed();
		vi.mocked(agents.update).mockResolvedValueOnce(assistant);

		render(AgentsPage);
		await page.getByRole('button', { name: /Assistant/ }).click();
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
