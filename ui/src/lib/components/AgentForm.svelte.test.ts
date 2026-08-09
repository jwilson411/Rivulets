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
			fallback_models: []
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
				fallback_models: []
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
			fallback_models: ['anthropic:claude-opus-4-1']
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
});
