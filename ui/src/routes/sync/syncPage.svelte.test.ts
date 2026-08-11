// Browser-mode component test (see agents/agentsPage.svelte.test.ts). This
// route depends on $lib/api/sync, plus the real (unmocked) $lib/api/client
// for the ApiError class used in instanceof checks.

import { page } from 'vitest/browser';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import SyncPage from './+page.svelte';
import { sync, type CoordinatorStatus, type SyncStatus, type SyncConflict } from '$lib/api/sync';
import { ApiError } from '$lib/api/client';

vi.mock('$lib/api/sync', () => ({
	sync: {
		status: vi.fn(),
		connect: vi.fn(),
		disconnect: vi.fn(),
		coordinator: vi.fn(),
		reclaimCoordinator: vi.fn(),
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
	pending_changes: 0,
	own_addresses: ['/ip4/192.168.1.5/tcp/5000/p2p/node-abc123']
};

const selfCoordinator: CoordinatorStatus = {
	running: true,
	node_id: 'node-abc123',
	coordinator_id: 'node-abc123',
	term: 1,
	is_self: true,
	self_score: 42.5,
	peer_scores: {}
};

let writeTextMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
	vi.mocked(sync.getCapabilities).mockResolvedValue({ capabilities: [] });
	vi.mocked(sync.coordinator).mockResolvedValue(selfCoordinator);
	// navigator.clipboard.writeText is stubbed since real clipboard access
	// needs OS-level permissions Playwright's headless Chromium doesn't
	// grant by default (see invites/invitesPage.svelte.test.ts).
	writeTextMock = vi.fn().mockResolvedValue(undefined);
	Object.defineProperty(navigator, 'clipboard', {
		value: { writeText: writeTextMock },
		configurable: true
	});
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

	it('shows own sync address with a copy affordance (#132)', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);

		render(SyncPage);
		await expect
			.element(page.getByText('/ip4/192.168.1.5/tcp/5000/p2p/node-abc123'))
			.toBeInTheDocument();

		await page.getByRole('button', { name: 'Copy', exact: true }).click();

		expect(writeTextMock).toHaveBeenCalledWith('/ip4/192.168.1.5/tcp/5000/p2p/node-abc123');
		await expect.element(page.getByRole('button', { name: 'Copied' })).toBeInTheDocument();
	});

	it('hides the own-address section when no addresses are available', async () => {
		vi.mocked(sync.status).mockResolvedValue({ ...runningStatus, own_addresses: [] });
		vi.mocked(sync.conflicts).mockResolvedValue([]);

		render(SyncPage);
		await expect.element(page.getByText('running')).toBeInTheDocument();

		await expect
			.element(page.getByText('Your sync address', { exact: false }))
			.not.toBeInTheDocument();
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
		vi.mocked(sync.status).mockResolvedValue({
			...runningStatus,
			running: false,
			node_id: null,
			own_addresses: []
		});
		vi.mocked(sync.conflicts).mockResolvedValue([]);

		render(SyncPage);

		await expect.element(page.getByText('not running')).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Connect', exact: true })).toBeDisabled();
	});

	it('shows an error when the initial load fails', async () => {
		vi.mocked(sync.status).mockRejectedValueOnce(new Error('Failed to load sync status'));
		vi.mocked(sync.conflicts).mockResolvedValue([]);

		render(SyncPage);

		await expect.element(page.getByText('Failed to load sync status')).toBeInTheDocument();
	});

	it('shows a peer with capabilities and no peers otherwise', async () => {
		vi.mocked(sync.status).mockResolvedValue({
			...runningStatus,
			peers: [{ ...runningStatus.peers[0], capabilities: ['gpu', 'cpu-heavy'] }]
		});
		vi.mocked(sync.conflicts).mockResolvedValue([]);

		render(SyncPage);

		await expect.element(page.getByText('gpu, cpu-heavy')).toBeInTheDocument();
	});

	it('shows a no-peers message when nothing is connected', async () => {
		vi.mocked(sync.status).mockResolvedValue({ ...runningStatus, peers: [] });
		vi.mocked(sync.conflicts).mockResolvedValue([]);

		render(SyncPage);

		await expect.element(page.getByText('No peers connected.')).toBeInTheDocument();
	});

	it('saves capabilities via sync.setCapabilities, splitting and trimming the draft', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);
		vi.mocked(sync.setCapabilities).mockResolvedValueOnce({ capabilities: ['gpu', 'cpu-heavy'] });

		render(SyncPage);
		await page.getByPlaceholder(/My capabilities/).fill(' gpu ,cpu-heavy, ');
		await page.getByRole('button', { name: 'Save capabilities' }).click();

		expect(sync.setCapabilities).toHaveBeenCalledWith(['gpu', 'cpu-heavy']);
	});

	it('shows the ApiError message when saving capabilities fails', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);
		vi.mocked(sync.setCapabilities).mockRejectedValueOnce(
			new ApiError(422, 'invalid capability tag')
		);

		render(SyncPage);
		await page.getByPlaceholder(/My capabilities/).fill('bad tag!');
		await page.getByRole('button', { name: 'Save capabilities' }).click();

		await expect.element(page.getByText('invalid capability tag')).toBeInTheDocument();
	});

	it('does not connect while the address is blank', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);

		render(SyncPage);
		await page.getByRole('button', { name: 'Connect', exact: true }).click();

		expect(sync.connect).not.toHaveBeenCalled();
	});

	it('shows the ApiError message when connecting fails', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);
		vi.mocked(sync.connect).mockRejectedValueOnce(new ApiError(502, 'peer refused connection'));

		render(SyncPage);
		await page.getByPlaceholder(/Multiaddr/).fill('/ip4/9.9.9.9/tcp/5000/p2p/peer-2');
		await page.getByRole('button', { name: 'Connect', exact: true }).click();

		await expect.element(page.getByText('peer refused connection')).toBeInTheDocument();
	});

	it('resolves a conflict by keeping the local version', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([conflict]);
		vi.mocked(sync.resolveConflict).mockResolvedValueOnce(conflict);

		render(SyncPage);
		await expect.element(page.getByText('Conflicts (1)')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Keep local' }).click();

		expect(sync.resolveConflict).toHaveBeenCalledWith('conf-1', 'local');
	});

	it('shows the ApiError message when resolving a conflict fails', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([conflict]);
		vi.mocked(sync.resolveConflict).mockRejectedValueOnce(
			new ApiError(409, 'conflict already resolved')
		);

		render(SyncPage);
		await page.getByRole('button', { name: 'Keep remote' }).click();

		await expect.element(page.getByText('conflict already resolved')).toBeInTheDocument();
	});

	it('shows the ApiError message when disconnecting fails', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);
		vi.mocked(sync.disconnect).mockRejectedValueOnce(new ApiError(500, 'peer unreachable'));

		render(SyncPage);
		await page.getByRole('button', { name: 'Disconnect' }).click();

		await expect.element(page.getByText('peer unreachable')).toBeInTheDocument();
	});

	it('shows this node as coordinator and hides the reclaim button', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);
		vi.mocked(sync.coordinator).mockResolvedValue(selfCoordinator);

		render(SyncPage);

		await expect.element(page.getByText('this node', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('term 1', { exact: false })).toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'Reclaim coordinator' }))
			.not.toBeInTheDocument();
	});

	it('shows another peer as coordinator with a reclaim option', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);
		vi.mocked(sync.coordinator).mockResolvedValue({
			...selfCoordinator,
			coordinator_id: 'peer-9999999999999999',
			is_self: false
		});

		render(SyncPage);

		await expect
			.element(page.getByRole('button', { name: 'Reclaim coordinator' }))
			.toBeInTheDocument();
	});

	it('reclaims coordinator via sync.reclaimCoordinator', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);
		vi.mocked(sync.coordinator).mockResolvedValue({
			...selfCoordinator,
			coordinator_id: 'peer-9999999999999999',
			is_self: false
		});
		vi.mocked(sync.reclaimCoordinator).mockResolvedValueOnce({
			...selfCoordinator,
			term: 2
		});

		render(SyncPage);
		await page.getByRole('button', { name: 'Reclaim coordinator' }).click();

		expect(sync.reclaimCoordinator).toHaveBeenCalled();
		await expect.element(page.getByText('this node', { exact: true })).toBeInTheDocument();
	});

	it('shows the ApiError message when reclaiming coordinator fails', async () => {
		vi.mocked(sync.status).mockResolvedValue(runningStatus);
		vi.mocked(sync.conflicts).mockResolvedValue([]);
		vi.mocked(sync.coordinator).mockResolvedValue({
			...selfCoordinator,
			coordinator_id: 'peer-9999999999999999',
			is_self: false
		});
		vi.mocked(sync.reclaimCoordinator).mockRejectedValueOnce(
			new ApiError(409, 'sync engine is not running')
		);

		render(SyncPage);
		await page.getByRole('button', { name: 'Reclaim coordinator' }).click();

		await expect.element(page.getByText('sync engine is not running')).toBeInTheDocument();
	});

	it('hides the coordinator section when sync is not running', async () => {
		vi.mocked(sync.status).mockResolvedValue({
			...runningStatus,
			running: false,
			node_id: null,
			own_addresses: []
		});
		vi.mocked(sync.conflicts).mockResolvedValue([]);
		vi.mocked(sync.coordinator).mockResolvedValue({
			running: false,
			node_id: null,
			coordinator_id: null,
			term: 0,
			is_self: false,
			self_score: 0,
			peer_scores: {}
		});

		render(SyncPage);

		await expect.element(page.getByText('not running')).toBeInTheDocument();
		await expect.element(page.getByText('Coordinator')).not.toBeInTheDocument();
	});
});
