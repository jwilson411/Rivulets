// Browser-mode component test for Providers (06-screens.md → Providers,
// mockup 2k, owner only): large provider cards as a picker (never a select
// of slugs), key entry, Ollama's address-instead-of-key path, and the
// keychain-fallback disclosure (#118).

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ProvidersPage from './+page.svelte';
import { providers, type Provider } from '$lib/api/providers';

const authState = vi.hoisted(() => ({ grant: 'owner' }));

vi.mock('$lib/api/providers', () => ({
	providers: { list: vi.fn(), create: vi.fn(), remove: vi.fn(), credentialStorage: vi.fn() }
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

const anthropic: Provider = {
	id: 'prov-1',
	provider: 'anthropic',
	label: 'Anthropic',
	base_url: null,
	is_default: true
};

function seed(list: Provider[] = []) {
	vi.mocked(providers.list).mockResolvedValue(list);
	vi.mocked(providers.credentialStorage).mockResolvedValue({ backend: 'keychain' });
}

describe('providers/+page.svelte', () => {
	it('renders the owner-only empty state for a guest without firing requests (#351)', async () => {
		authState.grant = 'invite';

		render(ProvidersPage);

		await expect
			.element(page.getByText('This is only available to the workspace owner.'))
			.toBeInTheDocument();
		expect(providers.list).not.toHaveBeenCalled();
	});

	it('shows a configured provider as Connected with its key never displayed', async () => {
		seed([anthropic]);

		render(ProvidersPage);

		await expect.element(page.getByText('Anthropic', { exact: true }).first()).toBeInTheDocument();
		await expect.element(page.getByText('Connected', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('Key stays on this machine')).toBeInTheDocument();
	});

	it('offers the six lead providers as large cards, not a slug select', async () => {
		seed();

		render(ProvidersPage);

		for (const label of [
			'Anthropic',
			'OpenAI',
			'Google AI',
			'xAI',
			'Ollama',
			'OpenAI-compatible'
		]) {
			await expect
				.element(page.getByRole('button', { name: label, exact: true }))
				.toBeInTheDocument();
		}
		expect(document.querySelector('select')).toBeNull();
	});

	it('points Gmail/Calendar/Drive at Settings → Integrations, not this key (#471)', async () => {
		seed();

		render(ProvidersPage);

		await expect
			.element(
				page.getByText(
					'Gmail, Calendar, Drive, Docs, Sheets, Contacts, Tasks, and Meet need a connected Google',
					{
						exact: false
					}
				)
			)
			.toBeInTheDocument();
		await expect
			.element(page.getByRole('link', { name: 'Settings → Integrations' }))
			.toHaveAttribute('href', '/settings?tab=integrations');
	});

	it('saves a key for the picked provider with an auto-derived label', async () => {
		seed();
		vi.mocked(providers.create).mockResolvedValueOnce(anthropic);

		render(ProvidersPage);
		await page.getByLabelText('API key').fill('sk-ant-secret');
		await page.getByRole('button', { name: 'Save key' }).click();

		expect(providers.create).toHaveBeenCalledWith({
			provider: 'anthropic',
			label: 'Anthropic',
			api_key: 'sk-ant-secret',
			base_url: undefined
		});
	});

	it('asks Ollama for a local address instead of a key', async () => {
		seed();
		vi.mocked(providers.create).mockResolvedValueOnce({
			...anthropic,
			provider: 'ollama',
			label: 'Ollama'
		});

		render(ProvidersPage);
		await page.getByRole('button', { name: 'Ollama', exact: true }).click();

		await expect.element(page.getByLabelText('API key')).not.toBeInTheDocument();
		await page.getByLabelText('Local address').fill('http://localhost:11434');
		await page.getByRole('button', { name: 'Save key' }).click();

		expect(providers.create).toHaveBeenCalledWith({
			provider: 'ollama',
			label: 'Ollama',
			api_key: 'ollama',
			base_url: 'http://localhost:11434'
		});
	});

	it('rejects an OpenAI-compatible provider without an address', async () => {
		seed();

		render(ProvidersPage);
		await page.getByRole('button', { name: 'OpenAI-compatible', exact: true }).click();
		await page.getByLabelText('API key').fill('sk-whatever');
		await page.getByRole('button', { name: 'Save key' }).click();

		expect(providers.create).not.toHaveBeenCalled();
		await expect
			.element(page.getByText('An OpenAI-compatible provider needs its address.'))
			.toBeInTheDocument();
	});

	it('shows the rejected-key message when saving fails', async () => {
		seed();
		vi.mocked(providers.create).mockRejectedValueOnce(new Error('401'));

		render(ProvidersPage);
		await page.getByLabelText('API key').fill('sk-bad');
		await page.getByRole('button', { name: 'Save key' }).click();

		await expect
			.element(page.getByText('That key was rejected. Check it and try again.'))
			.toBeInTheDocument();
	});

	it('removes a provider', async () => {
		seed([anthropic]);
		vi.mocked(providers.remove).mockResolvedValueOnce(undefined);

		render(ProvidersPage);
		await page.getByRole('button', { name: 'Remove' }).click();

		expect(providers.remove).toHaveBeenCalledWith('prov-1');
	});

	it('discloses the keychain fallback when keys are phrase-encrypted (#118)', async () => {
		vi.mocked(providers.list).mockResolvedValue([]);
		vi.mocked(providers.credentialStorage).mockResolvedValue({ backend: 'fallback' });

		render(ProvidersPage);

		await expect
			.element(page.getByText('No OS keychain was found on this install', { exact: false }))
			.toBeInTheDocument();
	});
});
