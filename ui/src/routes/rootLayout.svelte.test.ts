// Browser-mode component test. +layout.svelte gates the entire app behind
// `auth.isAuthenticated` (Unlock vs. Name vs. the app shell) -- that
// conditional is real logic worth covering. Rendering the authenticated
// branch pulls in the real shell (icon rail + context panel + tabs), whose
// API dependencies are mocked here since +layout.svelte doesn't abstract
// them away itself.

import { createRawSnippet } from 'svelte';
import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import RootLayout from './+layout.svelte';

const authState = vi.hoisted(() => ({
	isAuthenticated: false,
	humanId: null as string | null,
	displayName: null as string | null,
	grant: null as string | null,
	resumeDisplayName: null as string | null
}));

const resumeInviteSessionMock = vi.hoisted(() => vi.fn());

const routeState = vi.hoisted(() => ({ pathname: '/' }));

vi.mock('$app/state', () => ({
	page: {
		get url() {
			return new URL('http://localhost' + routeState.pathname);
		}
	}
}));

vi.mock('$app/paths', () => ({
	resolve: (path: string) => path
}));

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get isAuthenticated() {
			return authState.isAuthenticated;
		},
		get humanId() {
			return authState.humanId;
		},
		get displayName() {
			return authState.displayName;
		},
		get grant() {
			return authState.grant;
		},
		get resumeDisplayName() {
			return authState.resumeDisplayName;
		},
		resumeInviteSession: resumeInviteSessionMock,
		logout: vi.fn(),
		claimIdentity: vi.fn(),
		clearIdentity: vi.fn()
	}
}));

vi.mock('$lib/api/humans', () => ({ humans: { list: vi.fn() } }));
vi.mock('$lib/api/channels', () => ({
	channels: { list: vi.fn(), create: vi.fn() }
}));
vi.mock('$lib/api/approvals', () => ({ approvals: { list: vi.fn() } }));

function childrenSnippet(text: string) {
	return createRawSnippet(() => ({
		render: () => `<div>${text}</div>`
	}));
}

afterEach(() => {
	vi.clearAllMocks();
	authState.isAuthenticated = false;
	authState.humanId = null;
	authState.displayName = null;
	authState.grant = null;
	authState.resumeDisplayName = null;
	routeState.pathname = '/';
});

describe('routes/+layout.svelte', () => {
	it('shows the Unlock screen and not the app chrome when unauthenticated', async () => {
		authState.isAuthenticated = false;

		render(RootLayout, { children: childrenSnippet('channel content') });

		await expect
			.element(page.getByRole('button', { name: 'Generate a recovery phrase' }))
			.toBeInTheDocument();
		await expect.element(page.getByText('channel content')).not.toBeInTheDocument();
	});

	it('shows the Name screen once authenticated but before an identity is claimed', async () => {
		authState.isAuthenticated = true;
		authState.humanId = null;
		const { humans } = await import('$lib/api/humans');
		vi.mocked(humans.list).mockResolvedValue([]);

		render(RootLayout, { children: childrenSnippet('channel content') });

		await expect.element(page.getByText('What should we call you?')).toBeInTheDocument();
		await expect.element(page.getByText('channel content')).not.toBeInTheDocument();
	});

	it('renders the icon rail and routed children once an identity is claimed', async () => {
		authState.isAuthenticated = true;
		authState.humanId = 'human-1';
		authState.displayName = 'Riley';
		authState.grant = 'owner';
		const { channels } = await import('$lib/api/channels');
		const { approvals } = await import('$lib/api/approvals');
		vi.mocked(channels.list).mockResolvedValue([]);
		vi.mocked(approvals.list).mockResolvedValue([]);

		render(RootLayout, { children: childrenSnippet('channel content') });

		await expect.element(page.getByText('channel content')).toBeInTheDocument();
		await expect
			.element(page.getByRole('navigation', { name: 'Main' }).first())
			.toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'Generate a recovery phrase' }))
			.not.toBeInTheDocument();
	});

	it('shows the pending-approvals badge count on the rail (#102)', async () => {
		authState.isAuthenticated = true;
		authState.humanId = 'human-1';
		authState.displayName = 'Riley';
		authState.grant = 'owner';
		const { channels } = await import('$lib/api/channels');
		const { approvals } = await import('$lib/api/approvals');
		vi.mocked(channels.list).mockResolvedValue([]);
		vi.mocked(approvals.list).mockResolvedValue([
			{ status: 'pending' },
			{ status: 'pending' },
			{ status: 'approved' }
		] as never);

		render(RootLayout, { children: childrenSnippet('channel content') });

		const approvalsLink = page.getByRole('link', { name: 'Approvals' }).first();
		await expect.element(approvalsLink.getByText('2', { exact: true })).toBeInTheDocument();
	});

	it('silently resumes a stored invite session on load instead of showing Unlock (#350)', async () => {
		authState.isAuthenticated = false;
		authState.resumeDisplayName = 'Ada';
		// Keep the resume in flight so the interim state is observable.
		let finishResume!: (value: boolean) => void;
		resumeInviteSessionMock.mockReturnValue(
			new Promise<boolean>((resolve) => {
				finishResume = resolve;
			})
		);

		render(RootLayout, { children: childrenSnippet('channel content') });

		await expect.element(page.getByText('Signing you back in…')).toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'Generate a recovery phrase' }))
			.not.toBeInTheDocument();
		expect(resumeInviteSessionMock).toHaveBeenCalledOnce();

		finishResume(true);
	});

	it('falls back to the Unlock screen when the silent resume fails', async () => {
		authState.isAuthenticated = false;
		authState.resumeDisplayName = 'Ada';
		resumeInviteSessionMock.mockImplementation(async () => {
			authState.resumeDisplayName = null;
			return false;
		});

		render(RootLayout, { children: childrenSnippet('channel content') });

		await expect
			.element(page.getByRole('button', { name: 'Generate a recovery phrase' }))
			.toBeInTheDocument();
	});

	it('does not attempt a resume when no invite credential is stored', async () => {
		authState.isAuthenticated = false;
		authState.resumeDisplayName = null;

		render(RootLayout, { children: childrenSnippet('channel content') });

		await expect
			.element(page.getByRole('button', { name: 'Generate a recovery phrase' }))
			.toBeInTheDocument();
		expect(resumeInviteSessionMock).not.toHaveBeenCalled();
	});

	it('renders routed children directly on an /invite/ route, bypassing auth entirely', async () => {
		authState.isAuthenticated = false;
		routeState.pathname = '/invite/inv-1.secret';

		render(RootLayout, { children: childrenSnippet('accept invite form') });

		await expect.element(page.getByText('accept invite form')).toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'Generate a recovery phrase' }))
			.not.toBeInTheDocument();
	});
});
