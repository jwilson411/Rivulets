// Browser-mode component test (see agents/agentsPage.svelte.test.ts). This
// route depends on $lib/api/teams and $lib/api/agents, not on any
// SvelteKit routing modules, so nothing else needs mocking.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import TeamsPage from './+page.svelte';
import { teams, type TeamDetail } from '$lib/api/teams';
import { agents, type Agent } from '$lib/api/agents';

vi.mock('$lib/api/teams', () => ({
	teams: {
		list: vi.fn(),
		get: vi.fn(),
		create: vi.fn(),
		update: vi.fn(),
		remove: vi.fn()
	}
}));

vi.mock('$lib/api/agents', () => ({
	agents: { list: vi.fn() }
}));

const researcher: Agent = {
	id: 'agent-1',
	name: 'Researcher',
	description: 'Looks things up',
	instructions: 'Be thorough',
	model: 'anthropic:claude-haiku-4-5-20251001',
	fallback_models: [],
	agentos_agent_id: 'agentos-1'
};

const supportTeam: TeamDetail = {
	id: 'team-1',
	name: 'Support',
	description: null,
	agent_ids: ['agent-1']
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('teams/+page.svelte', () => {
	it('renders each team with its agent checkboxes checked to match agent_ids', async () => {
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(teams.get).mockResolvedValue(supportTeam);
		vi.mocked(agents.list).mockResolvedValue([researcher]);

		render(TeamsPage);

		await expect.element(page.getByText('Support')).toBeInTheDocument();
		await expect.element(page.getByRole('checkbox', { name: 'Researcher' })).toBeChecked();
	});

	it('creates a team via teams.create and refreshes the list', async () => {
		vi.mocked(teams.list).mockResolvedValueOnce([]).mockResolvedValueOnce([supportTeam]);
		vi.mocked(teams.get).mockResolvedValue(supportTeam);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.create).mockResolvedValueOnce({
			id: 'team-1',
			name: 'Support',
			description: null
		});

		render(TeamsPage);

		await page.getByPlaceholder('Team name').fill('Support');
		await page.getByRole('button', { name: 'Create' }).click();

		expect(teams.create).toHaveBeenCalledWith('Support');
		await expect.element(page.getByText('Support')).toBeInTheDocument();
		await expect.element(page.getByPlaceholder('Team name')).toHaveValue('');
	});

	it('toggling an agent checkbox calls teams.update with the new agent_ids', async () => {
		const noAgentsTeam: TeamDetail = { ...supportTeam, agent_ids: [] };
		vi.mocked(teams.list).mockResolvedValue([noAgentsTeam]);
		vi.mocked(teams.get).mockResolvedValue(noAgentsTeam);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.update).mockResolvedValueOnce({ ...noAgentsTeam, agent_ids: ['agent-1'] });

		render(TeamsPage);

		const checkbox = page.getByRole('checkbox', { name: 'Researcher' });
		await expect.element(checkbox).not.toBeChecked();
		await checkbox.click();

		expect(teams.update).toHaveBeenCalledWith('team-1', { agent_ids: ['agent-1'] });
	});

	it('deletes a team via teams.remove', async () => {
		vi.mocked(teams.list).mockResolvedValueOnce([supportTeam]).mockResolvedValueOnce([]);
		vi.mocked(teams.get).mockResolvedValue(supportTeam);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.remove).mockResolvedValueOnce(undefined);

		render(TeamsPage);
		await expect.element(page.getByText('Support')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Delete' }).click();

		expect(teams.remove).toHaveBeenCalledWith('team-1');
		await expect.element(page.getByText('Support')).not.toBeInTheDocument();
	});

	it('unchecking an agent checkbox calls teams.update with it removed from agent_ids', async () => {
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(teams.get).mockResolvedValue(supportTeam);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.update).mockResolvedValueOnce({ ...supportTeam, agent_ids: [] });

		render(TeamsPage);

		const checkbox = page.getByRole('checkbox', { name: 'Researcher' });
		await expect.element(checkbox).toBeChecked();
		await checkbox.click();

		expect(teams.update).toHaveBeenCalledWith('team-1', { agent_ids: [] });
	});

	it('shows an empty state when a team has no agents to assign', async () => {
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(teams.get).mockResolvedValue(supportTeam);
		vi.mocked(agents.list).mockResolvedValue([]);

		render(TeamsPage);

		await expect.element(page.getByText(/No agents yet/)).toBeInTheDocument();
	});

	it('surfaces a server-rejected create instead of failing silently', async () => {
		vi.mocked(teams.list).mockResolvedValue([]);
		vi.mocked(agents.list).mockResolvedValue([]);
		vi.mocked(teams.create).mockRejectedValueOnce(
			new Error("A team named 'Support' already exists")
		);

		render(TeamsPage);
		await page.getByPlaceholder('Team name').fill('Support');
		await page.getByRole('button', { name: 'Create' }).click();

		await expect
			.element(page.getByText("A team named 'Support' already exists"))
			.toBeInTheDocument();
	});

	it('surfaces a failed agent toggle instead of failing silently', async () => {
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(teams.get).mockResolvedValue(supportTeam);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.update).mockRejectedValueOnce(new Error('Failed to update team'));

		render(TeamsPage);
		await page.getByRole('checkbox', { name: 'Researcher' }).click();

		await expect.element(page.getByText('Failed to update team')).toBeInTheDocument();
	});

	it('surfaces a failed delete instead of failing silently', async () => {
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(teams.get).mockResolvedValue(supportTeam);
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(teams.remove).mockRejectedValueOnce(new Error('Team still assigned to a channel'));

		render(TeamsPage);
		await expect.element(page.getByText('Support')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Delete' }).click();

		await expect.element(page.getByText('Team still assigned to a channel')).toBeInTheDocument();
	});

	it('shows an error when the initial load fails', async () => {
		vi.mocked(teams.list).mockRejectedValueOnce(new Error('Failed to load teams'));
		vi.mocked(agents.list).mockResolvedValue([]);

		render(TeamsPage);

		await expect.element(page.getByText('Failed to load teams')).toBeInTheDocument();
	});

	it('filters the team list by name via the search box', async () => {
		const billingTeam: TeamDetail = {
			id: 'team-2',
			name: 'Billing',
			description: null,
			agent_ids: []
		};
		vi.mocked(teams.list).mockResolvedValue([supportTeam, billingTeam]);
		vi.mocked(teams.get).mockImplementation((id) =>
			Promise.resolve(id === 'team-1' ? supportTeam : billingTeam)
		);
		vi.mocked(agents.list).mockResolvedValue([researcher]);

		render(TeamsPage);
		await expect.element(page.getByText('Support')).toBeInTheDocument();
		await expect.element(page.getByText('Billing')).toBeInTheDocument();

		await page.getByPlaceholder('Search teams…').fill('bill');

		await expect.element(page.getByText('Billing')).toBeInTheDocument();
		await expect.element(page.getByText('Support')).not.toBeInTheDocument();
	});
});
