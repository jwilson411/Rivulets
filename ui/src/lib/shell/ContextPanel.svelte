<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { auth } from '$lib/api/auth.svelte';
	import { channels, type Channel } from '$lib/api/channels';
	import Button from '$lib/ui/Button.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import SectionLabel from '$lib/ui/SectionLabel.svelte';

	// Desktop context panel (04-information-architecture.md → Chrome):
	// the channel list on Home / Channels, a section nav on "More" rooms,
	// nothing anywhere else.

	interface NavItem {
		label: string;
		path: string;
		ownerOnly?: boolean;
	}

	interface NavGroup {
		label: string;
		items: NavItem[];
	}

	const GROUPS: NavGroup[] = [
		{
			label: 'People',
			items: [
				{ label: 'Agents', path: '/agents' },
				{ label: 'Teams', path: '/teams' }
			]
		},
		{
			label: 'Automations',
			items: [
				{ label: 'Workflows', path: '/workflows' },
				{ label: 'Evals', path: '/evals' },
				{ label: 'Runs', path: '/runs' }
			]
		},
		{
			label: 'Knowledge',
			items: [
				{ label: 'Bases', path: '/knowledge-bases' },
				{ label: 'Tools', path: '/tools' },
				{ label: 'MCP servers', path: '/mcp-servers' }
			]
		},
		{
			label: 'Workspace',
			items: [
				{ label: 'Providers', path: '/providers', ownerOnly: true },
				{ label: 'Usage', path: '/usage' },
				{ label: 'Settings', path: '/settings' },
				{ label: 'Sync', path: '/sync', ownerOnly: true },
				{ label: 'Invites', path: '/invites', ownerOnly: true }
			]
		}
	];

	let channelList = $state<Channel[]>([]);
	let loadError = $state<string | null>(null);
	let search = $state('');
	let creating = $state(false);
	let newName = $state('');
	let createBusy = $state(false);
	let createError = $state<string | null>(null);

	let isChatArea = $derived(page.url.pathname === '/' || page.url.pathname.startsWith('/channels'));
	// The workflow canvas is full bleed (03-design-direction.md → Layout) —
	// no section nav beside it.
	let isFullBleed = $derived(/^\/workflows\/[^/]+/.test(page.url.pathname));
	let activeGroup = $derived(
		isFullBleed
			? null
			: (GROUPS.find((g) => g.items.some((item) => page.url.pathname.startsWith(item.path))) ??
					null)
	);

	let visibleChannels = $derived(
		channelList.filter(
			(c) => !c.archived && c.name.toLowerCase().includes(search.trim().toLowerCase())
		)
	);

	async function refresh() {
		loadError = null;
		try {
			channelList = await channels.list();
		} catch {
			loadError = "Couldn't load channels.";
		}
	}

	$effect(() => {
		if (isChatArea) refresh();
	});

	function isActiveChannel(id: string): boolean {
		return page.url.pathname.startsWith(`/channels/${id}`);
	}

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		const name = newName.trim();
		if (!name) return;
		createBusy = true;
		createError = null;
		try {
			const created = await channels.create(name);
			creating = false;
			newName = '';
			await refresh();
			goto(resolve('/channels/[id]', { id: created.id }));
		} catch (err) {
			createError = err instanceof Error ? err.message : "Couldn't create the channel.";
		} finally {
			createBusy = false;
		}
	}
</script>

{#if isChatArea}
	<aside
		class="hidden h-full w-[280px] flex-none flex-col border-r border-line px-4 pt-6 pb-4 lg:flex dark:border-line-dark"
		aria-label="Channels"
	>
		<label
			class="mb-5 flex h-11 items-center gap-2.5 rounded-lg border border-line bg-surface px-3.5 focus-within:border-accent dark:border-line-dark dark:bg-surface-dark dark:focus-within:border-accent-dark"
		>
			<span class="sr-only">Filter channels</span>
			<Icon name="search" class="h-[18px] w-[18px] flex-none text-muted dark:text-muted-dark" />
			<input
				type="search"
				bind:value={search}
				placeholder="Filter channels"
				class="min-w-0 flex-1 appearance-none bg-transparent text-[15px] text-ink placeholder:text-muted focus:outline-none dark:text-ink-dark dark:placeholder:text-muted-dark"
			/>
		</label>
		<SectionLabel class="mb-2 px-3.5">Channels</SectionLabel>
		<div class="flex flex-1 flex-col gap-1 overflow-y-auto">
			{#if loadError}
				<p class="px-3.5 text-sm text-danger">{loadError}</p>
			{:else}
				{#each visibleChannels as channel (channel.id)}
					<a
						href={resolve('/channels/[id]', { id: channel.id })}
						class="flex h-11 flex-none items-center gap-2.5 rounded-lg px-3.5 {isActiveChannel(
							channel.id
						)
							? 'bg-accent-soft dark:bg-accent-soft-dark'
							: 'hover:bg-surface dark:hover:bg-surface-dark'}"
					>
						<span
							class="text-base {isActiveChannel(channel.id)
								? 'text-accent dark:text-accent-dark'
								: 'text-muted dark:text-muted-dark'}">#</span
						>
						<span
							class="truncate text-[15px] {isActiveChannel(channel.id)
								? 'font-semibold'
								: 'font-medium'} text-ink dark:text-ink-dark">{channel.name}</span
						>
					</a>
				{/each}
			{/if}
		</div>
		<Button variant="secondary" class="mt-4 w-full" onclick={() => (creating = true)}>
			<Icon name="plus" class="h-[18px] w-[18px]" />
			New channel
		</Button>
	</aside>
{:else if activeGroup}
	<aside
		class="hidden h-full w-[240px] flex-none flex-col border-r border-line px-4 pt-8 lg:flex dark:border-line-dark"
		aria-label={activeGroup.label}
	>
		<SectionLabel class="mb-3 px-3.5">{activeGroup.label}</SectionLabel>
		<div class="flex flex-col gap-1">
			{#each activeGroup.items.filter((i) => !i.ownerOnly || auth.grant === 'owner') as item (item.path)}
				<a
					href={resolve(item.path as '/')}
					class="flex h-12 items-center rounded-lg px-4 text-[15px] {page.url.pathname.startsWith(
						item.path
					)
						? 'bg-accent-soft font-semibold text-ink dark:bg-accent-soft-dark dark:text-ink-dark'
						: 'font-medium text-muted hover:bg-surface hover:text-ink dark:text-muted-dark dark:hover:bg-surface-dark dark:hover:text-ink-dark'}"
				>
					{item.label}
				</a>
			{/each}
		</div>
	</aside>
{/if}

{#if creating}
	<Sheet title="New channel" onClose={() => (creating = false)}>
		<form id="new-channel-form" onsubmit={handleCreate} class="flex flex-col gap-2">
			<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="new-channel-name">
				Name
			</label>
			<input
				id="new-channel-name"
				type="text"
				bind:value={newName}
				placeholder="launch-readiness"
				class="h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark"
			/>
			{#if createError}
				<p class="text-sm text-danger">{createError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (creating = false)}>Cancel</Button>
			<Button
				disabled={createBusy || !newName.trim()}
				onclick={() =>
					(document.getElementById('new-channel-form') as HTMLFormElement).requestSubmit()}
			>
				Create channel
			</Button>
		{/snippet}
	</Sheet>
{/if}
