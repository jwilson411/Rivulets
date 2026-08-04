<script lang="ts">
	import { ApiError } from '$lib/api/client';
	import { sync, type Peer, type SyncConflict, type SyncStatus } from '$lib/api/sync';

	let status = $state<SyncStatus | null>(null);
	let conflicts = $state<SyncConflict[]>([]);
	let loadError = $state<string | null>(null);

	let connectAddress = $state('');
	let connecting = $state(false);
	let connectError = $state<string | null>(null);
	let rowError = $state<string | null>(null);
	let resolvingId = $state<string | null>(null);

	async function refresh() {
		loadError = null;
		try {
			[status, conflicts] = await Promise.all([sync.status(), sync.conflicts()]);
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load sync status';
		}
	}

	refresh();

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

<div class="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-lg font-semibold text-zinc-900 dark:text-zinc-50">Sync</h1>
		<p class="text-sm text-zinc-500">
			P2P sync status, connected peers, and conflicts that need a decision (FR-9.6). Nodes on the
			same workspace find each other automatically over the local network — manual connect below is
			the fallback for nodes on different networks (FR-9.3).
		</p>
	</header>

	{#if loadError}
		<p class="text-sm text-red-600 dark:text-red-400">{loadError}</p>
	{:else if status}
		<section class="flex flex-col gap-3 rounded-md border border-zinc-200 p-4 dark:border-zinc-800">
			<div class="flex items-center justify-between">
				<h2 class="text-sm font-medium text-zinc-700 dark:text-zinc-300">Status</h2>
				<span
					class="rounded-full px-2 py-0.5 text-xs {status.running
						? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400'
						: 'bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400'}"
				>
					{status.running ? 'running' : 'not running'}
				</span>
			</div>
			{#if status.node_id}
				<p class="font-mono text-xs text-zinc-400">node: {status.node_id}</p>
			{/if}

			<form onsubmit={handleConnect} class="flex gap-2 pt-2">
				<input
					type="text"
					bind:value={connectAddress}
					placeholder="Multiaddr (e.g. /ip4/1.2.3.4/tcp/5000/p2p/12D3Koo...)"
					class="min-w-0 flex-1 rounded-md border border-zinc-300 px-3 py-2 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900"
				/>
				<button
					type="submit"
					disabled={connecting || !status.running}
					class="shrink-0 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
				>
					{connecting ? 'Connecting…' : 'Connect'}
				</button>
			</form>
			{#if connectError}
				<p class="text-sm text-red-600 dark:text-red-400">{connectError}</p>
			{/if}

			{#if status.peers.length === 0}
				<p class="text-sm text-zinc-400">No peers connected.</p>
			{:else}
				<ul class="flex flex-col gap-2">
					{#each status.peers as peer (peer.peer_id)}
						<li
							class="flex items-center justify-between rounded-md border border-zinc-200 px-3 py-2 dark:border-zinc-800"
						>
							<div>
								<p class="font-mono text-xs text-zinc-700 dark:text-zinc-300">
									{shortId(peer.peer_id)}
								</p>
								<p class="font-mono text-xs text-zinc-400">{peer.address}</p>
							</div>
							<button
								onclick={() => handleDisconnect(peer)}
								class="text-xs text-zinc-400 hover:text-red-600 dark:hover:text-red-400"
							>
								Disconnect
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</section>

		<section class="flex flex-col gap-3">
			<h2 class="text-sm font-medium text-zinc-700 dark:text-zinc-300">
				Conflicts {conflicts.length > 0 ? `(${conflicts.length})` : ''}
			</h2>
			{#if rowError}
				<p class="text-sm text-red-600 dark:text-red-400">{rowError}</p>
			{/if}
			{#if conflicts.length === 0}
				<p class="text-sm text-zinc-400">
					No unresolved conflicts — concurrent edits to the same entity on two disconnected nodes
					show up here.
				</p>
			{:else}
				<ul class="flex flex-col gap-3">
					{#each conflicts as conflict (conflict.id)}
						<li class="rounded-md border border-amber-300 p-4 dark:border-amber-800">
							<div class="mb-3 flex items-center justify-between">
								<p class="text-sm font-medium text-zinc-900 dark:text-zinc-50">
									{conflict.entity_type}
									<span class="ml-1 font-mono text-xs text-zinc-400"
										>{shortId(conflict.entity_id)}</span
									>
								</p>
								<p class="text-xs text-zinc-400">from {shortId(conflict.remote_node_id)}</p>
							</div>

							<div class="overflow-x-auto">
								<table class="w-full text-left text-xs">
									<thead>
										<tr class="text-zinc-400">
											<th class="pr-3 pb-1 font-normal">Field</th>
											<th class="pr-3 pb-1 font-normal">Local (this node)</th>
											<th class="pb-1 font-normal">Remote ({shortId(conflict.remote_node_id)})</th>
										</tr>
									</thead>
									<tbody>
										{#each conflictKeys(conflict) as key (key)}
											{@const localVal = conflict.local_snapshot[key]}
											{@const remoteVal = conflict.remote_snapshot[key]}
											{@const differs = JSON.stringify(localVal) !== JSON.stringify(remoteVal)}
											<tr class="border-t border-zinc-100 dark:border-zinc-800">
												<td class="py-1 pr-3 text-zinc-500">{key}</td>
												<td
													class="py-1 pr-3 font-mono {differs
														? 'text-zinc-900 dark:text-zinc-50'
														: 'text-zinc-400'}">{displayValue(localVal)}</td
												>
												<td
													class="py-1 font-mono {differs
														? 'text-zinc-900 dark:text-zinc-50'
														: 'text-zinc-400'}">{displayValue(remoteVal)}</td
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
									class="rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-700 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300"
								>
									Keep local
								</button>
								<button
									onclick={() => handleResolve(conflict, 'remote')}
									disabled={resolvingId === conflict.id}
									class="rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
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
