// Browser-mode component test (see LoginForm.svelte.test.ts) -- ModelPicker
// only depends on its own props, no SvelteKit/api modules to mock.

import { page } from 'vitest/browser';
import { describe, expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ModelPicker from './ModelPicker.svelte';
import type { Provider } from '$lib/api/providers';

const anthropicProvider: Provider = {
	id: 'prov-1',
	provider: 'anthropic',
	label: 'My Anthropic key',
	base_url: null,
	is_default: true
};

const openaiCompatibleProvider: Provider = {
	id: 'prov-2',
	provider: 'openai_compatible',
	label: 'Local vLLM',
	base_url: 'http://localhost:8000',
	is_default: false
};

describe('ModelPicker.svelte', () => {
	it('groups catalog models under each configured provider', async () => {
		render(ModelPicker, { providers: [anthropicProvider], value: '' });

		const select = page.getByRole('combobox');
		await expect
			.element(select.getByRole('option', { name: /Claude Haiku 4\.5/ }))
			.toBeInTheDocument();
		const optgroup = select.element().querySelector('optgroup');
		expect(optgroup?.getAttribute('label')).toBe('My Anthropic key (anthropic)');
	});

	it('shows the Auto slot as disabled', async () => {
		render(ModelPicker, { providers: [anthropicProvider], value: '' });

		await expect.element(page.getByRole('option', { name: 'Auto (coming soon)' })).toBeDisabled();
	});

	it('reveals a free-text input when Custom… is chosen, since openai_compatible has no catalog', async () => {
		render(ModelPicker, { providers: [openaiCompatibleProvider], value: '' });

		await page.getByRole('combobox').selectOptions('openai_compatible:__custom__');

		await expect
			.element(page.getByPlaceholder('model_name (e.g. claude-3-5-haiku-latest)'))
			.toBeInTheDocument();
	});

	it('shows a hint instead of options when no provider is configured', async () => {
		render(ModelPicker, { providers: [], value: '' });

		await expect
			.element(
				page.getByText('No providers configured yet — add one under Providers first.')
			)
			.toBeInTheDocument();
	});
});
