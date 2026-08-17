<script lang="ts">
	import { auth } from '$lib/api/auth.svelte';
	import OwnerOnly from '$lib/components/OwnerOnly.svelte';
	import {
		sync,
		type CoordinatorStatus,
		type OwnAddress,
		type OwnAddressScope,
		type Peer,
		type SyncConflict,
		type SyncStatus
	} from '$lib/api/sync';
	import Button from '$lib/ui/Button.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import SectionLabel from '$lib/ui/SectionLabel.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';
	import StatusPill from '$lib/ui/StatusPill.svelte';

	// Sync (06-screens.md → Sync, mockup 2m, owner only): This machine /
	// Other machines / Conflicts. Coordinator term & score and capability
	// labels hide behind "Advanced" — never in the everyday view. The word
	// "multiaddr" never appears in copy (banned list) — it's a sync address.

	let status = $state<SyncStatus | null>(null);
	let coordinator = $state<CoordinatorStatus | null>(null);
	let conflicts = $state<SyncConflict[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let reclaiming = $state(false);
	let reclaimError = $state<string | null>(null);

	let connecting = $state(false);
	let connectAddress = $state('');
	let connectBusy = $state(false);
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
		} catch {
			loadError = "Couldn't load sync status.";
		} finally {
			loading = false;
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
		} catch {
			reclaimError = "Couldn't take over scheduled work. Try again.";
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
		} catch {
			capabilitiesError = "Couldn't save the labels. Try again.";
		} finally {
			savingCapabilities = false;
		}
	}

	async function handleConnect(event: SubmitEvent) {
		event.preventDefault();
		connectError = null;
		if (!connectAddress.trim()) return;
		connectBusy = true;
		try {
			await sync.connect(connectAddress.trim());
			connectAddress = '';
			connecting = false;
			await refresh();
		} catch {
			connectError = "Couldn't reach that machine. Check the address and try again.";
		} finally {
			connectBusy = false;
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
		} catch {
			rowError = "Couldn't disconnect that machine. Try again.";
		}
	}

	async function handleResolve(conflict: SyncConflict, keep: 'local' | 'remote') {
		rowError = null;
		resolvingId = conflict.id;
		try {
			await sync.resolveConflict(conflict.id, keep);
			await refresh();
		} catch {
			rowError = "Couldn't resolve that conflict. Try again.";
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

	function machineStatusLabel(current: SyncStatus): string {
		if (!current.running) return 'Not running';
		// Empty mesh is listening for peers, not actively syncing (#420).
		return current.peers.length > 0 ? 'Syncing' : 'Listening';
	}

	function machineStatusTone(current: SyncStatus): 'accent' | 'neutral' {
		return current.running && current.peers.length > 0 ? 'accent' : 'neutral';
	}

	function addressScopeNote(scope: OwnAddressScope): string | null {
		if (scope === 'loopback') return 'This machine only';
		if (scope === 'container') return 'Container only — not reachable from another device';
		return null;
	}

	function hasShareableAddress(addresses: OwnAddress[]): boolean {
		return addresses.some((item) => item.scope === 'network');
	}
</script>

{#if auth.grant !== 'owner'}
	<OwnerOnly title="Sync" />
{:else}
	<div class="mx-auto max-w-[720px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
		<h1 class="mb-7 font-display text-[28px] font-semibold text-ink dark:text-ink-dark">Sync</h1>

		{#if loading}
			<SkeletonCards count={2} />
		{:else if loadError}
			<ErrorBanner message={loadError} onRetry={refresh} />
		{:else if status}
			<SectionLabel class="mb-2.5">This machine</SectionLabel>
			<div
				class="mb-3 flex min-h-16 items-center gap-3 rounded-xl border border-line bg-surface px-4.5 dark:border-line-dark dark:bg-surface-dark"
			>
				<span class="font-mono text-sm font-medium text-ink dark:text-ink-dark">
					{status.node_id ? shortId(status.node_id) : 'this machine'}
				</span>
				<StatusPill tone={machineStatusTone(status)} class="ml-auto">
					{machineStatusLabel(status)}
				</StatusPill>
			</div>
			{#if status.own_addresses.length > 0}
				<div class="mb-6 flex flex-col gap-2">
					<p class="text-[13px] leading-normal text-muted dark:text-muted-dark">
						{#if hasShareableAddress(status.own_addresses)}
							This machine's sync address — paste it into "Connect a machine" on the other device to
							pair them manually.
						{:else}
							These addresses only work on this machine. They won't reach another device on the
							network.
						{/if}
					</p>
					{#each status.own_addresses as item (item.address)}
						{@const note = addressScopeNote(item.scope)}
						<div class="flex flex-col gap-1">
							<div class="flex items-center gap-2.5">
								<code
									class="flex h-12 min-w-0 flex-1 items-center overflow-hidden rounded-lg border border-line bg-surface px-4 font-mono text-xs text-ink dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark"
								>
									<span class="truncate">{item.address}</span>
								</code>
								<Button
									variant="secondary"
									class="flex-none"
									onclick={() => handleCopyAddress(item.address)}
								>
									{copiedAddress === item.address ? 'Copied' : 'Copy'}
								</Button>
							</div>
							{#if note}
								<p class="pl-1 text-[12px] text-muted dark:text-muted-dark">
									{note}
								</p>
							{/if}
						</div>
					{/each}
				</div>
			{/if}

			<SectionLabel class="mb-2.5">Other machines</SectionLabel>
			{#if rowError}
				<p class="mb-2 text-sm text-danger">{rowError}</p>
			{/if}
			{#if status.peers.length === 0}
				<p class="mb-4 text-[15px] text-muted dark:text-muted-dark">
					No other machines connected. Machines on the same network find each other on their own.
				</p>
			{:else}
				<div class="mb-4 flex flex-col gap-2">
					{#each status.peers as peer (peer.peer_id)}
						<div
							class="flex min-h-16 flex-wrap items-center gap-3 rounded-xl border border-line bg-surface px-4.5 py-2 dark:border-line-dark dark:bg-surface-dark"
						>
							<span class="font-mono text-sm font-medium text-ink dark:text-ink-dark">
								{shortId(peer.peer_id)}
							</span>
							<span class="truncate font-mono text-xs text-muted dark:text-muted-dark">
								{peer.address}
							</span>
							<StatusPill tone="accent" live class="ml-auto">Connected</StatusPill>
							<button
								type="button"
								onclick={() => handleDisconnect(peer)}
								class="flex-none text-sm font-medium text-danger hover:underline"
							>
								Disconnect
							</button>
						</div>
					{/each}
				</div>
			{/if}
			<Button class="mb-8" onclick={() => (connecting = true)} disabled={!status.running}>
				Connect a machine
			</Button>

			<SectionLabel class="mb-2.5">Conflicts</SectionLabel>
			{#if conflicts.length === 0}
				<p class="mb-8 text-[15px] text-muted dark:text-muted-dark">No conflicts.</p>
			{:else}
				<div class="mb-8 flex flex-col gap-4">
					{#each conflicts as conflict (conflict.id)}
						<div
							class="rounded-2xl border border-warn-line bg-surface px-6 py-5 dark:border-warn-line-dark dark:bg-surface-dark"
						>
							<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
								<p class="text-[15px] font-semibold text-ink dark:text-ink-dark">
									{conflict.entity_type}
									<span class="ml-1 font-mono text-xs font-normal text-muted dark:text-muted-dark">
										{shortId(conflict.entity_id)}
									</span>
								</p>
								<p class="text-[13px] text-muted dark:text-muted-dark">
									edited here and on {shortId(conflict.remote_node_id)}
								</p>
							</div>
							<div class="mb-4 overflow-x-auto">
								<table class="w-full text-left text-[13px]">
									<thead>
										<tr class="text-muted dark:text-muted-dark">
											<th class="pr-3 pb-1.5 font-normal">Field</th>
											<th class="pr-3 pb-1.5 font-normal">This machine</th>
											<th class="pb-1.5 font-normal">The other machine</th>
										</tr>
									</thead>
									<tbody>
										{#each conflictKeys(conflict) as key (key)}
											{@const localVal = conflict.local_snapshot[key]}
											{@const remoteVal = conflict.remote_snapshot[key]}
											{@const differs = JSON.stringify(localVal) !== JSON.stringify(remoteVal)}
											<tr class="border-t border-line dark:border-line-dark">
												<td class="py-1.5 pr-3 text-muted dark:text-muted-dark">{key}</td>
												<td
													class="py-1.5 pr-3 font-mono {differs
														? 'font-medium text-ink dark:text-ink-dark'
														: 'text-muted dark:text-muted-dark'}"
												>
													{displayValue(localVal)}
												</td>
												<td
													class="py-1.5 font-mono {differs
														? 'font-medium text-ink dark:text-ink-dark'
														: 'text-muted dark:text-muted-dark'}"
												>
													{displayValue(remoteVal)}
												</td>
											</tr>
										{/each}
									</tbody>
								</table>
							</div>
							<div class="flex flex-wrap justify-end gap-3">
								<Button
									variant="secondary"
									disabled={resolvingId === conflict.id}
									onclick={() => handleResolve(conflict, 'local')}
								>
									Keep this machine
								</Button>
								<Button
									disabled={resolvingId === conflict.id}
									onclick={() => handleResolve(conflict, 'remote')}
								>
									Keep the other machine
								</Button>
							</div>
						</div>
					{/each}
				</div>
			{/if}

			<details>
				<summary
					class="flex cursor-pointer items-center gap-2 text-[15px] font-medium text-ink dark:text-ink-dark"
				>
					<Icon name="chevron-right" class="h-4 w-4 text-muted dark:text-muted-dark" />
					Advanced
				</summary>
				<div class="mt-4 flex flex-col gap-5 pl-6">
					{#if coordinator && coordinator.running}
						<div class="flex flex-col gap-2">
							<span class="text-sm font-semibold text-ink dark:text-ink-dark">
								Scheduled work runs on
							</span>
							<p class="text-sm leading-normal text-muted dark:text-muted-dark">
								One machine owns schedules and other workspace-wide jobs so they don't run twice.
								Right now that's
								{coordinator.is_self
									? 'this machine'
									: coordinator.coordinator_id
										? shortId(coordinator.coordinator_id)
										: 'undecided'}.
								<span class="font-mono text-xs">
									(term {coordinator.term} · this machine's score {coordinator.self_score.toFixed(
										1
									)})
								</span>
							</p>
							{#if !coordinator.is_self}
								<Button
									variant="secondary"
									size="md"
									class="self-start"
									onclick={handleReclaim}
									disabled={reclaiming}
								>
									{reclaiming ? 'Taking over…' : 'Run scheduled work here'}
								</Button>
							{/if}
							{#if reclaimError}
								<p class="text-sm text-danger">{reclaimError}</p>
							{/if}
						</div>
					{/if}
					<form onsubmit={handleSetCapabilities} class="flex flex-col gap-2">
						<label
							class="text-sm font-semibold text-ink dark:text-ink-dark"
							for="sync-capabilities"
						>
							This machine's labels
						</label>
						<p class="text-[13px] text-muted dark:text-muted-dark">
							Agents with a preferred machine run where the matching label is advertised — e.g.
							"gpu".
						</p>
						<div class="flex gap-2.5">
							<input
								id="sync-capabilities"
								type="text"
								bind:value={capabilitiesDraft}
								placeholder="gpu, cpu-heavy"
								class="h-12 min-w-0 flex-1 rounded-lg border border-line bg-surface px-4 font-mono text-sm text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark"
							/>
							<Button
								variant="secondary"
								class="flex-none"
								type="submit"
								disabled={savingCapabilities}
							>
								{savingCapabilities ? 'Saving…' : 'Save'}
							</Button>
						</div>
						{#if capabilitiesError}
							<p class="text-sm text-danger">{capabilitiesError}</p>
						{/if}
					</form>
				</div>
			</details>
		{/if}
	</div>
{/if}

{#if connecting}
	<Sheet title="Connect a machine" onClose={() => (connecting = false)} width={480}>
		<form id="sync-connect-form" onsubmit={handleConnect} class="flex flex-col gap-2">
			<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="sync-address">
				Sync address
			</label>
			<p class="text-[13px] leading-normal text-muted dark:text-muted-dark">
				On the other machine, open Sync and copy its address, then paste it here.
			</p>
			<input
				id="sync-address"
				type="text"
				bind:value={connectAddress}
				placeholder="/ip4/…"
				class="h-12 rounded-lg border border-line bg-surface px-4 font-mono text-xs text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark"
			/>
			{#if connectError}
				<p class="text-sm text-danger">{connectError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (connecting = false)}>Cancel</Button>
			<Button
				disabled={connectBusy || !connectAddress.trim()}
				onclick={() =>
					(document.getElementById('sync-connect-form') as HTMLFormElement).requestSubmit()}
			>
				{connectBusy ? 'Connecting…' : 'Connect'}
			</Button>
		{/snippet}
	</Sheet>
{/if}
