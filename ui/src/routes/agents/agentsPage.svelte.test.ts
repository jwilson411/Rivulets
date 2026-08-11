// Browser-mode component test (see LoginForm.svelte.test.ts). This route
// only depends on $lib/api/agents and $lib/api/providers, not on any
// SvelteKit routing modules, so nothing else needs mocking.

import { page } from 'vitest/browser';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import AgentsPage from './+page.svelte';
import { agents, type Agent } from '$lib/api/agents';
import { providers } from '$lib/api/providers';
import { teams, type TeamDetail } from '$lib/api/teams';

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
		rollback: vi.fn()
	}
}));

vi.mock('$lib/api/providers', () => ({
	providers: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() }
}));

vi.mock('$lib/api/teams', () => ({
	teams: { list: vi.fn(), get: vi.fn() }
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

const writer: Agent = {
	id: 'agent-2',
	name: 'Writer',
	description: 'Drafts copy',
	instructions: 'Be concise',
	model: 'anthropic:claude-haiku-4-5-20251001',
	fallback_models: [],
	approved_for_unattended_tools: false,
	agentos_agent_id: 'agentos-2'
};

const anthropicProvider = {
	id: 'prov-1',
	provider: 'anthropic' as const,
	label: 'My Anthropic key',
	base_url: null,
	is_default: true
};

const editorial: TeamDetail = {
	id: 'team-1',
	name: 'Editorial',
	description: null,
	agent_ids: ['agent-2']
};

afterEach(() => {
	vi.clearAllMocks();
});

beforeEach(() => {
	vi.mocked(agents.getPeerPreference).mockResolvedValue({ capability_tag: null });
	// Most tests don't care about version history -- default to none so
	// they don't all need to stub it just to get past refresh().
	vi.mocked(agents.listVersions).mockResolvedValue([]);
	// Most tests don't care about team membership -- default to no teams so
	// the "on a team" filter has nothing to match unless a test opts in.
	vi.mocked(teams.list).mockResolvedValue([]);
	vi.mocked(teams.get).mockResolvedValue(editorial);
});

describe('agents/+page.svelte', () => {
	it('switches an agent card into an edit form seeded with its current values', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);

		render(AgentsPage);

		await expect.element(page.getByText('Researcher')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Edit' }).click();

		// Two "Name" inputs exist while editing: the New agent form and this
		// card's edit form -- the edit one (second in DOM order) is seeded.
		await expect.element(page.getByPlaceholder('Name').nth(1)).toHaveValue('Researcher');
		await expect
			.element(page.getByRole('combobox').last())
			.toHaveValue('anthropic:claude-haiku-4-5-20251001');
	});

	it('saves an edit via agents.update and exits edit mode', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);
		vi.mocked(agents.update).mockResolvedValueOnce({ ...researcher, name: 'Deep Researcher' });

		render(AgentsPage);

		await expect.element(page.getByText('Researcher')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Edit' }).click();
		await page.getByPlaceholder('Name').nth(1).fill('Deep Researcher');
		await page.getByRole('button', { name: 'Save changes' }).click();

		expect(agents.update).toHaveBeenCalledWith('agent-1', {
			name: 'Deep Researcher',
			description: 'Looks things up',
			instructions: 'Be thorough',
			model: 'anthropic:claude-haiku-4-5-20251001',
			fallback_models: []
		});
		await expect
			.element(page.getByRole('button', { name: 'Save changes' }))
			.not.toBeInTheDocument();
	});

	it('discards changes when Cancel is clicked, without calling agents.update', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);

		render(AgentsPage);

		await expect.element(page.getByText('Researcher')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Edit' }).click();
		await page.getByRole('button', { name: 'Cancel' }).click();

		expect(agents.update).not.toHaveBeenCalled();
		await expect.element(page.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
	});

	it('shows an error when agents fail to load', async () => {
		vi.mocked(agents.list).mockRejectedValueOnce(new Error('Failed to load agents'));
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);

		render(AgentsPage);

		await expect.element(page.getByText('Failed to load agents')).toBeInTheDocument();
	});

	it('creates a new agent via the New agent form and refreshes the list', async () => {
		vi.mocked(agents.list).mockResolvedValueOnce([]).mockResolvedValueOnce([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);
		vi.mocked(agents.create).mockResolvedValueOnce(researcher);

		render(AgentsPage);
		await expect.element(page.getByText('New agent')).toBeInTheDocument();

		await page.getByPlaceholder('Name').fill('Researcher');
		await page.getByPlaceholder('Description').fill('Looks things up');
		await page.getByPlaceholder('Instructions').fill('Be thorough');
		await page.getByRole('combobox').selectOptions('anthropic:claude-haiku-4-5-20251001');
		await page.getByRole('button', { name: 'Create agent' }).click();

		expect(agents.create).toHaveBeenCalledWith({
			name: 'Researcher',
			description: 'Looks things up',
			instructions: 'Be thorough',
			model: 'anthropic:claude-haiku-4-5-20251001',
			fallback_models: []
		});
		await expect
			.element(page.getByRole('heading', { level: 1, name: 'Agents' }))
			.toBeInTheDocument();
		await expect.element(page.getByText('Researcher', { exact: false })).toBeInTheDocument();
	});

	it('shows an error when creating an agent fails', async () => {
		vi.mocked(agents.list).mockResolvedValue([]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.create).mockRejectedValueOnce(new Error('Failed to create agent'));

		render(AgentsPage);
		await expect.element(page.getByText('New agent')).toBeInTheDocument();

		await page.getByPlaceholder('Name').fill('Researcher');
		await page.getByPlaceholder('Description').fill('Looks things up');
		await page.getByPlaceholder('Instructions').fill('Be thorough');
		await page.getByRole('combobox').selectOptions('anthropic:claude-haiku-4-5-20251001');
		await page.getByRole('button', { name: 'Create agent' }).click();

		await expect.element(page.getByText('Failed to create agent')).toBeInTheDocument();
	});

	it('shows an error when saving an edit fails', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);
		vi.mocked(agents.update).mockRejectedValueOnce(new Error('Failed to update agent'));

		render(AgentsPage);
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Edit' }).click();
		await page.getByRole('button', { name: 'Save changes' }).click();

		await expect.element(page.getByText('Failed to update agent')).toBeInTheDocument();
		// Stays in edit mode on failure.
		await expect.element(page.getByRole('button', { name: 'Save changes' })).toBeInTheDocument();
	});

	it('sets an always-respond routing rule and reflects it in the summary', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules)
			.mockResolvedValueOnce([])
			.mockResolvedValueOnce([{ id: 'r1', rule_type: 'always', pattern: '', priority: 10 }]);
		vi.mocked(agents.setRoutingRules).mockResolvedValueOnce([]);

		render(AgentsPage);
		await expect
			.element(page.getByText('No routing rules — only @mention triggers this agent'))
			.toBeInTheDocument();

		await page.getByRole('radio', { name: 'Always respond' }).click();
		await page.getByRole('button', { name: 'Apply routing rule' }).click();

		expect(agents.setRoutingRules).toHaveBeenCalledWith('agent-1', [
			{ rule_type: 'always', pattern: '', priority: 10 }
		]);
		await expect.element(page.getByText('Routing:', { exact: false })).toBeInTheDocument();
		await expect.element(page.getByText('always', { exact: true })).toBeInTheDocument();
	});

	it('sets a mention-only routing rule', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules)
			.mockResolvedValueOnce([{ id: 'r1', rule_type: 'always', pattern: '', priority: 10 }])
			.mockResolvedValueOnce([]);
		vi.mocked(agents.setRoutingRules).mockResolvedValueOnce([]);

		render(AgentsPage);
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();

		await page.getByRole('radio', { name: '@mention only' }).click();
		await page.getByRole('button', { name: 'Apply routing rule' }).click();

		expect(agents.setRoutingRules).toHaveBeenCalledWith('agent-1', [
			{ rule_type: 'mention_only', pattern: '', priority: 10 }
		]);
	});

	it('shows an error when setting a routing rule fails', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);
		vi.mocked(agents.setRoutingRules).mockRejectedValueOnce(
			new Error('Failed to update routing rule')
		);

		render(AgentsPage);
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();

		await page.getByRole('radio', { name: 'Always respond' }).click();
		await page.getByRole('button', { name: 'Apply routing rule' }).click();

		await expect.element(page.getByText('Failed to update routing rule')).toBeInTheDocument();
	});

	it('sets keyword routing rules from the comma-separated draft', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);
		vi.mocked(agents.setRoutingRules).mockResolvedValueOnce([]);

		render(AgentsPage);
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();

		await page.getByRole('radio', { name: 'Keyword match' }).click();
		const keywordInput = page.getByPlaceholder('keyword, keyword, ...');
		await keywordInput.fill('billing, invoices');
		await page.getByRole('button', { name: 'Apply routing rule' }).click();

		expect(agents.setRoutingRules).toHaveBeenCalledWith('agent-1', [
			{ rule_type: 'keyword', pattern: JSON.stringify(['billing', 'invoices']), priority: 10 }
		]);
	});

	it('disables Apply routing rule when keyword type is selected with an empty draft', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);

		render(AgentsPage);
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();

		await page.getByRole('radio', { name: 'Keyword match' }).click();

		await expect
			.element(page.getByRole('button', { name: 'Apply routing rule' }))
			.toBeDisabled();
		expect(agents.setRoutingRules).not.toHaveBeenCalled();
	});

	it('saves a peer preference via agents.setPeerPreference', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);
		vi.mocked(agents.setPeerPreference).mockResolvedValueOnce({ capability_tag: 'gpu' });

		render(AgentsPage);
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();

		await page.getByText('Advanced: peer capability preference').click();
		await page.getByPlaceholder('e.g. gpu (blank = no preference)').fill('gpu');
		await page.getByRole('button', { name: 'Save' }).click();

		expect(agents.setPeerPreference).toHaveBeenCalledWith('agent-1', 'gpu');
	});

	it('saves a blank peer preference as null', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);
		vi.mocked(agents.setPeerPreference).mockResolvedValueOnce({ capability_tag: null });

		render(AgentsPage);
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();

		await page.getByText('Advanced: peer capability preference').click();
		await page.getByRole('button', { name: 'Save' }).click();

		expect(agents.setPeerPreference).toHaveBeenCalledWith('agent-1', null);
	});

	it('shows an error when saving a peer preference fails', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);
		vi.mocked(agents.setPeerPreference).mockRejectedValueOnce(
			new Error('Failed to update peer preference')
		);

		render(AgentsPage);
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();

		await page.getByText('Advanced: peer capability preference').click();
		await page.getByRole('button', { name: 'Save' }).click();

		await expect.element(page.getByText('Failed to update peer preference')).toBeInTheDocument();
	});

	it('deletes an agent via agents.remove and refreshes the list', async () => {
		vi.mocked(agents.list).mockResolvedValueOnce([researcher]).mockResolvedValueOnce([]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);
		vi.mocked(agents.remove).mockResolvedValueOnce(undefined);

		render(AgentsPage);
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Delete' }).click();

		expect(agents.remove).toHaveBeenCalledWith('agent-1');
		await expect.element(page.getByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
	});

	it('shows an error when deleting an agent fails', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);
		vi.mocked(agents.remove).mockRejectedValueOnce(new Error('Failed to delete agent'));

		render(AgentsPage);
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Delete' }).click();

		await expect.element(page.getByText('Failed to delete agent')).toBeInTheDocument();
	});

	it('filters the agent list by name via the search box', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher, writer]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);

		render(AgentsPage);
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();
		await expect.element(page.getByText('Writer')).toBeInTheDocument();

		await page.getByPlaceholder('Search agents…').fill('writ');

		await expect.element(page.getByText('Writer')).toBeInTheDocument();
		await expect.element(page.getByText('Researcher')).not.toBeInTheDocument();
	});

	it('filters the agent list by team membership', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher, writer]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([
			{ id: 'team-1', name: 'Editorial', description: null }
		]);

		render(AgentsPage);
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();
		await expect.element(page.getByText('Writer')).toBeInTheDocument();

		await page.getByRole('combobox', { name: 'Team' }).selectOptions('yes');

		await expect.element(page.getByText('Writer')).toBeInTheDocument();
		await expect.element(page.getByText('Researcher')).not.toBeInTheDocument();

		await page.getByRole('combobox', { name: 'Team' }).selectOptions('no');

		await expect.element(page.getByText('Researcher')).toBeInTheDocument();
		await expect.element(page.getByText('Writer')).not.toBeInTheDocument();
	});
});
