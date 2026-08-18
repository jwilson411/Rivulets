<script lang="ts">
	import { page } from '$app/state';
	import { auth } from '$lib/api/auth.svelte';
	import { settings, type WorkspaceSettings } from '$lib/api/settings';
	import { dispatch, type HitRate } from '$lib/api/dispatch';
	import { update, type UpdateStatus } from '$lib/api/update';
	import { backups as backupsApi, type Backup, type BackupKind } from '$lib/api/backups';
	import { formatBytes, timeAgo } from '$lib/format';
	import { providers as providersApi, type Provider } from '$lib/api/providers';
	import {
		integrations as integrationsApi,
		type IntegrationAccount,
		type GoogleOAuthApp
	} from '$lib/api/integrations';
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
	import FolderPickerSheet from '$lib/components/FolderPickerSheet.svelte';
	import Button from '$lib/ui/Button.svelte';
	import FilterChip from '$lib/ui/FilterChip.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';

	// Settings (06-screens.md → Settings, mockup 1l): five tabs — Safety,
	// Spend, Files, Integrations, Updates & backups — in plain language
	// (09-copy-deck.md). Guests see spend status only (#351: everything
	// else on this page reads owner-only endpoints).

	type Tab = 'safety' | 'spend' | 'files' | 'integrations' | 'updates';
	const SETTINGS_TABS: Tab[] = ['safety', 'spend', 'files', 'integrations', 'updates'];

	function tabFromUrl(): Tab {
		const raw = page.url.searchParams.get('tab');
		return raw && (SETTINGS_TABS as string[]).includes(raw) ? (raw as Tab) : 'safety';
	}

	let tab = $state<Tab>(tabFromUrl());

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
	let workingDirectory = $state<string | null>(null);
	let pickingFolder = $state(false);

	let hitRate = $state<HitRate | null>(null);
	let providersList = $state<Provider[]>([]);

	let updateStatus = $state<UpdateStatus | null>(null);
	let updateError = $state<string | null>(null);
	let applying = $state(false);
	let applyError = $state<string | null>(null);
	let restarting = $state(false);

	let backupsList = $state<Backup[]>([]);
	let backupsError = $state<string | null>(null);
	let creatingBackup = $state(false);
	let createBackupError = $state<string | null>(null);
	let restoringBackup = $state<Backup | null>(null);
	let restoreConfirmText = $state('');
	let restoring = $state(false);
	let restoreError = $state<string | null>(null);
	let restoreSuccess = $state(false);

	let budgetCaps = $state<BudgetStatus[]>([]);
	let budgetsError = $state<string | null>(null);
	let budgetAgents = $state<Agent[]>([]);
	let budgetTeams = $state<Team[]>([]);
	let addingCap = $state(false);
	let newCapScope = $state<BudgetScope>('workspace');
	let newCapAgentId = $state('');
	let newCapTeamId = $state('');
	let newCapPeriod = $state<BudgetPeriod>('day');
	let newCapLimit = $state(10);
	let newCapAction = $state<BudgetAction>('alert');
	let creatingCap = $state(false);
	let createCapError = $state<string | null>(null);
	let overridingCapId = $state<string | null>(null);

	let googleApp = $state<GoogleOAuthApp | null>(null);
	let googleAccounts = $state<IntegrationAccount[]>([]);
	let integrationsError = $state<string | null>(null);
	let googleClientId = $state('');
	let googleClientSecret = $state('');
	let savingGoogleApp = $state(false);
	let connectingGoogle = $state(false);
	let disconnectingId = $state<string | null>(null);

	const isOwner = $derived(auth.grant === 'owner');

	const inputClass =
		'h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark';

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
			workingDirectory = loaded['tools.working_directory'];
		} catch {
			loadError = "Couldn't load settings.";
		}
	}

	async function refreshUpdateStatus() {
		updateError = null;
		try {
			updateStatus = await update.status();
		} catch {
			updateError = "Couldn't check for updates.";
		}
	}

	async function refreshBackups() {
		backupsError = null;
		try {
			backupsList = await backupsApi.list();
		} catch {
			backupsError = "Couldn't load backups.";
		}
	}

	async function refreshIntegrations() {
		integrationsError = null;
		try {
			const [app, accounts] = await Promise.all([
				integrationsApi.googleOAuthApp(),
				integrationsApi.list()
			]);
			googleApp = app;
			googleClientId = app.client_id;
			googleAccounts = accounts.filter((row) => row.provider === 'google');
		} catch {
			integrationsError = "Couldn't load integrations.";
		}
	}

	async function handleSaveGoogleApp() {
		savingGoogleApp = true;
		integrationsError = null;
		try {
			const body: { client_id: string; client_secret?: string } = {
				client_id: googleClientId.trim()
			};
			if (googleClientSecret.trim()) body.client_secret = googleClientSecret.trim();
			googleApp = await integrationsApi.saveGoogleOAuthApp(body);
			googleClientId = googleApp.client_id;
			googleClientSecret = '';
		} catch {
			integrationsError = "Couldn't save the Google OAuth client.";
		} finally {
			savingGoogleApp = false;
		}
	}

	async function handleConnectGoogle() {
		connectingGoogle = true;
		integrationsError = null;
		try {
			const { authorization_url } = await integrationsApi.connectGoogle();
			// Same-tab Google trip drops the memory-only JWT (#464). Park
			// it so return to ?tab=integrations is already signed in.
			auth.leaveForOAuth(authorization_url);
		} catch (err) {
			integrationsError = err instanceof Error ? err.message : "Couldn't start Google sign-in.";
			connectingGoogle = false;
		}
	}

	async function handleDisconnect(id: string) {
		disconnectingId = id;
		integrationsError = null;
		try {
			await integrationsApi.disconnect(id);
			await refreshIntegrations();
		} catch {
			integrationsError = "Couldn't disconnect that account.";
		} finally {
			disconnectingId = null;
		}
	}

	async function refreshBudgets() {
		budgetsError = null;
		try {
			budgetCaps = await budgetsApi.list();
		} catch {
			budgetsError = "Couldn't load spend caps.";
		}
	}

	// #351: GET /settings, /providers, /update, and /backups are all
	// OwnerGrant-only server-side -- firing them from an invite-grant
	// session could only ever collect a 403 apiece. Budgets are the
	// invite-safe subset (#232 left GET /budgets any-grant).
	if (auth.grant === 'owner') {
		refresh();
		providersApi
			.list()
			.then((list) => (providersList = list))
			.catch(() => {});
		dispatch
			.hitRate('week')
			.then((r) => (hitRate = r))
			.catch(() => {});
		refreshUpdateStatus();
		refreshBackups();
		refreshIntegrations();
	} else {
		tab = 'spend';
	}
	refreshBudgets();
	agentsApi
		.list()
		.then((list) => (budgetAgents = list))
		.catch(() => {});
	teamsApi
		.list()
		.then((list) => (budgetTeams = list))
		.catch(() => {});

	async function saveSettings(patch: Partial<WorkspaceSettings>) {
		if (Object.keys(patch).length === 0) return;
		saving = true;
		saveError = null;
		saved = false;
		try {
			loaded = await settings.update(patch);
			saved = true;
		} catch {
			saveError = "Couldn't save. Try again.";
		} finally {
			saving = false;
		}
	}

	async function handleSaveSafety(event: SubmitEvent) {
		event.preventDefault();
		if (!loaded) return;
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
		await saveSettings(patch);
	}

	async function saveWorkingDirectory(path: string | null) {
		pickingFolder = false;
		workingDirectory = path;
		await saveSettings({ 'tools.working_directory': path });
		if (loaded) workingDirectory = loaded['tools.working_directory'];
	}

	// Toggles save on flip — no separate Save step for a switch.
	async function toggleFiles(key: 'sync.eager_files_lan' | 'sync.eager_files_wan') {
		if (!loaded) return;
		const next = key === 'sync.eager_files_lan' ? !eagerFilesLan : !eagerFilesWan;
		if (key === 'sync.eager_files_lan') eagerFilesLan = next;
		else eagerFilesWan = next;
		await saveSettings({ [key]: next });
	}

	async function handleApplyUpdate() {
		applyError = null;
		applying = true;
		try {
			await update.apply();
			restarting = true;
		} catch {
			applyError = "Couldn't apply the update. Try again.";
		} finally {
			applying = false;
		}
	}

	async function handleCreateBackup() {
		createBackupError = null;
		creatingBackup = true;
		try {
			await backupsApi.create();
			await refreshBackups();
		} catch {
			createBackupError = "Couldn't create a backup. Try again.";
		} finally {
			creatingBackup = false;
		}
	}

	async function confirmRestore() {
		if (!restoringBackup) return;
		restoreError = null;
		restoring = true;
		try {
			await backupsApi.restore(restoringBackup.filename);
			restoringBackup = null;
			restoreConfirmText = '';
			restoreSuccess = true;
			await refreshBackups();
		} catch {
			restoreError = "Couldn't restore that backup. Try again.";
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
				return 'Before update';
			case 'pre-restore':
				return 'Before restore';
		}
	}

	async function handleCreateBudgetCap() {
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
			addingCap = false;
			await refreshBudgets();
		} catch {
			createCapError = "Couldn't add that cap. Try again.";
		} finally {
			creatingCap = false;
		}
	}

	async function handleDeleteBudgetCap(id: string) {
		try {
			await budgetsApi.remove(id);
			await refreshBudgets();
		} catch {
			budgetsError = "Couldn't delete that cap.";
		}
	}

	async function handleOverrideBudgetCap(id: string) {
		overridingCapId = id;
		try {
			await budgetsApi.override(id);
			await refreshBudgets();
		} catch {
			budgetsError = "Couldn't lift that cap.";
		} finally {
			overridingCapId = null;
		}
	}

	function budgetScopeLabel(cap: BudgetStatus): string {
		if (cap.scope_type === 'agent') {
			return budgetAgents.find((a) => a.id === cap.agent_id)?.name ?? 'A deleted agent';
		}
		if (cap.scope_type === 'team') {
			return budgetTeams.find((t) => t.id === cap.team_id)?.name ?? 'A deleted team';
		}
		return 'Whole workspace';
	}

	function formatPct(rate: number | null): string {
		return rate === null ? '—' : `${Math.round(rate * 100)}%`;
	}
</script>

{#snippet settingRow(title: string, body: string)}
	<span class="min-w-0">
		<span class="mb-0.5 block text-base font-semibold text-ink dark:text-ink-dark">{title}</span>
		<span class="block text-sm text-muted dark:text-muted-dark">{body}</span>
	</span>
{/snippet}

{#snippet toggle(on: boolean, onclick: () => void, label: string)}
	<button
		type="button"
		role="switch"
		aria-checked={on}
		aria-label={label}
		{onclick}
		class="relative ml-auto h-7 w-12 flex-none rounded-full transition-colors {on
			? 'bg-accent dark:bg-accent-dark'
			: 'bg-line dark:bg-line-dark'}"
	>
		<span
			class="absolute top-1 h-5 w-5 rounded-full bg-white transition-all {on ? 'left-6' : 'left-1'}"
		></span>
	</button>
{/snippet}

<div class="mx-auto max-w-[720px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
	<h1 class="mb-5 font-display text-[28px] font-semibold text-ink dark:text-ink-dark">Settings</h1>

	<div class="mb-7 flex flex-wrap gap-2">
		{#if isOwner}
			<FilterChip selected={tab === 'safety'} onclick={() => (tab = 'safety')}>Safety</FilterChip>
		{/if}
		<FilterChip selected={tab === 'spend'} onclick={() => (tab = 'spend')}>Spend</FilterChip>
		{#if isOwner}
			<FilterChip selected={tab === 'files'} onclick={() => (tab = 'files')}>Files</FilterChip>
			<FilterChip selected={tab === 'integrations'} onclick={() => (tab = 'integrations')}>
				Integrations
			</FilterChip>
			<FilterChip selected={tab === 'updates'} onclick={() => (tab = 'updates')}>
				Updates & backups
			</FilterChip>
		{/if}
	</div>

	{#if !isOwner && tab !== 'spend'}
		<p class="py-6 text-center text-base text-muted dark:text-muted-dark">
			This is only available to the workspace owner.
		</p>
	{:else if tab === 'safety'}
		{#if loadError}
			<p class="text-sm text-danger">{loadError}</p>
		{:else if !loaded}
			<div class="breath h-4 w-1/2 rounded-full bg-line dark:bg-line-dark"></div>
		{:else}
			<form onsubmit={handleSaveSafety} class="flex flex-col gap-4">
				<div class="mb-1 font-display text-lg font-semibold text-ink dark:text-ink-dark">
					How conversations stop
				</div>
				<div
					class="flex min-h-16 items-center gap-4 rounded-2xl border border-line bg-surface px-6 py-5 dark:border-line-dark dark:bg-surface-dark"
				>
					{@render settingRow(
						'Max replies in a row',
						'Agents pause after this many turns without a person.'
					)}
					<input
						type="number"
						min="1"
						max="100"
						bind:value={turnLimit}
						aria-label="Max replies in a row"
						class="{inputClass} ml-auto w-22 flex-none text-center"
					/>
				</div>
				<div
					class="flex min-h-16 flex-wrap items-center gap-4 rounded-2xl border border-line bg-surface px-6 py-5 dark:border-line-dark dark:bg-surface-dark"
				>
					{@render settingRow(
						'Same two agents looping',
						`Pause when the same pair trades ${cycleThreshold} replies within a window of ${cycleWindow}.`
					)}
					<span class="ml-auto flex flex-none gap-2">
						<input
							type="number"
							min="4"
							max="20"
							bind:value={cycleWindow}
							aria-label="Cycle window"
							class="{inputClass} w-18 text-center"
						/>
						<input
							type="number"
							min="2"
							max="20"
							bind:value={cycleThreshold}
							aria-label="Cycle threshold"
							class="{inputClass} w-18 text-center"
						/>
					</span>
				</div>
				<div
					class="flex min-h-16 items-center gap-4 rounded-2xl border border-line bg-surface px-6 py-5 dark:border-line-dark dark:bg-surface-dark"
				>
					{@render settingRow(
						'Pause after this much quiet',
						'A conversation goes idle when nobody replies. Minutes.'
					)}
					<input
						type="number"
						min="5"
						max="1440"
						bind:value={timeoutMinutes}
						aria-label="Pause after this many minutes of quiet"
						class="{inputClass} ml-auto w-24 flex-none text-center"
					/>
				</div>

				<details class="mt-2">
					<summary
						class="flex cursor-pointer items-center gap-2 text-[15px] font-medium text-ink dark:text-ink-dark"
					>
						<Icon name="chevron-right" class="h-4 w-4 text-muted dark:text-muted-dark" />
						Advanced — routing model
					</summary>
					<div class="mt-4 flex flex-col gap-3 pl-6">
						<p class="text-sm leading-normal text-muted dark:text-muted-dark">
							The model that decides which agent answers. Leave unselected to auto-pick from your
							providers.
						</p>
						<ModelPicker providers={providersList} bind:value={modelOverride} showAuto={false} />
						{#if hitRate && hitRate.total_decisions > 0}
							<p class="text-sm text-muted dark:text-muted-dark">
								This week, {formatPct(hitRate.hit_rate)} of routing decisions were made without calling
								a model ({hitRate.total_decisions} decisions).
							</p>
							{#if hitRate.fallback_warning}
								<p
									class="rounded-xl border border-warn-line bg-warn-soft px-4 py-3 text-sm leading-normal text-warn-ink dark:border-warn-line-dark dark:bg-warn-soft-dark dark:text-warn-ink-dark"
								>
									Over half of routing decisions this week needed a model, which costs more.
									Tightening agent roles or adding "when to speak" rules routes more messages
									directly.
								</p>
							{/if}
						{/if}
					</div>
				</details>

				<div class="mt-2 flex items-center gap-4">
					<Button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
					{#if saved}
						<p class="text-sm text-accent dark:text-accent-dark">Saved.</p>
					{/if}
					{#if saveError}
						<p class="text-sm text-danger">{saveError}</p>
					{/if}
				</div>
			</form>
		{/if}
	{:else if tab === 'spend'}
		<div class="mb-4 flex items-center justify-between">
			<div class="font-display text-lg font-semibold text-ink dark:text-ink-dark">Spend caps</div>
			{#if isOwner}
				<Button onclick={() => (addingCap = true)}>Add a cap</Button>
			{/if}
		</div>
		{#if budgetsError}
			<p class="mb-3 text-sm text-danger">{budgetsError}</p>
		{/if}
		{#if budgetCaps.length === 0}
			<p class="py-6 text-center text-base text-muted dark:text-muted-dark">
				No spend caps yet. A cap watches spend per day, week, or month.
			</p>
		{:else}
			<div class="flex flex-col gap-3">
				{#each budgetCaps as cap (cap.id)}
					{@const pct = Math.min(100, Math.round((cap.spend_usd / cap.limit_usd) * 100))}
					<div
						class="rounded-2xl border border-line bg-surface px-6 py-5 dark:border-line-dark dark:bg-surface-dark"
					>
						<div class="mb-2 flex flex-wrap items-center gap-3">
							<span class="text-base font-semibold text-ink dark:text-ink-dark">
								{budgetScopeLabel(cap)}
							</span>
							<span class="ml-auto text-sm text-muted dark:text-muted-dark">
								{cap.action === 'hard_stop' ? 'Stops when hit' : 'Notifies, keeps going'} · per {cap.period}
							</span>
						</div>
						<div class="mb-2 h-2.5 overflow-hidden rounded-full bg-line dark:bg-line-dark">
							<div
								class="h-full rounded-full {cap.blocked
									? 'bg-danger'
									: 'bg-accent dark:bg-accent-dark'}"
								style="width: {pct}%"
							></div>
						</div>
						<div class="flex flex-wrap items-center gap-3 text-sm">
							<span class="text-muted dark:text-muted-dark">
								${cap.spend_usd.toFixed(2)} of ${cap.limit_usd.toFixed(2)}
								{#if cap.unpriced_run_count > 0}
									· {cap.unpriced_run_count} run{cap.unpriced_run_count === 1 ? '' : 's'} with no price
									on file
								{/if}
							</span>
							{#if isOwner}
								<span class="ml-auto flex items-center gap-3.5">
									{#if cap.blocked}
										<button
											type="button"
											onclick={() => handleOverrideBudgetCap(cap.id)}
											disabled={overridingCapId === cap.id}
											class="font-semibold text-accent hover:underline disabled:opacity-50 dark:text-accent-dark"
										>
											{overridingCapId === cap.id ? 'Lifting…' : 'Lift for now'}
										</button>
									{/if}
									<button
										type="button"
										onclick={() => handleDeleteBudgetCap(cap.id)}
										class="font-medium text-danger hover:underline"
									>
										Delete
									</button>
								</span>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		{/if}
	{:else if tab === 'files'}
		{#if loadError}
			<p class="text-sm text-danger">{loadError}</p>
		{:else if !loaded}
			<div class="breath h-4 w-1/2 rounded-full bg-line dark:bg-line-dark"></div>
		{:else}
			<div class="flex flex-col gap-4">
				<div
					class="flex flex-col gap-4 rounded-2xl border border-line bg-surface px-6 py-5 dark:border-line-dark dark:bg-surface-dark"
				>
					{@render settingRow(
						'Default project folder',
						'Agents read and write here when they build and work, unless a channel picks its own folder. A conversation can override the channel without changing it.'
					)}
					{#if workingDirectory}
						<p
							class="truncate font-mono text-[13px] text-ink dark:text-ink-dark"
							title={workingDirectory}
						>
							{workingDirectory}
						</p>
					{:else}
						<p class="text-sm text-muted dark:text-muted-dark">
							Using the built-in sandbox until you pick a folder.
						</p>
					{/if}
					<div class="flex flex-wrap items-center gap-3">
						<Button variant="secondary" size="md" onclick={() => (pickingFolder = true)}>
							{workingDirectory ? 'Change folder' : 'Choose folder'}
						</Button>
						{#if workingDirectory}
							<button
								type="button"
								onclick={() => saveWorkingDirectory(null)}
								class="text-sm font-semibold text-accent hover:underline dark:text-accent-dark"
							>
								Use built-in sandbox
							</button>
						{/if}
					</div>
				</div>
				<div
					class="flex min-h-16 items-center gap-4 rounded-2xl border border-line bg-surface px-6 py-5 dark:border-line-dark dark:bg-surface-dark"
				>
					{@render settingRow(
						'Copy files to other machines on this network',
						'Attachments are pushed to nearby machines right away, so they open instantly there.'
					)}
					{@render toggle(
						eagerFilesLan,
						() => toggleFiles('sync.eager_files_lan'),
						'Copy files to other machines on this network'
					)}
				</div>
				<div
					class="flex min-h-16 items-center gap-4 rounded-2xl border border-line bg-surface px-6 py-5 dark:border-line-dark dark:bg-surface-dark"
				>
					{@render settingRow(
						'Copy files across the internet',
						'Can use real bandwidth on slow or metered connections. Off means far machines fetch a file only when they need it.'
					)}
					{@render toggle(
						eagerFilesWan,
						() => toggleFiles('sync.eager_files_wan'),
						'Copy files across the internet'
					)}
				</div>
				{#if saveError}
					<p class="text-sm text-danger">{saveError}</p>
				{/if}
			</div>
		{/if}
	{:else if tab === 'integrations'}
		<div class="flex flex-col gap-6">
			<section class="flex flex-col gap-3">
				<div class="font-display text-lg font-semibold text-ink dark:text-ink-dark">Google</div>
				<p class="max-w-[60ch] text-sm leading-normal text-muted dark:text-muted-dark">
					Connect a Google account so assigned agents can read Gmail and list Calendar. Sending mail
					and creating events stay extra-gated. Tokens live in this machine's credential store, not
					the workspace database.
				</p>
				<p class="max-w-[60ch] text-sm leading-normal text-muted dark:text-muted-dark">
					Create an OAuth client in Google Cloud (Desktop app) and add this redirect URI:
					<span class="font-mono text-[13px] text-ink dark:text-ink-dark"
						>{googleApp?.redirect_uri ??
							'http://127.0.0.1:8484/api/v1/integrations/google/callback'}</span
					>
				</p>
				<div
					class="flex flex-col gap-4 rounded-2xl border border-line bg-surface px-6 py-5 dark:border-line-dark dark:bg-surface-dark"
				>
					<label class="flex flex-col gap-1.5">
						<span class="text-sm font-semibold text-ink dark:text-ink-dark">Client ID</span>
						<input
							type="text"
							bind:value={googleClientId}
							autocomplete="off"
							aria-label="Google OAuth client ID"
							class={inputClass}
						/>
					</label>
					<label class="flex flex-col gap-1.5">
						<span class="text-sm font-semibold text-ink dark:text-ink-dark">
							Client secret
							{#if googleApp?.has_client_secret}
								<span class="font-normal text-muted dark:text-muted-dark"> (already saved)</span>
							{/if}
						</span>
						<input
							type="password"
							bind:value={googleClientSecret}
							autocomplete="new-password"
							placeholder={googleApp?.has_client_secret
								? 'Leave blank to keep the saved secret'
								: 'Optional for Desktop clients'}
							aria-label="Google OAuth client secret"
							class={inputClass}
						/>
					</label>
					<Button
						class="self-start"
						size="md"
						onclick={handleSaveGoogleApp}
						disabled={savingGoogleApp || !googleClientId.trim()}
					>
						{savingGoogleApp ? 'Saving…' : 'Save client'}
					</Button>
				</div>
			</section>

			<section class="flex flex-col gap-3">
				<div class="font-display text-lg font-semibold text-ink dark:text-ink-dark">
					Connected accounts
				</div>
				<Button
					class="self-start"
					onclick={handleConnectGoogle}
					disabled={connectingGoogle || !googleApp?.client_id}
				>
					{connectingGoogle ? 'Opening Google…' : 'Connect Google account'}
				</Button>
				{#if googleAccounts.length === 0}
					<p class="text-sm text-muted dark:text-muted-dark">No Google account connected yet.</p>
				{:else}
					<div class="flex flex-col gap-2">
						{#each googleAccounts as account (account.id)}
							<div
								class="flex min-h-14 flex-wrap items-center gap-3 rounded-xl border border-line bg-surface px-4.5 py-2 dark:border-line-dark dark:bg-surface-dark"
							>
								<span class="min-w-0 truncate text-base text-ink dark:text-ink-dark">
									{account.account_email ?? account.label}
								</span>
								<span class="text-[13px] text-muted dark:text-muted-dark">{account.status}</span>
								<button
									type="button"
									onclick={() => handleDisconnect(account.id)}
									disabled={disconnectingId === account.id}
									class="ml-auto text-sm font-medium text-danger hover:underline disabled:opacity-50"
								>
									{disconnectingId === account.id ? 'Disconnecting…' : 'Disconnect'}
								</button>
							</div>
						{/each}
					</div>
				{/if}
			</section>
			{#if integrationsError}
				<p class="text-sm text-danger">{integrationsError}</p>
			{/if}
		</div>
	{:else if tab === 'updates'}
		<div class="flex flex-col gap-8">
			<section class="flex flex-col gap-3">
				<div class="font-display text-lg font-semibold text-ink dark:text-ink-dark">Updates</div>
				{#if updateError}
					<p class="text-sm text-danger">{updateError}</p>
				{:else if !updateStatus}
					<div class="breath h-4 w-1/2 rounded-full bg-line dark:bg-line-dark"></div>
				{:else}
					<p class="text-base text-ink dark:text-ink-dark">
						You're on <span class="font-mono text-sm">{updateStatus.current_version}</span
						>{updateStatus.update_available ? '' : " — that's the latest."}
					</p>
					{#if restarting}
						<p class="text-[15px] text-accent dark:text-accent-dark">
							Restarting into {updateStatus.latest_version}… reload this page in a few seconds.
						</p>
					{:else if updateStatus.update_available}
						{#if updateStatus.applicable}
							<Button class="self-start" onclick={handleApplyUpdate} disabled={applying}>
								{applying ? 'Updating…' : `Update to ${updateStatus.latest_version}`}
							</Button>
							{#if applyError}
								<p class="text-sm text-danger">{applyError}</p>
							{/if}
						{:else}
							<p class="text-sm text-muted dark:text-muted-dark">
								<span class="font-mono">{updateStatus.latest_version}</span> is out, but this install
								updates outside the app — pull the new image or tag.
							</p>
						{/if}
					{/if}
					{#if !restarting}
						<button
							type="button"
							onclick={refreshUpdateStatus}
							class="self-start text-sm font-semibold text-accent hover:underline dark:text-accent-dark"
						>
							Check again
						</button>
					{/if}
				{/if}
			</section>

			<section class="flex flex-col gap-3">
				<div class="font-display text-lg font-semibold text-ink dark:text-ink-dark">Backups</div>
				<p class="max-w-[60ch] text-sm leading-normal text-muted dark:text-muted-dark">
					A snapshot of this machine's workspace. One is taken daily, and again before any update or
					restore. File attachments aren't included — cover those with your own backups.
				</p>
				<Button class="self-start" onclick={handleCreateBackup} disabled={creatingBackup}>
					{creatingBackup ? 'Backing up…' : 'Back up now'}
				</Button>
				{#if createBackupError}
					<p class="text-sm text-danger">{createBackupError}</p>
				{/if}
				{#if restoreSuccess}
					<p class="text-[15px] text-accent dark:text-accent-dark">
						Restored. Reload the page to see the restored workspace.
					</p>
				{/if}
				{#if backupsError}
					<p class="text-sm text-danger">{backupsError}</p>
				{:else if backupsList.length > 0}
					<div class="flex flex-col gap-2">
						{#each backupsList as b (b.filename)}
							<div
								class="flex min-h-14 flex-wrap items-center gap-3 rounded-xl border border-line bg-surface px-4.5 py-2 dark:border-line-dark dark:bg-surface-dark"
							>
								<span class="min-w-0 truncate font-mono text-[13px] text-ink dark:text-ink-dark">
									{b.filename}
								</span>
								<span class="text-[13px] text-muted dark:text-muted-dark">
									{backupKindLabel(b.kind)} · {formatBytes(b.size_bytes)} · {timeAgo(b.created_at)}
								</span>
								<button
									type="button"
									onclick={() => {
										restoringBackup = b;
										restoreConfirmText = '';
										restoreError = null;
										restoreSuccess = false;
									}}
									class="ml-auto text-sm font-semibold text-accent hover:underline dark:text-accent-dark"
								>
									Restore
								</button>
							</div>
						{/each}
					</div>
				{:else}
					<p class="text-sm text-muted dark:text-muted-dark">No backups yet.</p>
				{/if}
			</section>
		</div>
	{/if}
</div>

{#if pickingFolder}
	<FolderPickerSheet
		initialPath={workingDirectory}
		onClose={() => (pickingFolder = false)}
		onSelect={(path) => saveWorkingDirectory(path)}
	/>
{/if}

{#if addingCap}
	<Sheet title="Add a cap" onClose={() => (addingCap = false)} width={480}>
		<div class="flex flex-col gap-4">
			<div class="flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="cap-scope">
					Watch spend for
				</label>
				<select id="cap-scope" bind:value={newCapScope} class={inputClass}>
					<option value="workspace">Whole workspace</option>
					<option value="team">One team</option>
					<option value="agent">One agent</option>
				</select>
			</div>
			{#if newCapScope === 'agent'}
				<select bind:value={newCapAgentId} aria-label="Agent" class={inputClass}>
					<option value="" disabled>Choose an agent…</option>
					{#each budgetAgents as agent (agent.id)}
						<option value={agent.id}>{agent.name}</option>
					{/each}
				</select>
			{:else if newCapScope === 'team'}
				<select bind:value={newCapTeamId} aria-label="Team" class={inputClass}>
					<option value="" disabled>Choose a team…</option>
					{#each budgetTeams as team (team.id)}
						<option value={team.id}>{team.name}</option>
					{/each}
				</select>
			{/if}
			<div class="grid grid-cols-2 gap-4">
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="cap-period">
						Per
					</label>
					<select id="cap-period" bind:value={newCapPeriod} class={inputClass}>
						<option value="day">Day</option>
						<option value="week">Week</option>
						<option value="month">Month</option>
					</select>
				</div>
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="cap-limit">
						Cap (USD)
					</label>
					<input
						id="cap-limit"
						type="number"
						min="0.01"
						step="0.01"
						bind:value={newCapLimit}
						class={inputClass}
					/>
				</div>
			</div>
			<div class="flex flex-col gap-2">
				<span class="text-sm font-semibold text-ink dark:text-ink-dark">When it's hit</span>
				<label
					class="flex h-12 cursor-pointer items-center gap-3 rounded-lg border px-4 {newCapAction ===
					'hard_stop'
						? 'border-accent bg-accent-soft dark:border-accent-dark dark:bg-accent-soft-dark'
						: 'border-line dark:border-line-dark'}"
				>
					<input
						type="radio"
						name="cap-action"
						value="hard_stop"
						bind:group={newCapAction}
						class="accent-(--color-accent)"
					/>
					<span class="text-[15px] text-ink dark:text-ink-dark">Stop when the cap is hit</span>
				</label>
				<label
					class="flex h-12 cursor-pointer items-center gap-3 rounded-lg border px-4 {newCapAction ===
					'alert'
						? 'border-accent bg-accent-soft dark:border-accent-dark dark:bg-accent-soft-dark'
						: 'border-line dark:border-line-dark'}"
				>
					<input
						type="radio"
						name="cap-action"
						value="alert"
						bind:group={newCapAction}
						class="accent-(--color-accent)"
					/>
					<span class="text-[15px] text-ink dark:text-ink-dark">Notify me, keep going</span>
				</label>
			</div>
			{#if createCapError}
				<p class="text-sm text-danger">{createCapError}</p>
			{/if}
		</div>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (addingCap = false)}>Cancel</Button>
			<Button
				disabled={creatingCap ||
					(newCapScope === 'agent' && !newCapAgentId) ||
					(newCapScope === 'team' && !newCapTeamId)}
				onclick={handleCreateBudgetCap}
			>
				{creatingCap ? 'Adding…' : 'Add cap'}
			</Button>
		{/snippet}
	</Sheet>
{/if}

{#if restoringBackup}
	<Sheet title="Restore this backup?" onClose={() => (restoringBackup = null)} width={480}>
		<p class="text-base leading-normal text-ink dark:text-ink-dark">
			Type the backup name to restore. This replaces the current workspace — anything created since
			the snapshot was taken will be gone. A safety backup is taken first, so this is undoable.
		</p>
		<div class="flex flex-col gap-2">
			<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="restore-confirm">
				Backup name
			</label>
			<input
				id="restore-confirm"
				type="text"
				bind:value={restoreConfirmText}
				placeholder={restoringBackup.filename}
				class="{inputClass} font-mono text-[13px]"
			/>
		</div>
		{#if restoreError}
			<p class="text-sm text-danger">{restoreError}</p>
		{/if}
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (restoringBackup = null)}>Cancel</Button>
			<Button
				variant="destructive"
				disabled={restoring || restoreConfirmText !== restoringBackup!.filename}
				onclick={confirmRestore}
			>
				{restoring ? 'Restoring…' : 'Restore'}
			</Button>
		{/snippet}
	</Sheet>
{/if}
