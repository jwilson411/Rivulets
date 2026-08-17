// Browser-mode component test for Usage (06-screens.md → Usage, mockup
// 2l): three large stats, a Day/Week/Month segmented control, bars by
// agent and model.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import UsagePage from './+page.svelte';
import { usage, type Usage } from '$lib/api/usage';

vi.mock('$lib/api/usage', () => ({
	usage: { get: vi.fn() }
}));

afterEach(() => {
	vi.clearAllMocks();
});

const weekUsage: Usage = {
	range: 'week',
	since: '2026-01-01T00:00:00Z',
	total_input_tokens: 900_000,
	total_output_tokens: 384_220,
	total_tokens: 1_284_220,
	total_cost_usd: 18.4,
	cost_incomplete: false,
	run_count: 46,
	by_agent: [
		{
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			input_tokens: 500_000,
			output_tokens: 167_794,
			total_tokens: 667_794,
			cost_usd: 9.5,
			run_count: 20
		},
		{
			agent_id: 'agent-2',
			agent_name: 'Coder',
			input_tokens: 250_000,
			output_tokens: 109_582,
			total_tokens: 359_582,
			cost_usd: 5.2,
			run_count: 14
		}
	],
	by_model: [
		{
			model: 'anthropic:claude-3-5-haiku-latest',
			tier: null,
			input_tokens: 600_000,
			output_tokens: 298_954,
			total_tokens: 898_954,
			cost_usd: 13.0,
			run_count: 30
		}
	]
};

describe('usage/+page.svelte', () => {
	it('shows the three large stats with compact token formatting', async () => {
		vi.mocked(usage.get).mockResolvedValue(weekUsage);

		render(UsagePage);

		await expect.element(page.getByText('1.28M')).toBeInTheDocument();
		await expect.element(page.getByText('$18.40')).toBeInTheDocument();
		await expect.element(page.getByText('46', { exact: true })).toBeInTheDocument();
	});

	it('shows agent and model bars with their share of tokens', async () => {
		vi.mocked(usage.get).mockResolvedValue(weekUsage);

		render(UsagePage);

		await expect.element(page.getByText('Researcher')).toBeInTheDocument();
		await expect.element(page.getByText('52%')).toBeInTheDocument();
		await expect.element(page.getByText('Claude 3.5 Haiku')).toBeInTheDocument();
	});

	it('does not print raw provider:model ids on the by-model bars', async () => {
		vi.mocked(usage.get).mockResolvedValue({
			...weekUsage,
			by_model: [
				{
					model: 'openai:gpt-4o-mini',
					tier: 'cheap',
					input_tokens: 1_284_220,
					output_tokens: 0,
					total_tokens: 1_284_220,
					cost_usd: 18.4,
					run_count: 46
				}
			]
		});

		render(UsagePage);

		await expect.element(page.getByText('GPT-4o mini')).toBeInTheDocument();
		await expect.element(page.getByText('openai:gpt-4o-mini')).not.toBeInTheDocument();
	});

	it('refetches when the window changes via the segmented control', async () => {
		vi.mocked(usage.get).mockResolvedValue(weekUsage);

		render(UsagePage);
		await expect.element(page.getByText('1.28M')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Day' }).click();

		expect(usage.get).toHaveBeenLastCalledWith('day');
	});

	it('marks the cost as a floor when a model has no price on file', async () => {
		vi.mocked(usage.get).mockResolvedValue({ ...weekUsage, cost_incomplete: true });

		render(UsagePage);

		await expect.element(page.getByText('$18.40+')).toBeInTheDocument();
	});

	it('says when nothing has run in the window', async () => {
		vi.mocked(usage.get).mockResolvedValue({
			...weekUsage,
			run_count: 0,
			by_agent: [],
			by_model: []
		});

		render(UsagePage);

		await expect.element(page.getByText('Nothing has run in this window.')).toBeInTheDocument();
	});

	it('shows a quiet error with a retry when usage fails to load', async () => {
		vi.mocked(usage.get).mockRejectedValue(new Error('boom'));

		render(UsagePage);

		await expect.element(page.getByText("Couldn't load usage.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});
});
