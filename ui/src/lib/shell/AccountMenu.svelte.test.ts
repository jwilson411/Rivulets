import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import AccountMenu from './AccountMenu.svelte';
import { auth } from '$lib/api/auth.svelte';
import { theme } from '$lib/theme.svelte';

const authState = vi.hoisted(() => ({
	displayName: 'Riley' as string | null,
	grant: 'owner' as string | null
}));

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

afterEach(() => {
	vi.clearAllMocks();
	authState.displayName = 'Riley';
	authState.grant = 'owner';
});

describe('AccountMenu.svelte', () => {
	it('shows owner identity, theme, switch-name, and sign out', async () => {
		render(AccountMenu, { onClose: vi.fn() });

		await expect.element(page.getByRole('menu', { name: 'Account' })).toBeInTheDocument();
		await expect.element(page.getByText('Riley')).toBeInTheDocument();
		await expect.element(page.getByText('Owner')).toBeInTheDocument();
		await expect
			.element(page.getByRole('menuitem', { name: 'Use a different name' }))
			.toBeInTheDocument();
		await expect.element(page.getByRole('menuitem', { name: 'Sign out' })).toBeInTheDocument();
	});

	it('hides switch-name for guests', async () => {
		authState.grant = 'invite';
		render(AccountMenu, { onClose: vi.fn() });

		await expect.element(page.getByText('Guest')).toBeInTheDocument();
		await expect
			.element(page.getByRole('menuitem', { name: 'Use a different name' }))
			.not.toBeInTheDocument();
	});

	it('signs out and closes', async () => {
		const onClose = vi.fn();
		render(AccountMenu, { onClose });

		await page.getByRole('menuitem', { name: 'Sign out' }).click();

		expect(onClose).toHaveBeenCalledOnce();
		expect(auth.logout).toHaveBeenCalledOnce();
	});

	it('clears identity for switch-name', async () => {
		const onClose = vi.fn();
		render(AccountMenu, { onClose });

		await page.getByRole('menuitem', { name: 'Use a different name' }).click();

		expect(onClose).toHaveBeenCalledOnce();
		expect(auth.clearIdentity).toHaveBeenCalledOnce();
	});

	it('applies a theme choice', async () => {
		render(AccountMenu, { onClose: vi.fn() });

		await page.getByRole('button', { name: 'Dark' }).click();

		expect(theme.preference).toBe('dark');
	});

	it('closes on Escape', async () => {
		const onClose = vi.fn();
		render(AccountMenu, { onClose });

		await page
			.getByRole('menu', { name: 'Account' })
			.element()
			.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		// Window listener — fire on window.
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

		expect(onClose).toHaveBeenCalled();
	});
});
