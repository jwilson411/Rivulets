// Browser-mode component test (see agents/agentsPage.svelte.test.ts). This
// route depends on $lib/api/sync, plus the real (unmocked) $lib/api/client
// for the ApiError class used in instanceof checks.

import { page } from 'vitest/browser';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import SyncPage from './+page.svelte';
import { sync, type SyncStatus, type SyncConflict } from '$lib/api/sync';
import { ApiError } from '$lib/api/client';

vi.mock('$lib/api/sync', () => ({
	sync: {
		status: vi.fn(),
		connect: vi.fn(),
		disconnect: vi.fn(),
		conflicts: vi.fn(),
		resolveConflict: vi.fn(),
		getCapabilities: vi.fn(),
		setCapabilities: vi.fn()
	}
}));

const runningStatus: SyncStatus = {
	running: true,
	node_id: 'node-abc123',
	peers: [
		{
			peer_id: 'peer-1',
			address: '/ip4/1.2.3.4/tcp/5000/p2p/peer-1',
			connected: true,
			capabilities: []
		}
	],
	pending_changes: 0
};

beforeEach(() => {
	vi.mocked(sync.getCapabilities).mockResolvedValue({ capabilities: [] });
});

const conflict: SyncConflict = {
	id: 'conf-1',
	entity_type: 'channel',
	entity_id: 'chan-1',
	local_snapshot: { name: 'general' },
	remote_snapshot: { name: 'general-renamed' },
	remote_node_id: 'node-xyz789',
	detected_at: '2026-08-01T00:00:00Z'
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('sync/+page.svelte', () => {
	it('shows running status and connected peers', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);

		render(SyncPage);

		await expect.element(page.getByText('running')).toBeInTheDocument();
		await expect.element(page.getByText('node: node-abc123', { exact: false })).toBeInTheDocument();
		await expect
			.element(page.getByText('No unresolved conflicts', { exact: false }))
			.toBeInTheDocument();
	});

	it('connects to a peer via sync.connect and clears the address field', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);
		vi.mocked(sync.connect).mockResolvedValueOnce({
			peer_id: 'peer-2',
			address: '/ip4/9.9.9.9/tcp/5000/p2p/peer-2',
			connected: true,
			capabilities: []
		});

		render(SyncPage);
		const input = page.getByPlaceholder(/Multiaddr/);
		await expect.element(input).toBeInTheDocument();

		await input.fill('/ip4/9.9.9.9/tcp/5000/p2p/peer-2');
		await page.getByRole('button', { name: 'Connect', exact: true }).click();

		expect(sync.connect).toHaveBeenCalledWith('/ip4/9.9.9.9/tcp/5000/p2p/peer-2');
		await expect.element(input).toHaveValue('');
	});

	it('disconnects a peer via sync.disconnect', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);
		vi.mocked(sync.disconnect).mockResolvedValueOnce(undefined);

		render(SyncPage);
		await expect.element(page.getByText('running')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Disconnect' }).click();

		expect(sync.disconnect).toHaveBeenCalledWith('peer-1');
	});

	it('resolves a conflict by keeping the remote version', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([conflict]);
		vi.mocked(sync.resolveConflict).mockResolvedValueOnce(conflict);

		render(SyncPage);
		await expect.element(page.getByText('Conflicts (1)')).toBeInTheDocument();
		await expect.element(page.getByText('general-renamed')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Keep remote' }).click();

		expect(sync.resolveConflict).toHaveBeenCalledWith('conf-1', 'remote');
	});

	it('shows a not-running status and disables Connect', async () => {
		vi.mocked(sync.status).mockResolvedValue({ ...runningStatus, running: false, node_id: null });
		vi.mocked(sync.conflicts).mockResolvedValue([]);

		render(SyncPage);

		await expect.element(page.getByText('not running')).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Connect', exact: true })).toBeDisabled();
	});

	it('shows the ApiError message when disconnecting fails', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);
		vi.mocked(sync.disconnect).mockRejectedValueOnce(new ApiError(500, 'peer unreachable'));

		render(SyncPage);
		await page.getByRole('button', { name: 'Disconnect' }).click();

		await expect.element(page.getByText('peer unreachable')).toBeInTheDocument();
	});
});
