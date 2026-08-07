<script lang="ts">
	import { settings, type WorkspaceSettings } from '$lib/api/settings';
	import { dispatch, type HitRate } from '$lib/api/dispatch';

	let loaded = $state<WorkspaceSettings | null>(null);
	let loadError = $state<string | null>(null);
	let saveError = $state<string | null>(null);
	let saved = $state(false);
	let saving = $state(false);

	let turnLimit = $state(10);
	let cycleWindow = $state(8);
	let timeoutMinutes = $state(30);
	let modelOverride = $state('');
	let eagerFilesLan = $state(true);
	let eagerFilesWan = $state(false);

	let hitRate = $state<HitRate | null>(null);
	let hitRateError = $state<string | null>(null);

	async function refresh() {
		loadError = null;
		try {
			loaded = await settings.get();
			turnLimit = loaded['guard.turn_limit'];
			cycleWindow = loaded['guard.cycle_window'];
			timeoutMinutes = loaded['guard.timeout_minutes'];
			modelOverride = loaded['dispatcher.model_override'] ?? '';
			eagerFilesLan = loaded['sync.eager_files_lan'];
			eagerFilesWan = loaded['sync.eager_files_wan'];
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load settings';
		}
	}

	async function refreshHitRate() {
		hitRateError = null;
		try {
			hitRate = await dispatch.hitRate('week');
		} catch (err) {
			hitRateError = err instanceof Error ? err.message : 'Failed to load dispatcher hit rate';
		}
	}

	refresh();
	refreshHitRate();

	function formatPct(rate: number | null): string {
		return rate === null ? '—' : `${Math.round(rate * 100)}%`;
	}

	async function handleSave(event: SubmitEvent) {
		event.preventDefault();
		if (!loaded) return;
		saveError = null;
		saved = false;

		const patch: Partial<WorkspaceSettings> = {};
		const trimmedOverride = modelOverride.trim() || null;
		if (turnLimit !== loaded['guard.turn_limit']) patch['guard.turn_limit'] = turnLimit;
		if (cycleWindow !== loaded['guard.cycle_window']) patch['guard.cycle_window'] = cycleWindow;
		if (timeoutMinutes !== loaded['guard.timeout_minutes'])
			patch['guard.timeout_minutes'] = timeoutMinutes;
		if (trimmedOverride !== loaded['dispatcher.model_override'])
			patch['dispatcher.model_override'] = trimmedOverride;
		if (eagerFilesLan !== loaded['sync.eager_files_lan'])
			patch['sync.eager_files_lan'] = eagerFilesLan;
		if (eagerFilesWan !== loaded['sync.eager_files_wan'])
			patch['sync.eager_files_wan'] = eagerFilesWan;

		if (Object.keys(patch).length === 0) return;

		saving = true;
		try {
			loaded = await settings.update(patch);
			saved = true;
		} catch (err) {
			saveError = err instanceof Error ? err.message : 'Failed to save settings';
		} finally {
			saving = false;
		}
	}
</script>

<div class="mx-auto flex max-w-2xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">Settings</h1>
		<p class="text-sm text-neutral-600 dark:text-neutral-400">
			Workspace-wide policy — synced to every peer (FR-9.1), except the local UI port.
		</p>
	</header>

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{:else if !loaded}
		<p class="text-sm text-neutral-500 italic">Loading…</p>
	{:else}
		<form onsubmit={handleSave} class="flex flex-col gap-8">
			<section
				class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
			>
				<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Guardrails</h2>
				<p class="text-xs text-neutral-600 dark:text-neutral-400">
					Loop-prevention thresholds (FR-7.4) — the same on every node.
				</p>

				<label class="flex flex-col gap-1 text-sm text-ink dark:text-ink-dark">
					Turn limit <span class="text-xs text-neutral-500">(1-100 messages per rivulet)</span>
					<input
						type="number"
						min="1"
						max="100"
						bind:value={turnLimit}
						class="w-32 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
					/>
				</label>

				<label class="flex flex-col gap-1 text-sm text-ink dark:text-ink-dark">
					Cycle detection window
					<span class="text-xs text-neutral-500">(4-20 messages looked back for repeats)</span>
					<input
						type="number"
						min="4"
						max="20"
						bind:value={cycleWindow}
						class="w-32 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
					/>
				</label>

				<label class="flex flex-col gap-1 text-sm text-ink dark:text-ink-dark">
					Time-based pause <span class="text-xs text-neutral-500">(5-1440 minutes)</span>
					<input
						type="number"
						min="5"
						max="1440"
						bind:value={timeoutMinutes}
						class="w-32 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
					/>
				</label>
			</section>

			<section
				class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
			>
				<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Dispatcher</h2>
				<p class="text-xs text-neutral-600 dark:text-neutral-400">
					Override the model the dispatcher uses to route messages (OQ-2). Leave blank to let it
					auto-pick from your configured providers.
				</p>
				<input
					type="text"
					bind:value={modelOverride}
					placeholder="provider:model_name (e.g. anthropic:claude-3-5-haiku-latest)"
					class="rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
				/>

				<div class="mt-2 flex flex-col gap-2 border-t border-ink/10 pt-3 dark:border-white/10">
					<h3 class="text-xs font-medium text-ink dark:text-ink-dark">
						Hit rate <span class="text-neutral-500">(last 7 days)</span>
					</h3>
					{#if hitRateError}
						<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
							{hitRateError}
						</p>
					{:else if !hitRate}
						<p class="text-xs text-neutral-500 italic">Loading…</p>
					{:else if hitRate.total_decisions === 0}
						<p class="text-xs text-neutral-600 dark:text-neutral-400">
							No dispatcher activity yet this week.
						</p>
					{:else}
						<div
							class="flex flex-wrap items-center gap-4 text-xs text-neutral-600 dark:text-neutral-400"
						>
							<span>
								Resolved without the LLM fallback:
								<span class="font-medium text-ink dark:text-ink-dark"
									>{formatPct(hitRate.hit_rate)}</span
								>
								<span class="text-neutral-500">(goal &gt;80%)</span>
							</span>
							<span>
								Fallback rate:
								<span class="font-medium text-ink dark:text-ink-dark"
									>{formatPct(hitRate.fallback_rate)}</span
								>
							</span>
							<span class="text-neutral-500">{hitRate.total_decisions} decisions</span>
						</div>
						{#if hitRate.fallback_warning}
							<p
								class="rounded-md border border-agent-magenta-700/30 bg-agent-magenta-700/10 px-3 py-2 text-xs text-agent-magenta-700 dark:border-agent-magenta-400/30 dark:text-agent-magenta-400"
							>
								Over half of routing decisions this week fell back to the LLM router — this is
								costing more than expected. Consider tightening agent descriptions or adding manual
								routing rules (FR-3.3/FR-3.4) to route more messages deterministically.
							</p>
						{/if}
					{/if}
				</div>
			</section>

			<section
				class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
			>
				<h2 class="text-sm font-medium text-ink dark:text-ink-dark">File sync policy</h2>
				<p class="text-xs text-neutral-600 dark:text-neutral-400">
					Eager sync pushes file attachments to peers immediately; lazy sync waits until a peer asks
					for them (OQ-5). Set independently per network.
				</p>
				<label class="flex items-center gap-2 text-sm text-ink dark:text-ink-dark">
					<input type="checkbox" bind:checked={eagerFilesLan} class="h-4 w-4" />
					Eager sync on LAN
				</label>
				<label class="flex items-center gap-2 text-sm text-ink dark:text-ink-dark">
					<input type="checkbox" bind:checked={eagerFilesWan} class="h-4 w-4" />
					Eager sync on WAN
				</label>
			</section>

			<div class="flex items-center gap-3">
				<button
					type="submit"
					disabled={saving}
					class="self-start rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
				>
					{saving ? 'Saving…' : 'Save changes'}
				</button>
				{#if saved}
					<p class="text-sm text-agent-cyan-700 dark:text-agent-cyan-400">Saved.</p>
				{/if}
				{#if saveError}
					<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{saveError}</p>
				{/if}
			</div>
		</form>
	{/if}
</div>
