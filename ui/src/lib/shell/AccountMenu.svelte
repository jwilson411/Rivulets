<script lang="ts">
	import { auth } from '$lib/api/auth.svelte';
	import { theme, type ThemePreference } from '$lib/theme.svelte';
	import { initials } from '$lib/ink';
	import Icon from '$lib/ui/Icon.svelte';

	// Account menu, opened from the rail avatar (04-information-architecture
	// → Icon rail): name, theme, switch identity (owner), sign out.
	let { onClose }: { onClose: () => void } = $props();

	const themeOptions: { value: ThemePreference; label: string; icon: string }[] = [
		{ value: 'light', label: 'Light', icon: 'sun' },
		{ value: 'dark', label: 'Dark', icon: 'moon' },
		{ value: 'system', label: 'System', icon: 'monitor' }
	];

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') onClose();
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="fixed inset-0 z-40" onclick={onClose} role="presentation"></div>
<div
	class="fixed bottom-4 left-[80px] z-50 w-[280px] rounded-xl border border-line bg-surface p-3 shadow-pop max-md:right-4 max-md:bottom-20 max-md:left-auto dark:border-line-dark dark:bg-surface-dark"
	role="menu"
	aria-label="Account"
>
	<div class="flex items-center gap-3 px-2 pt-1 pb-3">
		<span
			class="flex h-9 w-9 flex-none items-center justify-center rounded-[12px] bg-ink text-sm font-semibold text-paper dark:bg-ink-dark dark:text-paper-dark"
		>
			{initials(auth.displayName ?? '?')}
		</span>
		<div class="min-w-0">
			<div class="truncate text-[15px] font-semibold text-ink dark:text-ink-dark">
				{auth.displayName}
			</div>
			<div class="text-[13px] text-muted capitalize dark:text-muted-dark">
				{auth.grant === 'owner' ? 'Owner' : 'Guest'}
			</div>
		</div>
	</div>

	<div
		role="group"
		aria-label="Theme"
		class="mb-2 flex rounded-lg border border-line p-1 dark:border-line-dark"
	>
		{#each themeOptions as option (option.value)}
			<button
				type="button"
				title={option.label}
				aria-pressed={theme.preference === option.value}
				onclick={() => theme.set(option.value)}
				class="flex h-9 flex-1 items-center justify-center gap-1.5 rounded-md text-[13px] font-medium {theme.preference ===
				option.value
					? 'bg-accent-soft text-accent dark:bg-accent-soft-dark dark:text-accent-dark'
					: 'text-muted hover:bg-paper dark:text-muted-dark dark:hover:bg-paper-dark'}"
			>
				<Icon name={option.icon} class="h-4 w-4" />
				{option.label}
			</button>
		{/each}
	</div>

	{#if auth.grant === 'owner'}
		<button
			type="button"
			role="menuitem"
			onclick={() => {
				onClose();
				auth.clearIdentity();
			}}
			class="flex h-11 w-full items-center rounded-lg px-3 text-left text-[15px] font-medium text-ink hover:bg-paper dark:text-ink-dark dark:hover:bg-paper-dark"
		>
			Use a different name
		</button>
	{/if}
	<button
		type="button"
		role="menuitem"
		onclick={() => {
			onClose();
			auth.logout();
		}}
		class="flex h-11 w-full items-center rounded-lg px-3 text-left text-[15px] font-medium text-ink hover:bg-paper dark:text-ink-dark dark:hover:bg-paper-dark"
	>
		Sign out
	</button>
</div>
