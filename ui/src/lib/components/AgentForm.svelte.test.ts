// Browser-mode component test (see LoginForm.svelte.test.ts) -- AgentForm
// only depends on its own props (and ModelPicker, exercised for real here
// rather than mocked, since composing "provider:model_name" correctly is
// the behavior worth covering).

import { page } from 'vitest/browser';
import { describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import AgentForm from './AgentForm.svelte';
import type { Provider } from '$lib/api/providers';

const anthropicProvider: Provider = {
	id: 'prov-1',
	provider: 'anthropic',
	label: 'My Anthropic key',
	base_url: null,
	is_default: true
};

describe('AgentForm.svelte', () => {
	it('submits trimmed values with the model composed from the picker', async () => {
		const onsubmit = vi.fn();
		render(AgentForm, {
			providers: [anthropicProvider],
			submitLabel: 'Create agent',
			busyLabel: 'Creating…',
			onsubmit
		});

		await page.getByPlaceholder('Name').fill('  Researcher  ');
		await page
			.getByPlaceholder('Description (used by the dispatcher for routing)')
			.fill('  Looks things up  ');
		await page.getByPlaceholder('Instructions (system prompt)').fill('  Be thorough  ');
		await page.getByRole('combobox').selectOptions('anthropic:claude-haiku-4-5-20251001');
		await page.getByRole('button', { name: 'Create agent' }).click();

		expect(onsubmit).toHaveBeenCalledWith({
			name: 'Researcher',
			description: 'Looks things up',
			instructions: 'Be thorough',
			model: 'anthropic:claude-haiku-4-5-20251001',
			fallback_models: [],
			output_schema: null
		});
	});

	it('does not submit while any field, including the model, is empty', async () => {
		const onsubmit = vi.fn();
		render(AgentForm, {
			providers: [anthropicProvider],
			submitLabel: 'Create agent',
			busyLabel: 'Creating…',
			onsubmit
		});

		await page.getByPlaceholder('Name').fill('Researcher');
		await page
			.getByPlaceholder('Description (used by the dispatcher for routing)')
			.fill('Looks things up');
		await page.getByPlaceholder('Instructions (system prompt)').fill('Be thorough');
		await page.getByRole('button', { name: 'Create agent' }).click();

		expect(onsubmit).not.toHaveBeenCalled();
	});

	it('seeds fields from `initial` for editing and calls oncancel', async () => {
		const oncancel = vi.fn();
		render(AgentForm, {
			providers: [anthropicProvider],
			initial: {
				name: 'Researcher',
				description: 'Looks things up',
				instructions: 'Be thorough',
				model: 'anthropic:claude-haiku-4-5-20251001',
				fallback_models: [],
				output_schema: null
			},
			submitLabel: 'Save changes',
			busyLabel: 'Saving…',
			oncancel,
			onsubmit: vi.fn()
		});

		await expect.element(page.getByPlaceholder('Name')).toHaveValue('Researcher');
		await expect
			.element(page.getByRole('combobox'))
			.toHaveValue('anthropic:claude-haiku-4-5-20251001');

		await page.getByRole('button', { name: 'Cancel' }).click();
		expect(oncancel).toHaveBeenCalled();
	});

	it('adds and removes fallback models, submitting them in order (#103)', async () => {
		const onsubmit = vi.fn();
		render(AgentForm, {
			providers: [anthropicProvider],
			submitLabel: 'Create agent',
			busyLabel: 'Creating…',
			onsubmit
		});

		await page.getByPlaceholder('Name').fill('Researcher');
		await page
			.getByPlaceholder('Description (used by the dispatcher for routing)')
			.fill('Looks things up');
		await page.getByPlaceholder('Instructions (system prompt)').fill('Be thorough');
		await page.getByRole('combobox').first().selectOptions('anthropic:claude-haiku-4-5-20251001');

		await page.getByRole('button', { name: '+ Add fallback' }).click();
		await page.getByRole('button', { name: '+ Add fallback' }).click();
		const comboboxes = page.getByRole('combobox');
		await comboboxes.nth(1).selectOptions('anthropic:claude-3-5-haiku-latest');
		await comboboxes.nth(2).selectOptions('anthropic:claude-opus-4-1');

		await page.getByRole('button', { name: 'Remove' }).first().click();

		await page.getByRole('button', { name: 'Create agent' }).click();

		expect(onsubmit).toHaveBeenCalledWith({
			name: 'Researcher',
			description: 'Looks things up',
			instructions: 'Be thorough',
			model: 'anthropic:claude-haiku-4-5-20251001',
			fallback_models: ['anthropic:claude-opus-4-1'],
			output_schema: null
		});
	});

	it('shows the passed error message', async () => {
		render(AgentForm, {
			providers: [anthropicProvider],
			submitLabel: 'Create agent',
			busyLabel: 'Creating…',
			error: 'Failed to create agent',
			onsubmit: vi.fn()
		});

		await expect.element(page.getByText('Failed to create agent')).toBeInTheDocument();
	});

	it('submits a parsed output schema entered in the Advanced section (#107)', async () => {
		const onsubmit = vi.fn();
		render(AgentForm, {
			providers: [anthropicProvider],
			submitLabel: 'Create agent',
			busyLabel: 'Creating…',
			onsubmit
		});

		await page.getByPlaceholder('Name').fill('Extractor');
		await page.getByPlaceholder('Description').fill('Extracts fields');
		await page.getByPlaceholder('Instructions').fill('Extract the fields');
		await page.getByRole('combobox').selectOptions('anthropic:claude-haiku-4-5-20251001');
		await page.getByText('Advanced: structured output schema').click();
		await page.getByPlaceholder('Output schema (JSON)').fill('{"type": "object"}');
		await page.getByRole('button', { name: 'Create agent' }).click();

		expect(onsubmit).toHaveBeenCalledWith({
			name: 'Extractor',
			description: 'Extracts fields',
			instructions: 'Extract the fields',
			model: 'anthropic:claude-haiku-4-5-20251001',
			fallback_models: [],
			output_schema: { type: 'object' }
		});
	});

	it('rejects invalid JSON in the output schema field instead of submitting (#107)', async () => {
		const onsubmit = vi.fn();
		render(AgentForm, {
			providers: [anthropicProvider],
			submitLabel: 'Create agent',
			busyLabel: 'Creating…',
			onsubmit
		});

		await page.getByPlaceholder('Name').fill('Extractor');
		await page.getByPlaceholder('Description').fill('Extracts fields');
		await page.getByPlaceholder('Instructions').fill('Extract the fields');
		await page.getByRole('combobox').selectOptions('anthropic:claude-haiku-4-5-20251001');
		await page.getByText('Advanced: structured output schema').click();
		await page.getByPlaceholder('Output schema (JSON)').fill('{not json');
		await page.getByRole('button', { name: 'Create agent' }).click();

		expect(onsubmit).not.toHaveBeenCalled();
		await expect.element(page.getByText('Output schema must be valid JSON')).toBeInTheDocument();
	});
});
