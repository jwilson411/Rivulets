// Browser-mode component test for Teams (06-screens.md → Teams, mockup
// 2d): cards with member discs and where each team is used; membership
// edits happen in a sheet, saved as a batch.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import TeamsPage from './+page.svelte';
import { teams, type Team, type TeamDetail } from '$lib/api/teams';
import { agents, type Agent } from '$lib/api/agents';
import { channels } from '$lib/api/channels';

const authState = vi.hoisted(() => ({ grant: 'owner' }));

vi.mock('$lib/api/teams', () => ({
	teams: { list: vi.fn(), get: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() }
}));

vi.mock('$lib/api/agents', () => ({
	agents: { list: vi.fn(), getToolScopes: vi.fn() }
}));

vi.mock('$lib/api/channels', () => ({
	channels: { list: vi.fn() }
}));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get grant() {
			return authState.grant;
		}
	}
}));

afterEach(() => {
	vi.clearAllMocks();
	authState.grant = 'owner';
});

const starterTeam: Team = { id: 'team-1', name: 'Starter Team', description: null };
const starterDetail: TeamDetail = { ...starterTeam, agent_ids: ['agent-1'] };

const assistant: Agent = {
	id: 'agent-1',
	name: 'Assistant',
	description: 'Generalist. Hands off to specialists.',
	instructions: 'Be helpful',
	model: 'auto',
	fallback_models: [],
	approved_for_unattended_tools: false,
	agentos_agent_id: 'aos-1'
};

const writer: Agent = {
	...assistant,
	id: 'agent-2',
	name: 'Writer',
	description: 'Drafts and edits prose.'
};

function seed() {
	vi.mocked(teams.list).mockResolvedValue([starterTeam]);
	vi.mocked(teams.get).mockResolvedValue(starterDetail);
	vi.mocked(agents.list).mockResolvedValue([assistant, writer]);
	vi.mocked(channels.list).mockResolvedValue([
		{
			id: 'chan-1',
			name: 'general',
			description: null,
			team_id: 'team-1',
			position: 0,
			archived: false,
			working_directory: null,
			effective_working_directory: null
		}
	]);
}

describe('teams/+page.svelte', () => {
	it('shows team cards with where each team is used', async () => {
		seed();

		render(TeamsPage);

		await expect.element(page.getByText('Starter Team')).toBeInTheDocument();
		await expect.element(page.getByText('Used in #general')).toBeInTheDocument();
	});

	it('creates a team from the New team sheet', async () => {
		seed();
		vi.mocked(teams.create).mockResolvedValueOnce({
			id: 'team-2',
			name: 'Ops',
			description: null
		});

		render(TeamsPage);
		await expect.element(page.getByText('Starter Team')).toBeInTheDocument();

		await page.getByRole('button', { name: 'New team' }).click();
		await page.getByLabelText('Name').fill('Ops');
		await page.getByRole('button', { name: 'Create team' }).click();

		expect(teams.create).toHaveBeenCalledWith('Ops');
	});

	it('opens the member sheet and saves membership as a batch', async () => {
		seed();
		vi.mocked(teams.update).mockResolvedValueOnce({
			...starterDetail,
			agent_ids: ['agent-1', 'agent-2']
		});

		render(TeamsPage);
		await page.getByRole('button', { name: /Starter Team/ }).click();

		await expect.element(page.getByText('Members')).toBeInTheDocument();
		// Writer isn't a member yet — tick it, then Save sends the whole set.
		await page.getByRole('checkbox').nth(1).click();
		await page.getByRole('button', { name: 'Save', exact: true }).click();

		expect(teams.update).toHaveBeenCalledWith('team-1', { agent_ids: ['agent-1', 'agent-2'] });
	});

	it('deletes a team behind a confirm sheet, never window.confirm', async () => {
		seed();
		vi.mocked(teams.remove).mockResolvedValueOnce(undefined);

		render(TeamsPage);
		await page.getByRole('button', { name: /Starter Team/ }).click();
		await page.getByRole('button', { name: 'Delete team' }).click();

		// Nothing deleted yet — the confirm sheet is the gate.
		expect(teams.remove).not.toHaveBeenCalled();
		await page.getByRole('button', { name: 'Delete team' }).click();

		expect(teams.remove).toHaveBeenCalledWith('team-1');
	});

	it('shows a quiet error with retry when teams fail to load', async () => {
		vi.mocked(teams.list).mockRejectedValue(new Error('boom'));
		vi.mocked(agents.list).mockResolvedValue([]);
		vi.mocked(channels.list).mockResolvedValue([]);

		render(TeamsPage);

		await expect.element(page.getByText("Couldn't load teams.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});

	it('gates membership writes for a guest when an agent holds a scope (#393)', async () => {
		authState.grant = 'invite';
		seed();
		vi.mocked(agents.getToolScopes).mockImplementation(async (id: string) => ({
			scopes: id === 'agent-1' ? ['sensitive_tools:manage'] : []
		}));

		render(TeamsPage);
		await page.getByRole('button', { name: /Starter Team/ }).click();

		// The gated member's checkbox is disabled, and the delete affordance
		// is hidden for a team containing a scoped agent.
		await expect.element(page.getByRole('checkbox').first()).toBeDisabled();
		await expect.element(page.getByRole('button', { name: 'Delete team' })).not.toBeInTheDocument();
	});
});
