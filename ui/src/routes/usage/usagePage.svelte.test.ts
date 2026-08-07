// Browser-mode component test (see sync/syncPage.svelte.test.ts). This
// route depends only on $lib/api/usage.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import UsagePage from './+page.svelte';
import { usage, type Usage } from '$lib/api/usage';

vi.mock('$lib/api/usage', () => ({
	usage: { get: vi.fn() }
}));

const weekUsage: Usage = {
	range: 'week',
	since: '2026-08-01T00:00:00Z',
	total_input_tokens: 1000,
	total_output_tokens: 500,
	total_tokens: 1500,
	total_cost_usd: 0.42,
	cost_incomplete: false,
	run_count: 3,
	by_agent: [
		{
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			input_tokens: 1000,
			output_tokens: 500,
			total_tokens: 1500,
			cost_usd: 0.42,
			run_count: 3
		}
	],
	by_model: [
		{
			model: 'anthropic:claude-haiku-4-5-20251001',
			tier: 'cheap',
			input_tokens: 1000,
			output_tokens: 500,
			total_tokens: 1500,
			cost_usd: 0.42,
			run_count: 3
		}
	]
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('usage/+page.svelte', () => {
	it('loads the default (week) range and shows totals, by-agent, and by-model breakdowns', async () => {
		vi.mocked(usage.get).mockResolvedValue(weekUsage);

		render(UsagePage);

		expect(usage.get).toHaveBeenCalledWith('week');
		await expect.element(page.getByText('1,500', { exact: true }).first()).toBeInTheDocument();
		await expect.element(page.getByText('$0.4200', { exact: false }).first()).toBeInTheDocument();
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();
		await expect.element(page.getByText('anthropic:claude-haiku-4-5-20251001')).toBeInTheDocument();
		await expect.element(page.getByText('cheap')).toBeInTheDocument();
	});

	it('switches range and re-fetches on click', async () => {
		vi.mocked(usage.get).mockResolvedValue(weekUsage);

		render(UsagePage);
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();

		vi.mocked(usage.get).mockResolvedValueOnce({ ...weekUsage, range: 'month' });
		await page.getByRole('button', { name: 'Month' }).click();

		expect(usage.get).toHaveBeenCalledWith('month');
	});

	it('shows the empty state when there are no runs in the window', async () => {
		vi.mocked(usage.get).mockResolvedValue({
			...weekUsage,
			run_count: 0,
			by_agent: [],
			by_model: []
		});

		render(UsagePage);

		await expect
			.element(page.getByText('No agent runs recorded in this window.'))
			.toBeInTheDocument();
	});

	it('marks an incomplete cost estimate with a "+" and an explanatory note', async () => {
		vi.mocked(usage.get).mockResolvedValue({ ...weekUsage, cost_incomplete: true });

		render(UsagePage);

		await expect.element(page.getByText('$0.4200+')).toBeInTheDocument();
		await expect
			.element(page.getByText("aren't in that table", { exact: false }))
			.toBeInTheDocument();
	});

	it('shows the load error message when usage.get rejects', async () => {
		vi.mocked(usage.get).mockRejectedValue(new Error('workspace unreachable'));

		render(UsagePage);

		await expect.element(page.getByText('workspace unreachable')).toBeInTheDocument();
	});
});
