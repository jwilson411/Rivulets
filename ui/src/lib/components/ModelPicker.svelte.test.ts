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

	it('offers an enabled Auto option', async () => {
		render(ModelPicker, { providers: [anthropicProvider], value: '' });

		await expect
			.element(page.getByRole('option', { name: 'Auto — picks cheap or capable per message' }))
			.not.toBeDisabled();
	});

	it('selecting Auto keeps it selected and hides the custom-model input', async () => {
		render(ModelPicker, { providers: [anthropicProvider], value: '' });

		const select = page.getByRole('combobox');
		await select.selectOptions('__auto__');

		expect((select.element() as HTMLSelectElement).value).toBe('__auto__');
		await expect
			.element(page.getByPlaceholder('model_name (e.g. claude-3-5-haiku-latest)'))
			.not.toBeInTheDocument();
	});

	it('shows Auto as selected when value is already the auto sentinel', async () => {
		render(ModelPicker, { providers: [anthropicProvider], value: 'auto' });

		const select = page.getByRole('combobox').element() as HTMLSelectElement;
		expect(select.value).toBe('__auto__');
	});

	it('reveals a free-text input when Custom… is chosen, since openai_compatible has no catalog', async () => {
		render(ModelPicker, { providers: [openaiCompatibleProvider], value: '' });

		await page.getByRole('combobox').selectOptions('openai_compatible:__custom__');

		await expect
			.element(page.getByPlaceholder('model_name (e.g. claude-3-5-haiku-latest)'))
			.toBeInTheDocument();
	});

	it('hides the Auto option when showAuto is false', async () => {
		render(ModelPicker, { providers: [anthropicProvider], value: '', showAuto: false });

		await expect
			.element(page.getByRole('option', { name: 'Auto — picks cheap or capable per message' }))
			.not.toBeInTheDocument();
	});

	it('shows a hint instead of options when no provider is configured', async () => {
		render(ModelPicker, { providers: [], value: '' });

		await expect
			.element(page.getByText('No providers configured yet — add one under Providers first.'))
			.toBeInTheDocument();
	});
});
