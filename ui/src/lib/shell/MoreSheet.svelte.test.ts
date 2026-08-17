// Browser-mode component test for the More sheet. #417: phone chrome hides
// the rail avatar, so Account (theme / switch name / sign out) must live
// here rather than only on md+.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import MoreSheet from './MoreSheet.svelte';
import { auth } from '$lib/api/auth.svelte';
import { agents } from '$lib/api/agents';
import { teams } from '$lib/api/teams';
import { mcpServers } from '$lib/api/mcpServers';
import { theme } from '$lib/theme.svelte';

const authState = vi.hoisted(() => ({
	displayName: 'Riley' as string | null,
	grant: 'owner' as string | null
}));

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/paths', () => ({ resolve: (path: string) => path }));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get displayName() {
			return authState.displayName;
		},
		get grant() {
			return authState.grant;
		},
		logout: vi.fn(),
		clearIdentity: vi.fn()
	}
}));

vi.mock('$lib/api/agents', () => ({ agents: { list: vi.fn() } }));
vi.mock('$lib/api/teams', () => ({ teams: { list: vi.fn() } }));
vi.mock('$lib/api/mcpServers', () => ({ mcpServers: { list: vi.fn() } }));

afterEach(() => {
	vi.clearAllMocks();
	authState.displayName = 'Riley';
	authState.grant = 'owner';
});

function seedLists() {
	vi.mocked(agents.list).mockResolvedValue([]);
	vi.mocked(teams.list).mockResolvedValue([]);
	vi.mocked(mcpServers.list).mockResolvedValue([]);
}

describe('MoreSheet.svelte', () => {
	it('puts theme, switch-name, and sign out on Account so phone chrome can reach them (#417)', async () => {
		seedLists();
		const onClose = vi.fn();
		render(MoreSheet, { onClose, onOpenPalette: vi.fn() });

		await expect.element(page.getByText('Account')).toBeInTheDocument();
		await expect.element(page.getByText('Riley')).toBeInTheDocument();
		await expect.element(page.getByText('Owner')).toBeInTheDocument();
		await expect.element(page.getByRole('group', { name: 'Theme' })).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Light' })).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Dark' })).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'System' })).toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'Use a different name' }))
			.toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Sign out' })).toBeInTheDocument();
	});

	it('hides switch-name for guests and still offers sign out and theme', async () => {
		seedLists();
		authState.grant = 'guest';
		render(MoreSheet, { onClose: vi.fn(), onOpenPalette: vi.fn() });

		await expect.element(page.getByText('Guest')).toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'Use a different name' }))
			.not.toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Sign out' })).toBeInTheDocument();
		await expect.element(page.getByRole('group', { name: 'Theme' })).toBeInTheDocument();
	});

	it('signs out and closes the sheet', async () => {
		seedLists();
		const onClose = vi.fn();
		render(MoreSheet, { onClose, onOpenPalette: vi.fn() });

		await page.getByRole('button', { name: 'Sign out' }).click();

		expect(onClose).toHaveBeenCalledOnce();
		expect(auth.logout).toHaveBeenCalledOnce();
	});

	it('clears identity and closes when the owner switches name', async () => {
		seedLists();
		const onClose = vi.fn();
		render(MoreSheet, { onClose, onOpenPalette: vi.fn() });

		await page.getByRole('button', { name: 'Use a different name' }).click();

		expect(onClose).toHaveBeenCalledOnce();
		expect(auth.clearIdentity).toHaveBeenCalledOnce();
	});

	it('applies a theme choice from the Account section', async () => {
		seedLists();
		render(MoreSheet, { onClose: vi.fn(), onOpenPalette: vi.fn() });

		await page.getByRole('button', { name: 'Dark' }).click();

		expect(theme.preference).toBe('dark');
		await expect
			.element(page.getByRole('button', { name: 'Dark' }))
			.toHaveAttribute('aria-pressed', 'true');
	});
});
