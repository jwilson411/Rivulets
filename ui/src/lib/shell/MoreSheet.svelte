<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { auth } from '$lib/api/auth.svelte';
	import { agents } from '$lib/api/agents';
	import { teams } from '$lib/api/teams';
	import { mcpServers } from '$lib/api/mcpServers';
	import { SETTINGS_INTEGRATIONS_HREF } from '$lib/toolCatalog';
	import { theme, type ThemePreference } from '$lib/theme.svelte';
	import { initials } from '$lib/ink';
	import Sheet from '$lib/ui/Sheet.svelte';
	import SectionLabel from '$lib/ui/SectionLabel.svelte';
	import SearchJumpButton from '$lib/shell/SearchJumpButton.svelte';
	import Icon from '$lib/ui/Icon.svelte';

	// The "More" sheet (mockup 2c): grouped cards with large type, 64px
	// rows. Guests never see Providers, Integrations, Sync, or Invites —
	// hidden, not disabled (2q); their Settings row is spend status only.
	// #417: phone chrome hides IconRail (and its avatar menu), so Account
	// lives here — theme, switch name, sign out — rather than only on md+.
	let {
		onClose,
		onOpenPalette
	}: {
		onClose: () => void;
		onOpenPalette: () => void;
	} = $props();

	let agentCount = $state<number | null>(null);
	let teamCount = $state<number | null>(null);
	let mcpConnected = $state(false);

	Promise.all([agents.list(), teams.list(), mcpServers.list()])
		.then(([agentList, teamList, mcpList]) => {
			agentCount = agentList.length;
			teamCount = teamList.length;
			mcpConnected = mcpList.some((s) => s.connected);
		})
		.catch(() => {
			// Counts are a convenience — the rows still navigate without them.
		});

	function open(path: string) {
		onClose();
		const [pathname, search = ''] = path.split('?');
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- pathname is resolved; the rule can't see through the search-string concat
		goto(resolve(pathname as '/') + (search ? `?${search}` : ''));
	}

	const isOwner = $derived(auth.grant === 'owner');

	const themeOptions: { value: ThemePreference; label: string; icon: string }[] = [
		{ value: 'light', label: 'Light', icon: 'sun' },
		{ value: 'dark', label: 'Dark', icon: 'moon' },
		{ value: 'system', label: 'System', icon: 'monitor' }
	];
</script>

{#snippet row(label: string, path: string, hint: string | null)}
	<button
		type="button"
		onclick={() => open(path)}
		class="flex h-16 w-full items-center gap-3 rounded-xl border border-line px-4.5 text-left text-[17px] font-semibold text-ink hover:border-accent dark:border-line-dark dark:text-ink-dark dark:hover:border-accent-dark"
	>
		{label}
		{#if hint}
			<span class="ml-auto text-sm font-normal text-muted dark:text-muted-dark">{hint}</span>
		{/if}
	</button>
{/snippet}

<Sheet title="More" {onClose} width={640}>
	<SearchJumpButton
		class="mb-6"
		onOpen={() => {
			onClose();
			onOpenPalette();
		}}
	/>
	<div class="grid grid-cols-1 gap-6 sm:grid-cols-2">
		<div class="flex flex-col gap-2 sm:col-span-2">
			<SectionLabel>Account</SectionLabel>
			<div
				class="flex h-16 items-center gap-3 rounded-xl border border-line px-4.5 dark:border-line-dark"
			>
				<span
					class="flex h-9 w-9 flex-none items-center justify-center rounded-[12px] bg-ink text-sm font-semibold text-paper dark:bg-ink-dark dark:text-paper-dark"
				>
					{initials(auth.displayName ?? '?')}
				</span>
				<div class="min-w-0">
					<div class="truncate text-[17px] font-semibold text-ink dark:text-ink-dark">
						{auth.displayName}
					</div>
					<div class="text-sm font-normal text-muted capitalize dark:text-muted-dark">
						{isOwner ? 'Owner' : 'Guest'}
					</div>
				</div>
			</div>
			<div
				role="group"
				aria-label="Theme"
				class="flex h-16 rounded-xl border border-line p-1 dark:border-line-dark"
			>
				{#each themeOptions as option (option.value)}
					<button
						type="button"
						title={option.label}
						aria-pressed={theme.preference === option.value}
						onclick={() => theme.set(option.value)}
						class="flex flex-1 items-center justify-center gap-1.5 rounded-lg text-[15px] font-medium {theme.preference ===
						option.value
							? 'bg-accent-soft text-accent dark:bg-accent-soft-dark dark:text-accent-dark'
							: 'text-muted hover:bg-paper dark:text-muted-dark dark:hover:bg-paper-dark'}"
					>
						<Icon name={option.icon} class="h-4 w-4" />
						{option.label}
					</button>
				{/each}
			</div>
			{#if isOwner}
				<button
					type="button"
					onclick={() => {
						onClose();
						auth.clearIdentity();
					}}
					class="flex h-16 w-full items-center rounded-xl border border-line px-4.5 text-left text-[17px] font-semibold text-ink hover:border-accent dark:border-line-dark dark:text-ink-dark dark:hover:border-accent-dark"
				>
					Use a different name
				</button>
			{/if}
			<button
				type="button"
				onclick={() => {
					onClose();
					auth.logout();
				}}
				class="flex h-16 w-full items-center rounded-xl border border-line px-4.5 text-left text-[17px] font-semibold text-ink hover:border-accent dark:border-line-dark dark:text-ink-dark dark:hover:border-accent-dark"
			>
				Sign out
			</button>
		</div>
		<div class="flex flex-col gap-2">
			<SectionLabel>People</SectionLabel>
			{@render row('Agents', '/agents', agentCount === null ? null : String(agentCount))}
			{@render row('Teams', '/teams', teamCount === null ? null : String(teamCount))}
		</div>
		<div class="flex flex-col gap-2">
			<SectionLabel>Automations</SectionLabel>
			{@render row('Workflows', '/workflows', null)}
			{@render row('Evals', '/evals', null)}
			{@render row('Runs', '/runs', null)}
		</div>
		<div class="flex flex-col gap-2">
			<SectionLabel>Knowledge</SectionLabel>
			{@render row('Bases', '/knowledge-bases', null)}
			{@render row('Tools', '/tools', null)}
			<button
				type="button"
				onclick={() => open('/mcp-servers')}
				class="flex h-16 w-full items-center gap-3 rounded-xl border border-line px-4.5 text-left text-[17px] font-semibold text-ink hover:border-accent dark:border-line-dark dark:text-ink-dark dark:hover:border-accent-dark"
			>
				MCP servers
				{#if mcpConnected}
					<span class="ml-auto h-2 w-2 rounded-full bg-accent dark:bg-accent-dark"></span>
				{/if}
			</button>
		</div>
		<div class="flex flex-col gap-2">
			<SectionLabel>Workspace</SectionLabel>
			{#if isOwner}
				{@render row('Providers', '/providers', null)}
				{@render row('Integrations', SETTINGS_INTEGRATIONS_HREF, 'Gmail · Drive · Calendar')}
			{/if}
			{@render row('Usage', '/usage', null)}
			{@render row('Settings', '/settings', isOwner ? null : 'Spend status only')}
			{#if isOwner}
				{@render row('Sync', '/sync', null)}
				{@render row('Invites', '/invites', null)}
			{/if}
		</div>
	</div>
</Sheet>
