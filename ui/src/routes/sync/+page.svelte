<script lang="ts">
	import { ApiError } from '$lib/api/client';
	import { auth } from '$lib/api/auth.svelte';
	import OwnerOnly from '$lib/components/OwnerOnly.svelte';
	import {
		sync,
		type CoordinatorStatus,
		type Peer,
		type SyncConflict,
		type SyncStatus
	} from '$lib/api/sync';

	let status = $state<SyncStatus | null>(null);
	let coordinator = $state<CoordinatorStatus | null>(null);
	let conflicts = $state<SyncConflict[]>([]);
	let loadError = $state<string | null>(null);
	let reclaiming = $state(false);
	let reclaimError = $state<string | null>(null);

	let connectAddress = $state('');
	let connecting = $state(false);
	let connectError = $state<string | null>(null);
	let rowError = $state<string | null>(null);
	let resolvingId = $state<string | null>(null);
	let copiedAddress = $state<string | null>(null);

	let myCapabilities = $state<string[]>([]);
	let capabilitiesDraft = $state('');
	let savingCapabilities = $state(false);
	let capabilitiesError = $state<string | null>(null);

	async function refresh() {
		loadError = null;
		try {
			[status, coordinator, conflicts, { capabilities: myCapabilities }] = await Promise.all([
				sync.status(),
				sync.coordinator(),
				sync.conflicts(),
				sync.getCapabilities()
			]);
			capabilitiesDraft = myCapabilities.join(', ');
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load sync status';
		}
	}

	// #351: every /sync endpoint is OwnerGrant-only server-side, so a
	// non-owner session skips the fetches and renders <OwnerOnly> below
	// instead of a wall of 403 load errors.
	if (auth.grant === 'owner') refresh();

	async function handleReclaim() {
		reclaimError = null;
		reclaiming = true;
		try {
			coordinator = await sync.reclaimCoordinator();
		} catch (err) {
			reclaimError = err instanceof ApiError ? err.message : 'Failed to reclaim coordinator';
		} finally {
			reclaiming = false;
		}
	}

	async function handleSetCapabilities(event: SubmitEvent) {
		event.preventDefault();
		capabilitiesError = null;
		const tags = capabilitiesDraft
			.split(',')
			.map((t) => t.trim())
			.filter(Boolean);
		savingCapabilities = true;
		try {
			const result = await sync.setCapabilities(tags);
			myCapabilities = result.capabilities;
		} catch (err) {
			capabilitiesError = err instanceof ApiError ? err.message : 'Failed to save capabilities';
		} finally {
			savingCapabilities = false;
		}
	}

	async function handleConnect(event: SubmitEvent) {
		event.preventDefault();
		connectError = null;
		if (!connectAddress.trim()) return;
		connecting = true;
		try {
			await sync.connect(connectAddress.trim());
			connectAddress = '';
			await refresh();
		} catch (err) {
			connectError = err instanceof ApiError ? err.message : 'Failed to connect';
		} finally {
			connecting = false;
		}
	}

	async function handleCopyAddress(address: string) {
		await navigator.clipboard.writeText(address);
		copiedAddress = address;
	}

	async function handleDisconnect(peer: Peer) {
		rowError = null;
		try {
			await sync.disconnect(peer.peer_id);
			await refresh();
		} catch (err) {
			rowError = err instanceof ApiError ? err.message : 'Failed to disconnect';
		}
	}

	async function handleResolve(conflict: SyncConflict, keep: 'local' | 'remote') {
		rowError = null;
		resolvingId = conflict.id;
		try {
			await sync.resolveConflict(conflict.id, keep);
			await refresh();
		} catch (err) {
			rowError = err instanceof ApiError ? err.message : 'Failed to resolve conflict';
		} finally {
			resolvingId = null;
		}
	}

	function shortId(id: string): string {
		return id.length > 16 ? `${id.slice(0, 8)}…${id.slice(-6)}` : id;
	}

	function conflictKeys(conflict: SyncConflict): string[] {
		return [
			...new Set([
				...Object.keys(conflict.local_snapshot),
				...Object.keys(conflict.remote_snapshot)
			])
		];
	}

	function displayValue(value: unknown): string {
		if (value === null || value === undefined) return '—';
		if (typeof value === 'boolean') return value ? 'true' : 'false';
		return String(value);
	}
</script>

{#if auth.grant !== 'owner'}
	<OwnerOnly title="Sync" />
{:else}
	<div class="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-8">
		<header>
			<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">Sync</h1>
			<p class="text-sm text-neutral-600 dark:text-neutral-400">
				P2P sync status, connected peers, and conflicts that need a decision (FR-9.6). Nodes on the
				same workspace find each other automatically over the local network — manual connect below
				is the fallback for nodes on different networks (FR-9.3).
			</p>
		</header>

		{#if loadError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
		{:else if status}
			<section
				class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
			>
				<div class="flex items-center justify-between">
					<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Status</h2>
					<span
						class="rounded-sm px-2 py-0.5 text-xs {status.running
							? 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400'
							: 'bg-neutral-200 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400'}"
					>
						{status.running ? 'running' : 'not running'}
					</span>
				</div>
				{#if status.node_id}
					<p class="font-mono text-xs text-neutral-500">node: {status.node_id}</p>
				{/if}

				<form onsubmit={handleSetCapabilities} class="flex gap-2 pt-2">
					<input
						type="text"
						bind:value={capabilitiesDraft}
						placeholder="My capabilities (e.g. gpu, cpu-heavy)"
						class="min-w-0 flex-1 rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-xs text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
					/>
					<button
						type="submit"
						disabled={savingCapabilities}
						class="shrink-0 rounded-md border border-ink/15 px-4 py-2 text-sm font-medium text-ink disabled:opacity-50 dark:border-white/15 dark:text-ink-dark"
					>
						{savingCapabilities ? 'Saving…' : 'Save capabilities'}
					</button>
				</form>
				{#if capabilitiesError}
					<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">
						{capabilitiesError}
					</p>
				{/if}

				{#if status.own_addresses.length > 0}
					<div class="flex flex-col gap-2 rounded-md border border-ink/12 p-3 dark:border-white/10">
						<p class="text-xs text-neutral-500">
							Your sync address — paste this into the "Connect to a node" field on the
							<strong>other</strong> device to pair with it manually (e.g. over Tailscale or another network
							peers can't auto-discover each other on).
						</p>
						{#each status.own_addresses as address (address)}
							<div class="flex items-center gap-2">
								<code class="min-w-0 flex-1 truncate text-xs text-ink dark:text-ink-dark"
									>{address}</code
								>
								<button
									type="button"
									onclick={() => handleCopyAddress(address)}
									class="shrink-0 rounded-md border border-ink/15 px-3 py-1 text-xs font-medium text-ink dark:border-white/15 dark:text-ink-dark"
								>
									{copiedAddress === address ? 'Copied' : 'Copy'}
								</button>
							</div>
						{/each}
					</div>
				{/if}

				<form onsubmit={handleConnect} class="flex flex-col gap-1 pt-2">
					<p class="text-xs text-neutral-500">
						Connect to a node — paste the sync address copied from the other device here.
					</p>
					<div class="flex gap-2">
						<input
							type="text"
							bind:value={connectAddress}
							placeholder="Multiaddr (e.g. /ip4/1.2.3.4/tcp/5000/p2p/12D3Koo...)"
							class="min-w-0 flex-1 rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-xs text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
						/>
						<button
							type="submit"
							disabled={connecting || !status.running}
							class="shrink-0 rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
						>
							{connecting ? 'Connecting…' : 'Connect'}
						</button>
					</div>
				</form>
				{#if connectError}
					<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{connectError}</p>
				{/if}

				{#if status.peers.length === 0}
					<p class="text-sm text-neutral-500 italic">No peers connected.</p>
				{:else}
					<ul class="flex flex-col gap-2">
						{#each status.peers as peer (peer.peer_id)}
							<li
								class="flex items-center justify-between rounded-md border border-ink/12 px-3 py-2 dark:border-white/10"
							>
								<div>
									<p class="font-mono text-xs text-ink dark:text-ink-dark">
										{shortId(peer.peer_id)}
									</p>
									<p class="font-mono text-xs text-neutral-500">{peer.address}</p>
									{#if peer.capabilities.length > 0}
										<p class="font-mono text-xs text-neutral-500">
											{peer.capabilities.join(', ')}
										</p>
									{/if}
								</div>
								<button
									onclick={() => handleDisconnect(peer)}
									class="text-xs text-neutral-500 hover:text-agent-magenta-600"
								>
									Disconnect
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</section>

			{#if coordinator && coordinator.running}
				<section
					class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
				>
					<div class="flex items-center justify-between">
						<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Coordinator</h2>
						{#if coordinator.coordinator_id}
							<span
								class="rounded-sm px-2 py-0.5 text-xs {coordinator.is_self
									? 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400'
									: 'bg-neutral-200 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400'}"
							>
								{coordinator.is_self ? 'this node' : shortId(coordinator.coordinator_id)}
							</span>
						{/if}
					</div>
					<p class="text-sm text-neutral-600 dark:text-neutral-400">
						A spec-weighted election (#101) picks one peer to own workspace-singleton work (e.g.
						scheduled jobs) so it doesn't run redundantly on every peer at once. Failover is
						automatic; failback to a returning higher-spec peer is not — use reclaim below if you
						want it back explicitly.
					</p>
					<p class="font-mono text-xs text-neutral-500">
						term {coordinator.term} · this node's score {coordinator.self_score.toFixed(1)}
					</p>
					{#if !coordinator.is_self}
						<button
							onclick={handleReclaim}
							disabled={reclaiming}
							class="self-start rounded-md border border-ink/15 px-4 py-2 text-sm font-medium text-ink disabled:opacity-50 dark:border-white/15 dark:text-ink-dark"
						>
							{reclaiming ? 'Reclaiming…' : 'Reclaim coordinator'}
						</button>
					{/if}
					{#if reclaimError}
						<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{reclaimError}</p>
					{/if}
				</section>
			{/if}

			<section class="flex flex-col gap-3">
				<h2 class="text-sm font-medium text-ink dark:text-ink-dark">
					Conflicts {conflicts.length > 0 ? `(${conflicts.length})` : ''}
				</h2>
				{#if rowError}
					<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{rowError}</p>
				{/if}
				{#if conflicts.length === 0}
					<p class="text-sm text-neutral-500 italic">
						No unresolved conflicts — concurrent edits to the same entity on two disconnected nodes
						show up here.
					</p>
				{:else}
					<ul class="flex flex-col gap-3">
						{#each conflicts as conflict (conflict.id)}
							<li
								class="rounded-lg border border-agent-magenta-300 p-4 dark:border-agent-magenta-800/60"
							>
								<div class="mb-3 flex items-center justify-between">
									<p class="text-sm font-medium text-ink dark:text-ink-dark">
										{conflict.entity_type}
										<span class="ml-1 font-mono text-xs text-neutral-500"
											>{shortId(conflict.entity_id)}</span
										>
									</p>
									<p class="text-xs text-neutral-500">from {shortId(conflict.remote_node_id)}</p>
								</div>

								<div class="overflow-x-auto">
									<table class="w-full text-left text-xs">
										<thead>
											<tr class="text-neutral-500">
												<th class="pr-3 pb-1 font-normal">Field</th>
												<th class="pr-3 pb-1 font-normal">Local (this node)</th>
												<th class="pb-1 font-normal">Remote ({shortId(conflict.remote_node_id)})</th
												>
											</tr>
										</thead>
										<tbody>
											{#each conflictKeys(conflict) as key (key)}
												{@const localVal = conflict.local_snapshot[key]}
												{@const remoteVal = conflict.remote_snapshot[key]}
												{@const differs = JSON.stringify(localVal) !== JSON.stringify(remoteVal)}
												<tr class="border-t border-ink/10 dark:border-white/10">
													<td class="py-1 pr-3 text-neutral-500">{key}</td>
													<td
														class="py-1 pr-3 font-mono {differs
															? 'text-ink dark:text-ink-dark'
															: 'text-neutral-500'}">{displayValue(localVal)}</td
													>
													<td
														class="py-1 font-mono {differs
															? 'text-ink dark:text-ink-dark'
															: 'text-neutral-500'}">{displayValue(remoteVal)}</td
													>
												</tr>
											{/each}
										</tbody>
									</table>
								</div>

								<div class="mt-3 flex gap-2">
									<button
										onclick={() => handleResolve(conflict, 'local')}
										disabled={resolvingId === conflict.id}
										class="rounded-md border border-ink/15 px-3 py-1.5 text-xs font-medium text-ink disabled:opacity-50 dark:border-white/15 dark:text-ink-dark"
									>
										Keep local
									</button>
									<button
										onclick={() => handleResolve(conflict, 'remote')}
										disabled={resolvingId === conflict.id}
										class="rounded-md bg-agent-cyan px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
									>
										Keep remote
									</button>
								</div>
							</li>
						{/each}
					</ul>
				{/if}
			</section>
		{/if}
	</div>
{/if}
