import { page } from 'vitest/browser';
import { describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import SearchJumpButton from './SearchJumpButton.svelte';
import { paletteShortcutLabel } from '$lib/format';

describe('SearchJumpButton.svelte', () => {
	it('labels the control Search / jump and shows the palette shortcut', async () => {
		render(SearchJumpButton, { onOpen: vi.fn() });

		await expect.element(page.getByRole('button', { name: 'Search / jump' })).toBeInTheDocument();
		await expect.element(page.getByText(paletteShortcutLabel())).toBeInTheDocument();
	});

	it('opens the palette when clicked', async () => {
		const onOpen = vi.fn();
		render(SearchJumpButton, { onOpen });

		await page.getByRole('button', { name: 'Search / jump' }).click();

		expect(onOpen).toHaveBeenCalledOnce();
	});
});
