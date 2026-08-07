// Browser-mode component test (see agents/agentsPage.svelte.test.ts and
// Sidebar.svelte.test.ts). +layout.svelte is mostly a thin shell around
// `{@render children()}`, but it does gate the entire app behind
// `auth.isAuthenticated` (LoginForm vs. the real app chrome) -- that
// conditional is real logic worth covering, so this isn't skipped.
//
// Rendering the authenticated branch pulls in the real Sidebar component,
// which has its own API dependencies -- those are mocked here the same way
// Sidebar.svelte.test.ts mocks them, since +layout.svelte doesn't abstract
// them away itself.

import { createRawSnippet } from 'svelte';
import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import RootLayout from './+layout.svelte';

const authState = vi.hoisted(() => ({ isAuthenticated: false }));

vi.mock('$app/state', () => ({
	page: { url: new URL('http://localhost/') }
}));

vi.mock('$app/paths', () => ({
	resolve: (path: string) => path
}));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get isAuthenticated() {
			return authState.isAuthenticated;
		},
		logout: vi.fn()
	}
}));

vi.mock('$lib/api/channels', () => ({
	channels: { list: vi.fn(), create: vi.fn() }
}));
vi.mock('$lib/api/agents', () => ({ agents: { list: vi.fn() } }));
vi.mock('$lib/api/teams', () => ({ teams: { list: vi.fn() } }));
vi.mock('$lib/api/providers', () => ({ providers: { list: vi.fn() } }));
vi.mock('$lib/api/mcpServers', () => ({ mcpServers: { list: vi.fn() } }));
vi.mock('$lib/api/tools', () => ({ tools: { list: vi.fn() } }));
vi.mock('$lib/api/sync', () => ({ sync: { status: vi.fn() } }));

function childrenSnippet(text: string) {
	return createRawSnippet(() => ({
		render: () => `<div>${text}</div>`
	}));
}

afterEach(() => {
	vi.clearAllMocks();
	authState.isAuthenticated = false;
});

describe('routes/+layout.svelte', () => {
	it('shows the login form and not the app chrome when unauthenticated', async () => {
		authState.isAuthenticated = false;

		render(RootLayout, { children: childrenSnippet('channel content') });

		await expect
			.element(page.getByLabelText('Workspace recovery phrase (12 words)'))
			.toBeInTheDocument();
		await expect.element(page.getByText('channel content')).not.toBeInTheDocument();
	});

	it('renders the sidebar and routed children once authenticated', async () => {
		authState.isAuthenticated = true;
		const { channels } = await import('$lib/api/channels');
		const { agents } = await import('$lib/api/agents');
		const { teams } = await import('$lib/api/teams');
		const { providers } = await import('$lib/api/providers');
		const { mcpServers } = await import('$lib/api/mcpServers');
		const { tools } = await import('$lib/api/tools');
		const { sync } = await import('$lib/api/sync');
		vi.mocked(channels.list).mockResolvedValue([]);
		vi.mocked(agents.list).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);
		vi.mocked(providers.list).mockResolvedValue([]);
		vi.mocked(mcpServers.list).mockResolvedValue([]);
		vi.mocked(tools.list).mockResolvedValue([]);
		vi.mocked(sync.status).mockResolvedValue({
			running: false,
			node_id: null,
			peers: [],
			pending_changes: 0
		});

		render(RootLayout, { children: childrenSnippet('channel content') });

		await expect.element(page.getByText('channel content')).toBeInTheDocument();
		await expect
			.element(page.getByLabelText('Workspace recovery phrase (12 words)'))
			.not.toBeInTheDocument();
	});
});
