<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { auth } from '$lib/api/auth.svelte';
	import { approvalsBadge } from '$lib/approvalsBadge.svelte';
	import { readLastChannel } from '$lib/lastChannel';
	import { initials } from '$lib/ink';
	import Icon from '$lib/ui/Icon.svelte';
	import { paletteShortcutLabel } from '$lib/format';

	// 72px icon rail (04-information-architecture.md → Chrome): House,
	// Search (palette), Hash, Inbox (pending badge), Grid ("More" sheet),
	// avatar menu at the bottom. Desktop only — MobileTabs is the phone
	// equivalent.
	let {
		onOpenMore,
		onOpenAccount,
		onOpenPalette
	}: {
		onOpenMore: () => void;
		onOpenAccount: () => void;
		onOpenPalette: () => void;
	} = $props();

	approvalsBadge.refresh();

	function isActive(path: string): boolean {
		return page.url.pathname === path || page.url.pathname.startsWith(path + '/');
	}

	function railClass(active: boolean): string {
		return active
			? 'bg-accent-soft text-accent dark:bg-accent-soft-dark dark:text-accent-dark'
			: 'text-muted hover:bg-surface hover:text-ink dark:text-muted-dark dark:hover:bg-surface-dark dark:hover:text-ink-dark';
	}

	function openChannels() {
		const last = readLastChannel();
		goto(last ? resolve('/channels/[id]', { id: last }) : resolve('/channels'));
	}
</script>

<nav
	class="hidden h-full w-[72px] flex-none flex-col items-center gap-2 border-r border-line py-4 md:flex dark:border-line-dark"
	aria-label="Main"
>
	<a
		href={resolve('/')}
		class="mb-4 flex h-9 w-9 items-center justify-center rounded-[12px] bg-accent text-white dark:bg-accent-dark dark:text-paper-dark"
		aria-label="Rivulets home"
	>
		<Icon name="logo" class="h-5 w-5" />
	</a>

	<a
		href={resolve('/')}
		title="Home"
		aria-label="Home"
		class="flex h-12 w-12 items-center justify-center rounded-xl {railClass(
			page.url.pathname === '/'
		)}"
	>
		<Icon name="home" class="h-6 w-6" />
	</a>

	<button
		type="button"
		onclick={onOpenPalette}
		title="Search / jump ({paletteShortcutLabel()})"
		aria-label="Search / jump"
		class="flex h-12 w-12 items-center justify-center rounded-xl {railClass(false)}"
	>
		<Icon name="search" class="h-6 w-6" />
	</button>

	<button
		type="button"
		onclick={openChannels}
		title="Channels"
		aria-label="Channels"
		class="flex h-12 w-12 items-center justify-center rounded-xl {railClass(isActive('/channels'))}"
	>
		<Icon name="hash" class="h-6 w-6" />
	</button>

	<a
		href={resolve('/approvals')}
		title="Approvals"
		aria-label="Approvals"
		class="relative flex h-12 w-12 items-center justify-center rounded-xl {railClass(
			isActive('/approvals')
		)}"
	>
		<Icon name="inbox" class="h-6 w-6" />
		{#if approvalsBadge.count}
			<span
				class="absolute top-1 right-1 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-danger px-[5px] text-[11px] font-semibold text-white"
			>
				{approvalsBadge.count}
			</span>
		{/if}
	</a>

	<button
		type="button"
		onclick={onOpenMore}
		title="More"
		aria-label="More"
		class="flex h-12 w-12 items-center justify-center rounded-xl {railClass(false)}"
	>
		<Icon name="grid" class="h-6 w-6" />
	</button>

	<button
		type="button"
		onclick={onOpenAccount}
		title="Account"
		aria-label="Account"
		class="mt-auto flex h-9 w-9 items-center justify-center rounded-[12px] bg-ink text-sm font-semibold text-paper dark:bg-ink-dark dark:text-paper-dark"
	>
		{initials(auth.displayName ?? '?')}
	</button>
</nav>
