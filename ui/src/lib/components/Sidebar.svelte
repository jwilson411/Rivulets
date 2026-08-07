<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { auth } from '$lib/api/auth.svelte';
	import { channels, type Channel } from '$lib/api/channels';
	import { agents } from '$lib/api/agents';
	import { teams } from '$lib/api/teams';
	import { providers } from '$lib/api/providers';
	import { mcpServers } from '$lib/api/mcpServers';
	import { tools } from '$lib/api/tools';
	import { sync } from '$lib/api/sync';
	import { theme, type ThemePreference } from '$lib/theme.svelte';
	import Icon from './Icon.svelte';

	const themeOptions: { value: ThemePreference; label: string; icon: string }[] = [
		{ value: 'light', label: 'Light', icon: 'sun' },
		{ value: 'dark', label: 'Dark', icon: 'moon' },
		{ value: 'system', label: 'System', icon: 'monitor' }
	];

	let channelList = $state<Channel[]>([]);
	let newChannelName = $state('');
	let loadError = $state<string | null>(null);
	let createError = $state<string | null>(null);

	let agentCount = $state<number | null>(null);
	let teamCount = $state<number | null>(null);
	let providerCount = $state<number | null>(null);
	let mcpServerCount = $state<number | null>(null);
	let toolCount = $state<number | null>(null);
	let syncPeerCount = $state<number | null>(null);
	let syncRunning = $state(false);

	async function refresh() {
		loadError = null;
		try {
			channelList = await channels.list();
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load channels';
		}
	}

	async function refreshCounts() {
		try {
			const [agentList, teamList, providerList, mcpServerList, toolList, syncStatus] =
				await Promise.all([
					agents.list(),
					teams.list(),
					providers.list(),
					mcpServers.list(),
					tools.list(),
					sync.status()
				]);
			agentCount = agentList.length;
			teamCount = teamList.length;
			providerCount = providerList.length;
			mcpServerCount = mcpServerList.length;
			toolCount = toolList.length;
			syncRunning = syncStatus.running;
			syncPeerCount = syncStatus.peers.filter((p) => p.connected).length;
		} catch {
			// Sidebar counts are a convenience, not load-bearing — leave them
			// blank rather than surfacing a second error UI alongside `loadError`.
		}
	}

	refresh();
	refreshCounts();

	async function handleCreateChannel(event: SubmitEvent) {
		event.preventDefault();
		if (!newChannelName.trim()) return;
		createError = null;
		try {
			await channels.create(newChannelName.trim());
			newChannelName = '';
			await refresh();
		} catch (err) {
			createError = err instanceof Error ? err.message : 'Failed to create channel';
		}
	}

	function isActive(path: string): boolean {
		return page.url.pathname === path || page.url.pathname.startsWith(path + '/');
	}

	function navClass(active: boolean): string {
		return active
			? 'bg-agent-cyan-100 font-semibold text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400'
			: 'text-ink hover:bg-neutral-200/60 dark:text-ink-dark dark:hover:bg-white/5';
	}

	// Workspace-management links (issue #43) -- collapsed by default so the
	// channel list, the thing actually used constantly, dominates the
	// sidebar. "Sync" isn't in this group: its ambient status readout at the
	// bottom of the sidebar already covers it (now a link to /sync).
	const WORKSPACE_ROUTES = [
		'/agents',
		'/teams',
		'/providers',
		'/mcp-servers',
		'/tools',
		'/usage',
		'/settings'
	];
	const WORKSPACE_EXPANDED_KEY = 'rivulets-sidebar-workspace-expanded';

	function isOnWorkspaceRoute(): boolean {
		return WORKSPACE_ROUTES.some((path) => isActive(path));
	}

	function readStoredWorkspaceExpanded(): boolean | null {
		if (typeof localStorage === 'undefined') return null;
		const stored = localStorage.getItem(WORKSPACE_EXPANDED_KEY);
		if (stored === 'true') return true;
		if (stored === 'false') return false;
		return null;
	}

	let workspaceExpanded = $state(readStoredWorkspaceExpanded() ?? isOnWorkspaceRoute());
	const workspaceActive = $derived(isOnWorkspaceRoute());

	// Landing on a workspace route always reveals it, even if the user's
	// stored preference (or a manual collapse on a previous page) was
	// closed -- the active item must stay reachable. This only ever opens
	// the section: it doesn't depend on `workspaceExpanded`, so manually
	// collapsing it again while staying on the same route isn't fought.
	$effect(() => {
		if (isOnWorkspaceRoute()) workspaceExpanded = true;
	});

	function toggleWorkspace() {
		workspaceExpanded = !workspaceExpanded;
		localStorage.setItem(WORKSPACE_EXPANDED_KEY, String(workspaceExpanded));
	}
</script>

<nav
	class="flex h-full w-60 flex-col border-r border-ink/10 bg-paper pt-5 pb-4 dark:border-white/10 dark:bg-paper-dark"
>
	<div class="px-6">
		<a href={resolve('/')} class="text-xl font-semibold tracking-tight text-ink dark:text-ink-dark">
			Rivulets<span class="text-agent-cyan-600 dark:text-agent-cyan-400">.</span>
		</a>
		<p class="mt-0.5 mb-2.5 font-mono text-[11px] text-neutral-600 dark:text-neutral-400">
			{page.url.host} · unlocked
		</p>
		<div class="border-t-[3px] border-ink dark:border-ink-dark"></div>
		<div class="mt-0.5 border-t border-ink dark:border-ink-dark"></div>
	</div>

	<div class="mt-4 px-4">
		<button
			type="button"
			onclick={toggleWorkspace}
			aria-expanded={workspaceExpanded}
			aria-controls="workspace-links"
			class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13.5px] {workspaceActive
				? 'font-semibold text-agent-cyan-700 dark:text-agent-cyan-400'
				: 'text-ink hover:bg-neutral-200/60 dark:text-ink-dark dark:hover:bg-white/5'}"
		>
			<Icon
				name="chevron"
				class="h-[11px] w-[11px] flex-none text-neutral-500 transition-transform duration-150 {workspaceExpanded
					? 'rotate-90'
					: ''}"
			/>
			Workspace
		</button>
		{#if workspaceExpanded}
			<div id="workspace-links" class="mt-0.5 flex flex-col gap-0.5">
				<a
					href={resolve('/agents')}
					class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13.5px] {navClass(
						isActive('/agents')
					)}"
				>
					<Icon
						name="robot"
						class="h-[17px] w-[17px] flex-none text-neutral-600 dark:text-neutral-400"
					/>
					Agents
					{#if agentCount !== null}<span class="ml-auto text-[11.5px] text-neutral-500"
							>{agentCount}</span
						>{/if}
				</a>
				<a
					href={resolve('/teams')}
					class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13.5px] {navClass(
						isActive('/teams')
					)}"
				>
					<Icon
						name="users"
						class="h-[17px] w-[17px] flex-none text-neutral-600 dark:text-neutral-400"
					/>
					Teams
					{#if teamCount !== null}<span class="ml-auto text-[11.5px] text-neutral-500"
							>{teamCount}</span
						>{/if}
				</a>
				<a
					href={resolve('/providers')}
					class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13.5px] {navClass(
						isActive('/providers')
					)}"
				>
					<Icon
						name="plug"
						class="h-[17px] w-[17px] flex-none text-neutral-600 dark:text-neutral-400"
					/>
					Providers
					{#if providerCount !== null}<span class="ml-auto text-[11.5px] text-neutral-500"
							>{providerCount}</span
						>{/if}
				</a>
				<a
					href={resolve('/mcp-servers')}
					class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13.5px] {navClass(
						isActive('/mcp-servers')
					)}"
				>
					<Icon
						name="server"
						class="h-[17px] w-[17px] flex-none text-neutral-600 dark:text-neutral-400"
					/>
					MCP Servers
					{#if mcpServerCount !== null}<span class="ml-auto text-[11.5px] text-neutral-500"
							>{mcpServerCount}</span
						>{/if}
				</a>
				<a
					href={resolve('/tools')}
					class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13.5px] {navClass(
						isActive('/tools')
					)}"
				>
					<Icon
						name="wrench"
						class="h-[17px] w-[17px] flex-none text-neutral-600 dark:text-neutral-400"
					/>
					Tools
					{#if toolCount !== null}<span class="ml-auto text-[11.5px] text-neutral-500"
							>{toolCount}</span
						>{/if}
				</a>
				<a
					href={resolve('/usage')}
					class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13.5px] {navClass(
						isActive('/usage')
					)}"
				>
					<Icon
						name="chart"
						class="h-[17px] w-[17px] flex-none text-neutral-600 dark:text-neutral-400"
					/>
					Usage
				</a>
				<a
					href={resolve('/settings')}
					class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13.5px] {navClass(
						isActive('/settings')
					)}"
				>
					<Icon
						name="settings"
						class="h-[17px] w-[17px] flex-none text-neutral-600 dark:text-neutral-400"
					/>
					Settings
				</a>
			</div>
		{/if}
	</div>

	<div
		class="mt-6 mb-2 px-6 text-[11px] font-semibold tracking-wide text-neutral-600 uppercase dark:text-neutral-400"
	>
		Channels
	</div>

	<div class="flex-1 overflow-y-auto px-4 py-1">
		{#if loadError}
			<p class="px-2 text-xs text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
		{:else}
			{#each channelList as channel (channel.id)}
				<a
					href={resolve('/channels/[id]', { id: channel.id })}
					class="block truncate rounded-md px-2 py-1.5 text-[13.5px] {navClass(
						isActive('/channels/' + channel.id)
					)}"
				>
					<span class="text-neutral-500">#</span>{channel.name}
				</a>
			{/each}
		{/if}
	</div>

	<form
		onsubmit={handleCreateChannel}
		class="flex flex-col gap-1 border-t border-ink/10 p-4 dark:border-white/10"
	>
		<div class="flex gap-1">
			<input
				type="text"
				bind:value={newChannelName}
				placeholder="new-channel"
				class="min-w-0 flex-1 rounded-md border border-ink/15 bg-transparent px-2 py-1 text-xs text-ink focus:outline-none dark:border-white/15 dark:text-ink-dark"
			/>
			<button
				type="submit"
				class="rounded-md bg-agent-cyan px-2 py-1 text-xs font-semibold text-white hover:bg-agent-cyan-600"
			>
				Add
			</button>
		</div>
		{#if createError}
			<p class="px-1 text-xs text-agent-magenta-700 dark:text-agent-magenta-400">{createError}</p>
		{/if}
	</form>

	<a
		href={resolve('/sync')}
		class="flex items-center gap-2 border-t border-ink/10 px-4 pt-3 text-[12px] {isActive('/sync')
			? 'text-agent-cyan-700 dark:text-agent-cyan-400'
			: 'text-neutral-600 hover:text-ink dark:text-neutral-400 dark:hover:text-ink-dark'}"
	>
		<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
			<circle
				cx="7"
				cy="7"
				r="5.5"
				fill="none"
				stroke={syncPeerCount ? 'var(--color-agent-cyan)' : 'currentColor'}
				stroke-width="1.2"
			/>
			<path
				d="M7 0 V4 M7 10 V14 M0 7 H4 M10 7 H14"
				stroke={syncPeerCount ? 'var(--color-agent-cyan)' : 'currentColor'}
				stroke-width="1.2"
			/>
		</svg>
		{#if syncPeerCount === null}
			checking sync…
		{:else}
			{syncPeerCount} peer{syncPeerCount === 1 ? '' : 's'} · {syncRunning ? 'in register' : 'idle'}
		{/if}
	</a>

	<div class="px-4 pt-2">
		<div
			role="group"
			aria-label="Theme"
			class="flex rounded-md border border-ink/15 p-0.5 dark:border-white/15"
		>
			{#each themeOptions as option (option.value)}
				<button
					type="button"
					title={option.label}
					aria-pressed={theme.preference === option.value}
					onclick={() => theme.set(option.value)}
					class="flex flex-1 items-center justify-center rounded-[3px] py-1 {theme.preference ===
					option.value
						? 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400'
						: 'text-neutral-600 hover:bg-neutral-200/60 dark:text-neutral-400 dark:hover:bg-white/5'}"
				>
					<Icon name={option.icon} class="h-[15px] w-[15px]" />
					<span class="sr-only">{option.label}</span>
				</button>
			{/each}
		</div>
	</div>

	<div class="px-4 pt-2">
		<button
			onclick={() => auth.logout()}
			class="w-full rounded-md px-2 py-1.5 text-left text-sm text-neutral-600 hover:bg-neutral-200/60 dark:text-neutral-400 dark:hover:bg-white/5"
		>
			Sign out
		</button>
	</div>
</nav>
