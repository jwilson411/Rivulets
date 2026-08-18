// Browser-mode test for the create-channel sheet (#411): team is picked
// here (Starter Team by default) so the first message can get a reply.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import NewChannelSheet from './NewChannelSheet.svelte';
import { channels, type Channel } from '$lib/api/channels';
import { teams, type Team } from '$lib/api/teams';

vi.mock('$lib/api/channels', () => ({
	channels: { create: vi.fn() }
}));

vi.mock('$lib/api/teams', () => ({
	teams: { list: vi.fn() }
}));

afterEach(() => {
	vi.clearAllMocks();
});

const starter: Team = { id: 'team-starter', name: 'Starter Team', description: null };
const testTeam: Team = { id: 'team-test', name: 'Test Team', description: null };

const created: Channel = {
	id: 'ch-new',
	name: 'My Channel',
	description: null,
	team_id: 'team-starter',
	position: 0,
	archived: false,
	working_directory: null,
	effective_working_directory: null
};

describe('NewChannelSheet.svelte', () => {
	it('defaults the team picker to Starter Team and drops the slug hint', async () => {
		vi.mocked(teams.list).mockResolvedValue([testTeam, starter]);

		render(NewChannelSheet, { onClose: vi.fn(), onCreated: vi.fn() });

		await expect.element(page.getByLabelText('Team')).toBeInTheDocument();
		await expect.element(page.getByLabelText('Team')).toHaveValue('team-starter');
		await expect.element(page.getByPlaceholder('My Channel')).toBeInTheDocument();
		await expect.element(page.getByPlaceholder('launch-readiness')).not.toBeInTheDocument();
		await expect.element(page.getByText('3–80 characters.')).toBeInTheDocument();
	});

	it('keeps Create disabled until the name is 3–80 characters', async () => {
		vi.mocked(teams.list).mockResolvedValue([starter]);

		render(NewChannelSheet, { onClose: vi.fn(), onCreated: vi.fn() });

		await expect.element(page.getByLabelText('Team')).toHaveValue('team-starter');
		await page.getByLabelText('Name').fill('ai');
		await expect.element(page.getByRole('button', { name: 'Create channel' })).toBeDisabled();
		await page.getByLabelText('Name').fill('ops');
		await expect.element(page.getByRole('button', { name: 'Create channel' })).toBeEnabled();
	});

	it('creates the channel with the selected team', async () => {
		vi.mocked(teams.list).mockResolvedValue([starter, testTeam]);
		vi.mocked(channels.create).mockResolvedValue(created);
		const onCreated = vi.fn();

		render(NewChannelSheet, { onClose: vi.fn(), onCreated });

		await expect.element(page.getByLabelText('Team')).toHaveValue('team-starter');
		await page.getByLabelText('Name').fill('My Channel');
		await page.getByRole('button', { name: 'Create channel' }).click();

		await expect.poll(() => vi.mocked(channels.create).mock.calls.length).toBe(1);
		expect(channels.create).toHaveBeenCalledWith('My Channel', undefined, 'team-starter');
		expect(onCreated).toHaveBeenCalledWith(created);
	});

	it('shows the server sentence when the name is already taken', async () => {
		vi.mocked(teams.list).mockResolvedValue([starter]);
		vi.mocked(channels.create).mockRejectedValue(
			new Error("A channel named 'general' already exists")
		);

		render(NewChannelSheet, { onClose: vi.fn(), onCreated: vi.fn() });

		await expect.element(page.getByLabelText('Team')).toHaveValue('team-starter');
		await page.getByLabelText('Name').fill('general');
		await page.getByRole('button', { name: 'Create channel' }).click();

		await expect
			.element(page.getByText("A channel named 'general' already exists"))
			.toBeInTheDocument();
	});

	it('lets the user create with no team', async () => {
		vi.mocked(teams.list).mockResolvedValue([starter]);
		vi.mocked(channels.create).mockResolvedValue({ ...created, team_id: null });
		const onCreated = vi.fn();

		render(NewChannelSheet, { onClose: vi.fn(), onCreated });

		await expect.element(page.getByLabelText('Team')).toHaveValue('team-starter');
		await page.getByLabelText('Team').selectOptions('');
		await page.getByLabelText('Name').fill('Quiet Room');
		await page.getByRole('button', { name: 'Create channel' }).click();

		await expect.poll(() => vi.mocked(channels.create).mock.calls.length).toBe(1);
		expect(channels.create).toHaveBeenCalledWith('Quiet Room', undefined, null);
	});
});
