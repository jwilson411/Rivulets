// Browser-mode component test (see agents/agentsPage.svelte.test.ts). This
// route depends on $lib/api/providers, plus the real (unmocked)
// $lib/api/client for the ApiError class it uses in instanceof checks --
// that module makes no network calls of its own, so there's nothing to stub.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ProvidersPage from './+page.svelte';
import { providers, type Provider } from '$lib/api/providers';
import { ApiError } from '$lib/api/client';

vi.mock('$lib/api/providers', () => ({
	providers: {
		list: vi.fn(),
		create: vi.fn(),
		update: vi.fn(),
		remove: vi.fn()
	}
}));

const anthropicProvider: Provider = {
	id: 'prov-1',
	provider: 'anthropic',
	label: 'My Anthropic key',
	base_url: null,
	is_default: true
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('providers/+page.svelte', () => {
	it('lists configured providers with their kind badge', async () => {
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);

		render(ProvidersPage);

		await expect.element(page.getByText('My Anthropic key')).toBeInTheDocument();
		// "anthropic" also appears as a <select> option; scope to the badge.
		await expect.element(page.getByText('anthropic').last()).toBeInTheDocument();
	});

	it('shows the empty state when no providers are configured', async () => {
		vi.mocked(providers.list).mockResolvedValue([]);

		render(ProvidersPage);

		await expect
			.element(page.getByText('No providers configured yet — add one above.'))
			.toBeInTheDocument();
	});

	it('adds a provider via providers.create and clears the form', async () => {
		vi.mocked(providers.list).mockResolvedValueOnce([]).mockResolvedValueOnce([anthropicProvider]);
		vi.mocked(providers.create).mockResolvedValueOnce(anthropicProvider);

		render(ProvidersPage);
		await expect
			.element(page.getByText('No providers configured yet — add one above.'))
			.toBeInTheDocument();

		await page.getByPlaceholder('Label (e.g. Anthropic)').fill('My Anthropic key');
		await page.getByPlaceholder('API key').fill('sk-test-123');
		await page.getByRole('button', { name: 'Add provider' }).click();

		expect(providers.create).toHaveBeenCalledWith({
			provider: 'anthropic',
			label: 'My Anthropic key',
			api_key: 'sk-test-123',
			base_url: undefined
		});
		await expect.element(page.getByPlaceholder('Label (e.g. Anthropic)')).toHaveValue('');
		await expect.element(page.getByText('My Anthropic key')).toBeInTheDocument();
	});

	it('requires a base URL for openai_compatible and blocks the request otherwise', async () => {
		vi.mocked(providers.list).mockResolvedValue([]);

		render(ProvidersPage);

		await page.getByRole('combobox').selectOptions('openai_compatible');
		await page.getByPlaceholder('Label (e.g. Anthropic)').fill('Local model');
		await page.getByPlaceholder('API key').fill('sk-test-123');
		await page.getByRole('button', { name: 'Add provider' }).click();

		await expect
			.element(page.getByText('openai_compatible requires a base URL'))
			.toBeInTheDocument();
		expect(providers.create).not.toHaveBeenCalled();
	});

	it('requires a base URL for ollama and blocks the request otherwise', async () => {
		vi.mocked(providers.list).mockResolvedValue([]);

		render(ProvidersPage);

		await page.getByRole('combobox').selectOptions('ollama');
		await page.getByPlaceholder('Label (e.g. Anthropic)').fill('Local Ollama');
		await page.getByRole('button', { name: 'Add provider' }).click();

		await expect
			.element(page.getByText('ollama requires a base URL (its local host address)'))
			.toBeInTheDocument();
		expect(providers.create).not.toHaveBeenCalled();
	});

	it('adds an ollama provider with no API key', async () => {
		const ollamaProvider: Provider = {
			id: 'prov-2',
			provider: 'ollama',
			label: 'Local Ollama',
			base_url: 'http://localhost:11434',
			is_default: false
		};
		vi.mocked(providers.list).mockResolvedValueOnce([]).mockResolvedValueOnce([ollamaProvider]);
		vi.mocked(providers.create).mockResolvedValueOnce(ollamaProvider);

		render(ProvidersPage);
		await expect
			.element(page.getByText('No providers configured yet — add one above.'))
			.toBeInTheDocument();

		await page.getByRole('combobox').selectOptions('ollama');
		await page.getByPlaceholder('Label (e.g. Anthropic)').fill('Local Ollama');
		await page.getByPlaceholder('Base URL (required)').fill('http://localhost:11434');
		await page.getByRole('button', { name: 'Add provider' }).click();

		expect(providers.create).toHaveBeenCalledWith({
			provider: 'ollama',
			label: 'Local Ollama',
			api_key: '',
			base_url: 'http://localhost:11434'
		});
		await expect.element(page.getByText('Local Ollama')).toBeInTheDocument();
	});

	it('shows the ApiError message when removing a provider fails', async () => {
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(providers.remove).mockRejectedValueOnce(
			new ApiError(409, 'cannot remove the default provider')
		);

		render(ProvidersPage);
		await expect.element(page.getByText('My Anthropic key')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Remove' }).click();

		await expect.element(page.getByText('cannot remove the default provider')).toBeInTheDocument();
	});
});
