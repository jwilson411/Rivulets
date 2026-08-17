<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { auth } from '$lib/api/auth.svelte';
	import { agents } from '$lib/api/agents';
	import { teams } from '$lib/api/teams';
	import { mcpServers } from '$lib/api/mcpServers';
	import Sheet from '$lib/ui/Sheet.svelte';
	import SectionLabel from '$lib/ui/SectionLabel.svelte';
	import SearchJumpButton from '$lib/shell/SearchJumpButton.svelte';

	// The "More" sheet (mockup 2c): grouped cards with large type, 64px
	// rows. Guests never see Providers, Sync, or Invites — hidden, not
	// disabled (2q); their Settings row is spend status only.
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
		goto(resolve(path as '/'));
	}

	const isOwner = $derived(auth.grant === 'owner');
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
