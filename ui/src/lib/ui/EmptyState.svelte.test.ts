import { page } from 'vitest/browser';
import { describe, expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import EmptyState from './EmptyState.svelte';

describe('EmptyState.svelte', () => {
	it('renders the message', async () => {
		render(EmptyState, { message: 'No items yet.' });

		await expect.element(page.getByText('No items yet.')).toBeInTheDocument();
	});
});
