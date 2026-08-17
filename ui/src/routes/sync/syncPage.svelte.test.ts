// Browser-mode component test for Sync (06-screens.md → Sync, mockup 2m,
// owner only): This machine / Other machines / Conflicts, with coordinator
// details and capability labels tucked behind Advanced.

import { page } from 'vitest/browser';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import SyncPage from './+page.svelte';
import { sync, type CoordinatorStatus, type SyncConflict, type SyncStatus } from '$lib/api/sync';

const authState = vi.hoisted(() => ({ grant: 'owner' }));

vi.mock('$lib/api/sync', () => ({
	sync: {
		status: vi.fn(),
		coordinator: vi.fn(),
		conflicts: vi.fn(),
		getCapabilities: vi.fn(),
		setCapabilities: vi.fn(),
		connect: vi.fn(),
		disconnect: vi.fn(),
		resolveConflict: vi.fn(),
		reclaimCoordinator: vi.fn()
	}
}));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get grant() {
			return authState.grant;
		}
	}
}));

let writeTextMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
	writeTextMock = vi.fn().mockResolvedValue(undefined);
	Object.defineProperty(navigator, 'clipboard', {
		value: { writeText: writeTextMock },
		configurable: true
	});
});

afterEach(() => {
	vi.clearAllMocks();
	authState.grant = 'owner';
});

const runningStatus: SyncStatus = {
	running: true,
	node_id: 'node-a1',
	peers: [
		{
			peer_id: 'node-b7-long-peer-id-value',
			address: '/ip4/192.168.1.20/tcp/5000',
			connected: true,
			capabilities: []
		}
	],
	pending_changes: 0,
	own_addresses: [{ address: '/ip4/192.168.1.12/tcp/5000/p2p/12D3KooTest', scope: 'network' }]
};

const selfCoordinator: CoordinatorStatus = {
	running: true,
	node_id: 'node-a1',
	coordinator_id: 'node-a1',
	term: 3,
	is_self: true,
	self_score: 12.5,
	peer_scores: {}
};

const conflict: SyncConflict = {
	id: 'conf-1',
	entity_type: 'agent',
	entity_id: 'agent-1-entity-id',
	remote_node_id: 'node-b7-long-peer-id-value',
	local_snapshot: { name: 'Assistant' },
	remote_snapshot: { name: 'Helper' },
	detected_at: new Date().toISOString()
};

function seed(overrides?: {
	status?: SyncStatus;
	coordinator?: CoordinatorStatus;
	conflicts?: SyncConflict[];
}) {
	vi.mocked(sync.status).mockResolvedValue(overrides?.status ?? runningStatus);
	vi.mocked(sync.coordinator).mockResolvedValue(overrides?.coordinator ?? selfCoordinator);
	vi.mocked(sync.conflicts).mockResolvedValue(overrides?.conflicts ?? []);
	vi.mocked(sync.getCapabilities).mockResolvedValue({ capabilities: [] });
}

describe('sync/+page.svelte', () => {
	it('renders the owner-only empty state for a guest without firing requests (#351)', async () => {
		authState.grant = 'invite';

		render(SyncPage);

		await expect
			.element(page.getByText('This is only available to the workspace owner.'))
			.toBeInTheDocument();
		expect(sync.status).not.toHaveBeenCalled();
	});

	it('shows this machine, its sync address, and connected machines', async () => {
		seed();

		render(SyncPage);

		await expect
			.element(page.getByText('This machine', { exact: true }).first())
			.toBeInTheDocument();
		await expect.element(page.getByText('Syncing')).toBeInTheDocument();
		await expect
			.element(page.getByText('/ip4/192.168.1.12/tcp/5000/p2p/12D3KooTest'))
			.toBeInTheDocument();
		await expect.element(page.getByText('Connected')).toBeInTheDocument();
	});

	it('says Listening when the engine is up but the mesh is empty (#420)', async () => {
		seed({
			status: { ...runningStatus, peers: [] }
		});

		render(SyncPage);

		await expect.element(page.getByText('Listening')).toBeInTheDocument();
		await expect.element(page.getByText('Syncing')).not.toBeInTheDocument();
	});

	it('labels loopback and container addresses so they are not offered as LAN paste targets (#420)', async () => {
		seed({
			status: {
				...runningStatus,
				peers: [],
				own_addresses: [
					{ address: '/ip4/192.168.1.12/tcp/5000/p2p/12D3KooTest', scope: 'network' },
					{ address: '/ip4/127.0.0.1/tcp/5000/p2p/12D3KooTest', scope: 'loopback' },
					{ address: '/ip4/172.22.0.2/tcp/5000/p2p/12D3KooTest', scope: 'container' }
				]
			}
		});

		render(SyncPage);

		await expect
			.element(
				page.getByText('This machine\'s sync address — paste it into "Connect a machine"', {
					exact: false
				})
			)
			.toBeInTheDocument();
		await expect.element(page.getByText('This machine only')).toBeInTheDocument();
		await expect
			.element(page.getByText('Container only — not reachable from another device'))
			.toBeInTheDocument();
	});

	it('does not tell the user to paste container-only addresses on another device (#420)', async () => {
		seed({
			status: {
				...runningStatus,
				peers: [],
				own_addresses: [
					{ address: '/ip4/127.0.0.1/tcp/5000/p2p/12D3KooTest', scope: 'loopback' },
					{ address: '/ip4/172.22.0.2/tcp/5000/p2p/12D3KooTest', scope: 'container' }
				]
			}
		});

		render(SyncPage);

		await expect
			.element(page.getByText('These addresses only work on this machine.'))
			.toBeInTheDocument();
	});

	it('copies this machine’s sync address', async () => {
		seed();

		render(SyncPage);
		await page.getByRole('button', { name: 'Copy', exact: true }).click();

		expect(writeTextMock).toHaveBeenCalledWith('/ip4/192.168.1.12/tcp/5000/p2p/12D3KooTest');
	});

	it('connects a machine from the paste-address sheet', async () => {
		seed();
		vi.mocked(sync.connect).mockResolvedValueOnce({
			peer_id: 'new-peer',
			address: '/ip4/10.0.0.9/tcp/5000',
			connected: true,
			capabilities: []
		});

		render(SyncPage);
		await page.getByRole('button', { name: 'Connect a machine' }).click();
		await page.getByLabelText('Sync address').fill('/ip4/10.0.0.9/tcp/5000/p2p/12D3KooOther');
		await page.getByRole('button', { name: 'Connect', exact: true }).click();

		expect(sync.connect).toHaveBeenCalledWith('/ip4/10.0.0.9/tcp/5000/p2p/12D3KooOther');
	});

	it('disconnects a machine', async () => {
		seed();
		vi.mocked(sync.disconnect).mockResolvedValueOnce(undefined);

		render(SyncPage);
		await page.getByRole('button', { name: 'Disconnect' }).click();

		expect(sync.disconnect).toHaveBeenCalledWith('node-b7-long-peer-id-value');
	});

	it('resolves a conflict by keeping this machine or the other machine', async () => {
		seed({ conflicts: [conflict] });
		vi.mocked(sync.resolveConflict).mockResolvedValueOnce(conflict);

		render(SyncPage);
		await expect.element(page.getByText('Assistant')).toBeInTheDocument();
		await expect.element(page.getByText('Helper')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Keep the other machine' }).click();

		expect(sync.resolveConflict).toHaveBeenCalledWith('conf-1', 'remote');
	});

	it('says when there are no conflicts', async () => {
		seed();

		render(SyncPage);

		await expect.element(page.getByText('No conflicts.')).toBeInTheDocument();
	});

	it('keeps coordinator term and score behind Advanced', async () => {
		seed({
			coordinator: { ...selfCoordinator, is_self: false, coordinator_id: 'other-node-id-x' }
		});

		render(SyncPage);
		await expect
			.element(page.getByText('This machine', { exact: true }).first())
			.toBeInTheDocument();

		// The term/score details are inside the collapsed Advanced section.
		const details = document.querySelector('details');
		expect(details?.open).toBeFalsy();

		await page.getByText('Advanced').click();
		await expect.element(page.getByText(/term 3/)).toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'Run scheduled work here' }))
			.toBeInTheDocument();
	});

	it('saves this machine’s capability labels from Advanced', async () => {
		seed();
		vi.mocked(sync.setCapabilities).mockResolvedValueOnce({ capabilities: ['gpu'] });

		render(SyncPage);
		await page.getByText('Advanced').click();
		await page.getByLabelText("This machine's labels").fill('gpu');
		await page.getByRole('button', { name: 'Save', exact: true }).click();

		expect(sync.setCapabilities).toHaveBeenCalledWith(['gpu']);
	});

	it('shows a quiet error with retry when sync status fails to load', async () => {
		vi.mocked(sync.status).mockRejectedValue(new Error('boom'));
		vi.mocked(sync.coordinator).mockRejectedValue(new Error('boom'));
		vi.mocked(sync.conflicts).mockRejectedValue(new Error('boom'));
		vi.mocked(sync.getCapabilities).mockRejectedValue(new Error('boom'));

		render(SyncPage);

		await expect.element(page.getByText("Couldn't load sync status.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});
});
