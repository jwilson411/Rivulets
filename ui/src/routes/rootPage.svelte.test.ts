// Browser-mode component test (see agents/agentsPage.svelte.test.ts).
// The root route has no imports, no state, and no API dependency -- it's
// static markup shown when no channel is selected -- so there's nothing to
// mock and only one meaningful assertion.

import { page } from 'vitest/browser';
import { describe, expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import RootPage from './+page.svelte';

describe('routes/+page.svelte', () => {
	it('shows the empty-state prompt to select or create a channel', async () => {
		render(RootPage);

		await expect
			.element(page.getByText('Select a channel, or create one in the sidebar.'))
			.toBeInTheDocument();
	});
});
