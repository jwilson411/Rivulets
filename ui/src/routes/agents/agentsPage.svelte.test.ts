// Browser-mode component test (see LoginForm.svelte.test.ts). This route
// only depends on $lib/api/agents and $lib/api/providers, not on any
// SvelteKit routing modules, so nothing else needs mocking.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import AgentsPage from './+page.svelte';
import { agents, type Agent } from '$lib/api/agents';
import { providers } from '$lib/api/providers';

vi.mock('$lib/api/agents', () => ({
	agents: {
		list: vi.fn(),
		create: vi.fn(),
		update: vi.fn(),
		remove: vi.fn(),
		getRoutingRules: vi.fn(),
		setRoutingRules: vi.fn()
	}
}));

vi.mock('$lib/api/providers', () => ({
	providers: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() }
}));

const researcher: Agent = {
	id: 'agent-1',
	name: 'Researcher',
	description: 'Looks things up',
	instructions: 'Be thorough',
	model: 'anthropic:claude-haiku-4-5-20251001',
	agentos_agent_id: 'agentos-1'
};

const anthropicProvider = {
	id: 'prov-1',
	provider: 'anthropic' as const,
	label: 'My Anthropic key',
	base_url: null,
	is_default: true
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('agents/+page.svelte', () => {
	it('switches an agent card into an edit form seeded with its current values', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);

		render(AgentsPage);

		await expect.element(page.getByText('Researcher')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Edit' }).click();

		// Two "Name" inputs exist while editing: the New agent form and this
		// card's edit form -- the edit one (second in DOM order) is seeded.
		await expect.element(page.getByPlaceholder('Name').nth(1)).toHaveValue('Researcher');
		await expect
			.element(page.getByRole('combobox').last())
			.toHaveValue('anthropic:claude-haiku-4-5-20251001');
	});

	it('saves an edit via agents.update and exits edit mode', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);
		vi.mocked(agents.update).mockResolvedValueOnce({ ...researcher, name: 'Deep Researcher' });

		render(AgentsPage);

		await expect.element(page.getByText('Researcher')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Edit' }).click();
		await page.getByPlaceholder('Name').nth(1).fill('Deep Researcher');
		await page.getByRole('button', { name: 'Save changes' }).click();

		expect(agents.update).toHaveBeenCalledWith('agent-1', {
			name: 'Deep Researcher',
			description: 'Looks things up',
			instructions: 'Be thorough',
			model: 'anthropic:claude-haiku-4-5-20251001'
		});
		await expect
			.element(page.getByRole('button', { name: 'Save changes' }))
			.not.toBeInTheDocument();
	});

	it('discards changes when Cancel is clicked, without calling agents.update', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(providers.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(agents.getRoutingRules).mockResolvedValue([]);

		render(AgentsPage);

		await expect.element(page.getByText('Researcher')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Edit' }).click();
		await page.getByRole('button', { name: 'Cancel' }).click();

		expect(agents.update).not.toHaveBeenCalled();
		await expect.element(page.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
	});
});
