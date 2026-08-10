// Browser-mode component test (see LoginForm.svelte.test.ts / Sidebar.svelte.test.ts).
// Exercises FilterableList through FilterableList.testHost.svelte, since the
// `item` prop is a snippet that can only be constructed in .svelte markup.

import { page } from 'vitest/browser';
import { describe, expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import TestHost from './FilterableList.testHost.svelte';
import type { ListFilter } from './FilterableList.svelte';

interface Widget {
	id: string;
	name: string;
	kind: string;
}

function widget(id: string, name: string, kind: string): Widget {
	return { id, name, kind };
}

describe('FilterableList.svelte', () => {
	it('renders every item when there is no query or filter applied', async () => {
		const items = [widget('1', 'Alpha', 'a'), widget('2', 'Beta', 'b')];
		await render(TestHost, { items });

		await expect.element(page.getByText('Alpha (a)')).toBeInTheDocument();
		await expect.element(page.getByText('Beta (b)')).toBeInTheDocument();
	});

	it('shows the empty message when there are no items at all', async () => {
		await render(TestHost, { items: [] });

		await expect.element(page.getByText('Nothing here yet.')).toBeInTheDocument();
	});

	it('filters items by the search box, case-insensitively', async () => {
		const items = [widget('1', 'Alpha', 'a'), widget('2', 'Beta', 'b')];
		await render(TestHost, { items });

		await page.getByRole('searchbox').fill('ALP');

		await expect.element(page.getByText('Alpha (a)')).toBeInTheDocument();
		await expect.element(page.getByText('Beta (b)')).not.toBeInTheDocument();
	});

	it('shows the no-match message when the search excludes everything', async () => {
		const items = [widget('1', 'Alpha', 'a')];
		await render(TestHost, { items });

		await page.getByRole('searchbox').fill('zzz');

		await expect.element(page.getByText('No items match your search.')).toBeInTheDocument();
	});

	it('applies a select filter using its predicate', async () => {
		const items = [widget('1', 'Alpha', 'a'), widget('2', 'Beta', 'b')];
		const filters: ListFilter<Widget>[] = [
			{
				id: 'kind',
				label: 'Kind',
				options: [
					{ value: 'a', label: 'A' },
					{ value: 'b', label: 'B' }
				],
				predicate: (w, value) => w.kind === value
			}
		];
		await render(TestHost, { items, filters });

		await page.getByRole('combobox', { name: 'Kind' }).selectOptions('b');

		await expect.element(page.getByText('Alpha (a)')).not.toBeInTheDocument();
		await expect.element(page.getByText('Beta (b)')).toBeInTheDocument();
	});

	it('combines search and filter, both must match', async () => {
		const items = [widget('1', 'Alpha', 'a'), widget('2', 'Alicia', 'b')];
		const filters: ListFilter<Widget>[] = [
			{
				id: 'kind',
				label: 'Kind',
				options: [{ value: 'a', label: 'A' }],
				predicate: (w, value) => w.kind === value
			}
		];
		await render(TestHost, { items, filters });

		await page.getByRole('searchbox').fill('ali');
		await page.getByRole('combobox', { name: 'Kind' }).selectOptions('a');

		await expect.element(page.getByText('No items match your search.')).toBeInTheDocument();
		await expect.element(page.getByText('Alpha (a)')).not.toBeInTheDocument();
		await expect.element(page.getByText('Alicia (b)')).not.toBeInTheDocument();
	});

	it('paginates and resets to page 1 when the filtered set changes', async () => {
		const items = [widget('1', 'Alpha', 'a'), widget('2', 'Beta', 'a'), widget('3', 'Gamma', 'a')];
		await render(TestHost, { items, pageSize: 2 });

		await expect.element(page.getByText('Alpha (a)')).toBeInTheDocument();
		await expect.element(page.getByText('Beta (a)')).toBeInTheDocument();
		await expect.element(page.getByText('Gamma (a)')).not.toBeInTheDocument();
		await expect.element(page.getByText('Page 1 of 2 (3 items)')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Next' }).click();

		await expect.element(page.getByText('Gamma (a)')).toBeInTheDocument();
		await expect.element(page.getByText('Alpha (a)')).not.toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Next' })).toBeDisabled();

		await page.getByRole('searchbox').fill('a');

		await expect.element(page.getByText('Alpha (a)')).toBeInTheDocument();
	});
});
