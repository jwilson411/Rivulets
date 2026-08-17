<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { approvalsBadge } from '$lib/approvalsBadge.svelte';
	import Icon from '$lib/ui/Icon.svelte';

	// Phone chrome (04-information-architecture.md → Mobile): the rail
	// becomes a bottom tab bar — Home, Channels, Approvals, More.
	let { onOpenMore }: { onOpenMore: () => void } = $props();

	function isActive(path: string): boolean {
		return path === '/'
			? page.url.pathname === '/'
			: page.url.pathname === path || page.url.pathname.startsWith(path + '/');
	}

	function tabClass(active: boolean): string {
		return active ? 'text-accent dark:text-accent-dark' : 'text-muted dark:text-muted-dark';
	}
</script>

<nav
	class="flex-none border-t border-line bg-surface pb-[env(safe-area-inset-bottom,0px)] md:hidden dark:border-line-dark dark:bg-surface-dark"
	aria-label="Main"
>
	<div class="flex h-16 items-stretch">
		<a
			href={resolve('/')}
			class="flex flex-1 flex-col items-center justify-center gap-0.5 {tabClass(isActive('/'))}"
		>
			<Icon name="home" class="h-6 w-6" />
			<span class="text-[11px] font-medium">Home</span>
		</a>
		<a
			href={resolve('/channels')}
			class="flex flex-1 flex-col items-center justify-center gap-0.5 {tabClass(
				isActive('/channels')
			)}"
		>
			<Icon name="hash" class="h-6 w-6" />
			<span class="text-[11px] font-medium">Channels</span>
		</a>
		<a
			href={resolve('/approvals')}
			class="relative flex flex-1 flex-col items-center justify-center gap-0.5 {tabClass(
				isActive('/approvals')
			)}"
		>
			<span class="relative">
				<Icon name="inbox" class="h-6 w-6" />
				{#if approvalsBadge.count}
					<span
						class="absolute -top-1 -right-2 flex h-[16px] min-w-[16px] items-center justify-center rounded-full bg-danger px-1 text-[10px] font-semibold text-white"
					>
						{approvalsBadge.count}
					</span>
				{/if}
			</span>
			<span class="text-[11px] font-medium">Approvals</span>
		</a>
		<button
			type="button"
			onclick={onOpenMore}
			class="flex flex-1 flex-col items-center justify-center gap-0.5 {tabClass(false)}"
		>
			<Icon name="grid" class="h-6 w-6" />
			<span class="text-[11px] font-medium">More</span>
		</button>
	</div>
</nav>
