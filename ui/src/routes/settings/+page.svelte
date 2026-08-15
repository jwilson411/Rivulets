<script lang="ts">
	import { auth } from '$lib/api/auth.svelte';
	import { settings, type WorkspaceSettings } from '$lib/api/settings';
	import { dispatch, type HitRate } from '$lib/api/dispatch';
	import { update, type UpdateStatus } from '$lib/api/update';
	import { backups as backupsApi, type Backup, type BackupKind } from '$lib/api/backups';
	import { formatBytes, timeAgo } from '$lib/format';
	import { providers as providersApi, type Provider } from '$lib/api/providers';
	import {
		budgets as budgetsApi,
		type BudgetStatus,
		type BudgetScope,
		type BudgetPeriod,
		type BudgetAction
	} from '$lib/api/budgets';
	import { agents as agentsApi, type Agent } from '$lib/api/agents';
	import { teams as teamsApi, type Team } from '$lib/api/teams';
	import ModelPicker from '$lib/components/ModelPicker.svelte';

	let loaded = $state<WorkspaceSettings | null>(null);
	let loadError = $state<string | null>(null);
	let saveError = $state<string | null>(null);
	let saved = $state(false);
	let saving = $state(false);

	let turnLimit = $state(10);
	let cycleWindow = $state(8);
	let cycleThreshold = $state(3);
	let timeoutMinutes = $state(30);
	let modelOverride = $state('');
	let eagerFilesLan = $state(true);
	let eagerFilesWan = $state(false);

	let hitRate = $state<HitRate | null>(null);
	let hitRateError = $state<string | null>(null);

	let providersList = $state<Provider[]>([]);
	let providersError = $state<string | null>(null);

	let updateStatus = $state<UpdateStatus | null>(null);
	let updateError = $state<string | null>(null);
	let applying = $state(false);
	let applyError = $state<string | null>(null);
	let restarting = $state(false);

	let backupsList = $state<Backup[]>([]);
	let backupsError = $state<string | null>(null);
	let creatingBackup = $state(false);
	let createBackupError = $state<string | null>(null);
	let restoringFilename = $state<string | null>(null);
	let restoreConfirmText = $state('');
	let restoring = $state(false);
	let restoreError = $state<string | null>(null);
	let restoreSuccess = $state(false);

	let budgetCaps = $state<BudgetStatus[]>([]);
	let budgetsError = $state<string | null>(null);
	let budgetAgents = $state<Agent[]>([]);
	let budgetTeams = $state<Team[]>([]);
	let newCapScope = $state<BudgetScope>('workspace');
	let newCapAgentId = $state('');
	let newCapTeamId = $state('');
	let newCapPeriod = $state<BudgetPeriod>('day');
	let newCapLimit = $state(10);
	let newCapAction = $state<BudgetAction>('alert');
	let creatingCap = $state(false);
	let createCapError = $state<string | null>(null);
	let overridingCapId = $state<string | null>(null);

	async function refresh() {
		loadError = null;
		try {
			loaded = await settings.get();
			turnLimit = loaded['guard.turn_limit'];
			cycleWindow = loaded['guard.cycle_window'];
			cycleThreshold = loaded['guard.cycle_threshold'];
			timeoutMinutes = loaded['guard.timeout_minutes'];
			modelOverride = loaded['dispatcher.model_override'] ?? '';
			eagerFilesLan = loaded['sync.eager_files_lan'];
			eagerFilesWan = loaded['sync.eager_files_wan'];
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load settings';
		}
	}

	async function refreshProviders() {
		providersError = null;
		try {
			providersList = await providersApi.list();
		} catch (err) {
			providersError = err instanceof Error ? err.message : 'Failed to load providers';
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

	async function refreshUpdateStatus() {
		updateError = null;
		try {
			updateStatus = await update.status();
		} catch (err) {
			updateError = err instanceof Error ? err.message : 'Failed to check for updates';
		}
	}

	async function handleApplyUpdate() {
		applyError = null;
		applying = true;
		try {
			await update.apply();
			restarting = true;
		} catch (err) {
			applyError = err instanceof Error ? err.message : 'Failed to apply update';
		} finally {
			applying = false;
		}
	}

	async function refreshBackups() {
		backupsError = null;
		try {
			backupsList = await backupsApi.list();
		} catch (err) {
			backupsError = err instanceof Error ? err.message : 'Failed to load backups';
		}
	}

	async function handleCreateBackup() {
		createBackupError = null;
		creatingBackup = true;
		try {
			await backupsApi.create();
			await refreshBackups();
		} catch (err) {
			createBackupError = err instanceof Error ? err.message : 'Failed to create backup';
		} finally {
			creatingBackup = false;
		}
	}

	function startRestore(filename: string) {
		restoringFilename = filename;
		restoreConfirmText = '';
		restoreError = null;
		restoreSuccess = false;
	}

	function cancelRestore() {
		restoringFilename = null;
		restoreConfirmText = '';
		restoreError = null;
	}

	async function confirmRestore() {
		if (!restoringFilename) return;
		restoreError = null;
		restoring = true;
		try {
			await backupsApi.restore(restoringFilename);
			restoringFilename = null;
			restoreConfirmText = '';
			restoreSuccess = true;
			await refreshBackups();
		} catch (err) {
			restoreError = err instanceof Error ? err.message : 'Failed to restore backup';
		} finally {
			restoring = false;
		}
	}

	function backupKindLabel(kind: BackupKind): string {
		switch (kind) {
			case 'manual':
				return 'Manual';
			case 'daily':
				return 'Daily';
			case 'pre-upgrade':
				return 'Pre-upgrade';
			case 'pre-restore':
				return 'Pre-restore';
		}
	}

	async function refreshBudgets() {
		budgetsError = null;
		try {
			budgetCaps = await budgetsApi.list();
		} catch (err) {
			budgetsError = err instanceof Error ? err.message : 'Failed to load budget caps';
		}
	}

	async function refreshBudgetPickerOptions() {
		try {
			[budgetAgents, budgetTeams] = await Promise.all([agentsApi.list(), teamsApi.list()]);
		} catch {
			// Non-fatal: the create form's agent/team pickers just stay empty.
		}
	}

	async function handleCreateBudgetCap(event: SubmitEvent) {
		event.preventDefault();
		createCapError = null;
		creatingCap = true;
		try {
			await budgetsApi.create({
				scope_type: newCapScope,
				agent_id: newCapScope === 'agent' ? newCapAgentId || null : null,
				team_id: newCapScope === 'team' ? newCapTeamId || null : null,
				period: newCapPeriod,
				limit_usd: newCapLimit,
				action: newCapAction
			});
			await refreshBudgets();
		} catch (err) {
			createCapError = err instanceof Error ? err.message : 'Failed to create budget cap';
		} finally {
			creatingCap = false;
		}
	}

	async function handleDeleteBudgetCap(id: string) {
		try {
			await budgetsApi.remove(id);
			await refreshBudgets();
		} catch (err) {
			budgetsError = err instanceof Error ? err.message : 'Failed to delete budget cap';
		}
	}

	async function handleOverrideBudgetCap(id: string) {
		overridingCapId = id;
		try {
			await budgetsApi.override(id);
			await refreshBudgets();
		} catch (err) {
			budgetsError = err instanceof Error ? err.message : 'Failed to override budget cap';
		} finally {
			overridingCapId = null;
		}
	}

	function budgetScopeLabel(cap: BudgetStatus): string {
		if (cap.scope_type === 'agent') {
			return budgetAgents.find((a) => a.id === cap.agent_id)?.name ?? 'Agent (deleted)';
		}
		if (cap.scope_type === 'team') {
			return budgetTeams.find((t) => t.id === cap.team_id)?.name ?? 'Team (deleted)';
		}
		return 'Whole workspace';
	}

	refresh();
	refreshProviders();
	refreshHitRate();
	refreshUpdateStatus();
	refreshBackups();
	refreshBudgets();
	refreshBudgetPickerOptions();

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
		if (cycleThreshold !== loaded['guard.cycle_threshold'])
			patch['guard.cycle_threshold'] = cycleThreshold;
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

	<section
		class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
	>
		<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Updates</h2>
		<p class="text-xs text-neutral-600 dark:text-neutral-400">
			This machine only — not synced to peers. Each install checks GitHub Releases independently.
		</p>

		{#if updateError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{updateError}</p>
		{:else if !updateStatus}
			<p class="text-sm text-neutral-500 italic">Checking for updates…</p>
		{:else}
			<p class="text-sm text-ink dark:text-ink-dark">
				Current version <span class="font-mono">{updateStatus.current_version}</span>
			</p>

			{#if restarting}
				<p class="text-sm text-agent-cyan-700 dark:text-agent-cyan-400">
					Restarting to {updateStatus.latest_version}… reload this page in a few seconds.
				</p>
			{:else if updateStatus.update_available}
				<p class="text-sm text-ink dark:text-ink-dark">
					<span class="font-mono">{updateStatus.latest_version}</span> is available.
				</p>
				{#if updateStatus.applicable}
					<button
						type="button"
						onclick={handleApplyUpdate}
						disabled={applying}
						class="self-start rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
					>
						{applying ? 'Updating…' : `Update to ${updateStatus.latest_version}`}
					</button>
					{#if applyError}
						<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{applyError}</p>
					{/if}
				{:else}
					<p class="text-xs text-neutral-600 dark:text-neutral-400">
						Auto-update isn't available in this environment. Docker users should pull the new image;
						a source/dev install should pull the new tag.
					</p>
				{/if}
			{:else}
				<p class="text-sm text-neutral-600 dark:text-neutral-400">You're up to date.</p>
			{/if}

			{#if !restarting}
				<button
					type="button"
					onclick={refreshUpdateStatus}
					class="self-start text-xs text-agent-cyan-700 hover:underline dark:text-agent-cyan-400"
				>
					Check for updates
				</button>
			{/if}
		{/if}
	</section>

	<section
		class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
	>
		<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Backups</h2>
		<p class="text-xs text-neutral-600 dark:text-neutral-400">
			Node-local snapshots (#243) of the app database, the provider-key fallback store, this node's
			sync identity, and custom tool source. One is taken automatically once a day, and another
			right before any restore below, so a restore is itself always undoable. File attachments in <code
				class="font-mono">files/</code
			> aren't included — cover those with your own filesystem backups.
		</p>

		{#if backupsError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{backupsError}</p>
		{/if}

		{#if auth.grant === 'owner'}
			<button
				type="button"
				onclick={handleCreateBackup}
				disabled={creatingBackup}
				class="self-start rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
			>
				{creatingBackup ? 'Backing up…' : 'Back up now'}
			</button>
			{#if createBackupError}
				<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">
					{createBackupError}
				</p>
			{/if}

			{#if restoreSuccess}
				<p class="text-sm text-agent-cyan-700 dark:text-agent-cyan-400">
					Restored. Reload the page to see the restored workspace.
				</p>
			{/if}
		{/if}

		{#if backupsList.length > 0}
			<ul class="flex flex-col gap-2">
				{#each backupsList as b (b.filename)}
					<li class="rounded-md border border-ink/10 p-3 text-sm dark:border-white/10">
						<div class="flex flex-wrap items-center justify-between gap-2">
							<span class="font-mono text-xs text-ink dark:text-ink-dark">{b.filename}</span>
							<span class="text-xs text-neutral-500">
								{backupKindLabel(b.kind)} · {formatBytes(b.size_bytes)} · {timeAgo(b.created_at)}
							</span>
						</div>

						{#if restoringFilename === b.filename}
							<div
								class="mt-2 flex flex-col gap-2 border-t border-ink/10 pt-2 dark:border-white/10"
							>
								<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
									This replaces the live workspace with this snapshot — anything created since it
									was taken will be gone. Type the filename to confirm.
								</p>
								<label class="flex flex-col gap-1 text-xs text-ink dark:text-ink-dark">
									Filename
									<input
										type="text"
										bind:value={restoreConfirmText}
										placeholder={b.filename}
										class="rounded-md border border-ink/15 bg-transparent px-2 py-1.5 font-mono text-xs text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
									/>
								</label>
								<div class="flex items-center gap-3">
									<button
										type="button"
										onclick={confirmRestore}
										disabled={restoring || restoreConfirmText !== b.filename}
										class="rounded-md bg-agent-magenta-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-agent-magenta-700 disabled:opacity-50"
									>
										{restoring ? 'Restoring…' : 'Confirm restore'}
									</button>
									<button
										type="button"
										onclick={cancelRestore}
										class="text-xs text-neutral-600 hover:underline dark:text-neutral-400"
									>
										Cancel
									</button>
								</div>
								{#if restoreError}
									<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
										{restoreError}
									</p>
								{/if}
							</div>
						{:else}
							<button
								type="button"
								onclick={() => startRestore(b.filename)}
								class="mt-1 text-xs text-agent-cyan-700 hover:underline dark:text-agent-cyan-400"
							>
								Restore
							</button>
						{/if}
					</li>
				{/each}
			</ul>
		{:else if !backupsError}
			<p class="text-xs text-neutral-500 italic">No backups yet.</p>
		{/if}
	</section>

	<section
		class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
	>
		<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Budgets</h2>
		<p class="text-xs text-neutral-600 dark:text-neutral-400">
			Spend caps per agent, team, or the whole workspace (#97). Cap definitions sync to every peer;
			the spend shown against each one is only what this node itself has recorded — see the
			workspace-scope note below.
		</p>

		{#if budgetsError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{budgetsError}</p>
		{/if}

		{#if budgetCaps.length > 0}
			<ul class="flex flex-col gap-2">
				{#each budgetCaps as cap (cap.id)}
					{@const pct = Math.min(100, Math.round((cap.spend_usd / cap.limit_usd) * 100))}
					<li class="rounded-md border border-ink/10 p-3 text-sm dark:border-white/10">
						<div class="flex flex-wrap items-center justify-between gap-2">
							<span class="font-medium text-ink dark:text-ink-dark">{budgetScopeLabel(cap)}</span>
							<span class="text-xs text-neutral-500">
								{cap.action === 'hard_stop' ? 'Hard stop' : 'Alert only'} · per {cap.period}
							</span>
						</div>
						<div class="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-ink/10 dark:bg-white/10">
							<div
								class="h-full rounded-full {cap.blocked ? 'bg-agent-magenta-600' : 'bg-agent-cyan'}"
								style="width: {pct}%"
							></div>
						</div>
						<div class="mt-1 flex flex-wrap items-center justify-between gap-2 text-xs">
							<span class="text-neutral-600 dark:text-neutral-400">
								${cap.spend_usd.toFixed(2)} / ${cap.limit_usd.toFixed(2)}
								{#if cap.unpriced_run_count > 0}
									<span class="text-neutral-500">
										· {cap.unpriced_run_count} unpriced run{cap.unpriced_run_count === 1 ? '' : 's'} not
										counted
									</span>
								{/if}
							</span>
							<span class="flex items-center gap-2">
								{#if auth.grant === 'owner'}
									{#if cap.blocked}
										<button
											type="button"
											onclick={() => handleOverrideBudgetCap(cap.id)}
											disabled={overridingCapId === cap.id}
											class="text-agent-cyan-700 hover:underline disabled:opacity-50 dark:text-agent-cyan-400"
										>
											{overridingCapId === cap.id ? 'Overriding…' : 'Override'}
										</button>
									{/if}
									<button
										type="button"
										onclick={() => handleDeleteBudgetCap(cap.id)}
										class="text-agent-magenta-700 hover:underline dark:text-agent-magenta-400"
									>
										Delete
									</button>
								{/if}
							</span>
						</div>
					</li>
				{/each}
			</ul>
		{:else if !budgetsError}
			<p class="text-xs text-neutral-500 italic">No budget caps configured yet.</p>
		{/if}

		{#if auth.grant === 'owner'}
			<form
				onsubmit={handleCreateBudgetCap}
				class="mt-2 flex flex-wrap items-end gap-3 border-t border-ink/10 pt-3 dark:border-white/10"
			>
				<label class="flex flex-col gap-1 text-xs text-ink dark:text-ink-dark">
					Scope
					<select
						bind:value={newCapScope}
						class="rounded-md border border-ink/15 bg-transparent px-2 py-1.5 text-sm dark:border-white/15"
					>
						<option value="workspace">Whole workspace</option>
						<option value="team">Team</option>
						<option value="agent">Agent</option>
					</select>
				</label>

				{#if newCapScope === 'agent'}
					<label class="flex flex-col gap-1 text-xs text-ink dark:text-ink-dark">
						Agent
						<select
							bind:value={newCapAgentId}
							class="rounded-md border border-ink/15 bg-transparent px-2 py-1.5 text-sm dark:border-white/15"
						>
							<option value="" disabled>Select an agent</option>
							{#each budgetAgents as agent (agent.id)}
								<option value={agent.id}>{agent.name}</option>
							{/each}
						</select>
					</label>
				{:else if newCapScope === 'team'}
					<label class="flex flex-col gap-1 text-xs text-ink dark:text-ink-dark">
						Team
						<select
							bind:value={newCapTeamId}
							class="rounded-md border border-ink/15 bg-transparent px-2 py-1.5 text-sm dark:border-white/15"
						>
							<option value="" disabled>Select a team</option>
							{#each budgetTeams as team (team.id)}
								<option value={team.id}>{team.name}</option>
							{/each}
						</select>
					</label>
				{/if}

				<label class="flex flex-col gap-1 text-xs text-ink dark:text-ink-dark">
					Period
					<select
						bind:value={newCapPeriod}
						class="rounded-md border border-ink/15 bg-transparent px-2 py-1.5 text-sm dark:border-white/15"
					>
						<option value="day">Day</option>
						<option value="week">Week</option>
						<option value="month">Month</option>
					</select>
				</label>

				<label class="flex flex-col gap-1 text-xs text-ink dark:text-ink-dark">
					Limit (USD)
					<input
						type="number"
						min="0.01"
						step="0.01"
						bind:value={newCapLimit}
						class="w-28 rounded-md border border-ink/15 bg-transparent px-2 py-1.5 text-sm dark:border-white/15"
					/>
				</label>

				<label class="flex flex-col gap-1 text-xs text-ink dark:text-ink-dark">
					On breach
					<select
						bind:value={newCapAction}
						class="rounded-md border border-ink/15 bg-transparent px-2 py-1.5 text-sm dark:border-white/15"
					>
						<option value="alert">Alert only</option>
						<option value="hard_stop">Hard stop</option>
					</select>
				</label>

				<button
					type="submit"
					disabled={creatingCap ||
						(newCapScope === 'agent' && !newCapAgentId) ||
						(newCapScope === 'team' && !newCapTeamId)}
					class="rounded-md bg-agent-cyan px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
				>
					{creatingCap ? 'Adding…' : 'Add cap'}
				</button>
			</form>
			{#if createCapError}
				<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{createCapError}</p>
			{/if}
		{/if}
	</section>

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
					Loop-prevention thresholds (FR-7.4) — the same on every node. The defaults below work well
					for most workspaces; only change these if agents are looping unexpectedly or you want
					tighter or looser limits.
				</p>

				<label class="flex flex-col gap-1 text-sm text-ink dark:text-ink-dark">
					Turn limit
					<span class="text-xs text-neutral-600 dark:text-neutral-400">
						Pause an agent conversation for a human to check in once it's exchanged this many
						messages, whether or not anything's gone wrong.
					</span>
					<input
						type="number"
						min="1"
						max="100"
						bind:value={turnLimit}
						class="w-32 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
					/>
					<span class="text-xs text-neutral-500">1-100 messages per rivulet</span>
				</label>

				<label class="flex flex-col gap-1 text-sm text-ink dark:text-ink-dark">
					Cycle detection window
					<span class="text-xs text-neutral-600 dark:text-neutral-400">
						How many recent messages to check for the same two agents replying back and forth to
						each other.
					</span>
					<input
						type="number"
						min="4"
						max="20"
						bind:value={cycleWindow}
						class="w-32 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
					/>
					<span class="text-xs text-neutral-500">4-20 messages looked back for repeats</span>
				</label>

				<label class="flex flex-col gap-1 text-sm text-ink dark:text-ink-dark">
					Cycle threshold
					<span class="text-xs text-neutral-600 dark:text-neutral-400">
						How many times the same two agents have to reply to each other within that window before
						it's treated as a loop and paused for a human.
					</span>
					<input
						type="number"
						min="2"
						max="20"
						bind:value={cycleThreshold}
						class="w-32 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
					/>
					<span class="text-xs text-neutral-500">2-20 repeats within the cycle window</span>
				</label>

				<label class="flex flex-col gap-1 text-sm text-ink dark:text-ink-dark">
					Time-based pause
					<span class="text-xs text-neutral-600 dark:text-neutral-400">
						Pause an agent conversation automatically if it's been running this long without a human
						weighing in, even if neither limit above has been hit.
					</span>
					<input
						type="number"
						min="5"
						max="1440"
						bind:value={timeoutMinutes}
						class="w-32 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
					/>
					<span class="text-xs text-neutral-500">5-1440 minutes</span>
				</label>
			</section>

			<section
				class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
			>
				<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Dispatcher</h2>
				<p class="text-xs text-neutral-600 dark:text-neutral-400">
					Override the model the dispatcher uses to route messages (OQ-2). Leave unselected to let
					it auto-pick from your configured providers.
				</p>
				{#if providersError}
					<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">
						{providersError}
					</p>
				{/if}
				<ModelPicker providers={providersList} bind:value={modelOverride} showAuto={false} />

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
				<label
					class="flex items-center gap-2 text-sm text-ink dark:text-ink-dark"
					title="LAN = peers your node discovered on your local network (mDNS). On: file attachments are pushed to LAN peers as soon as you upload/attach them. Off: LAN peers only fetch a file's bytes the moment they actually need it."
				>
					<input type="checkbox" bind:checked={eagerFilesLan} class="h-4 w-4" />
					Eager sync on LAN
				</label>
				<label
					class="flex items-center gap-2 text-sm text-ink dark:text-ink-dark"
					title="WAN = peers connected over the internet rather than your local network. On: file attachments are pushed to WAN peers immediately, which can use significant bandwidth on a slow or metered connection. Off (default): WAN peers only fetch a file's bytes when they actually need it."
				>
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
