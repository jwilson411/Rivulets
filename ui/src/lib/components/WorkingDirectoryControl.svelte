<script lang="ts">
	import FolderPickerSheet from '$lib/components/FolderPickerSheet.svelte';
	import Icon from '$lib/ui/Icon.svelte';

	// Compact project-folder control for a channel (river default) or a
	// rivulet (conversation override). Changing a rivulet never writes
	// the channel folder.

	let {
		storedPath = null,
		inheritedPath = null,
		inheritedLabel = 'the default',
		canEdit = false,
		busy = false,
		error = null,
		onSave
	}: {
		storedPath?: string | null;
		inheritedPath?: string | null;
		inheritedLabel?: string;
		canEdit?: boolean;
		busy?: boolean;
		error?: string | null;
		onSave: (path: string | null) => Promise<void> | void;
	} = $props();

	let picking = $state(false);

	let effective = $derived(storedPath ?? inheritedPath);
	let usingOverride = $derived(storedPath != null);
	let label = $derived(usingOverride ? 'This conversation' : inheritedLabel);
	let displayPath = $derived(effective ?? 'Built-in sandbox');

	async function choose(path: string) {
		picking = false;
		await onSave(path);
	}

	async function clearOverride() {
		await onSave(null);
	}
</script>

<div class="flex max-w-full flex-col items-end gap-1">
	<div
		class="flex max-w-full items-center gap-2 rounded-lg border border-line bg-surface px-3 py-1.5 dark:border-line-dark dark:bg-surface-dark"
	>
		<Icon name="folder" class="h-4 w-4 flex-none text-muted dark:text-muted-dark" />
		<div class="min-w-0">
			<p
				class="truncate font-mono text-[12px] text-ink dark:text-ink-dark"
				title={effective ?? undefined}
			>
				{displayPath}
			</p>
			<p class="text-[11px] text-muted dark:text-muted-dark">{label}</p>
		</div>
		{#if canEdit}
			<button
				type="button"
				onclick={() => (picking = true)}
				disabled={busy}
				class="ml-1 flex-none text-[13px] font-semibold text-accent hover:underline disabled:opacity-50 dark:text-accent-dark"
			>
				{effective ? 'Change' : 'Choose'}
			</button>
			{#if usingOverride}
				<button
					type="button"
					onclick={clearOverride}
					disabled={busy}
					class="flex-none text-[13px] font-semibold text-muted hover:underline disabled:opacity-50 dark:text-muted-dark"
				>
					Use {inheritedLabel}
				</button>
			{/if}
		{/if}
	</div>
	{#if error}
		<p class="text-sm text-danger">{error}</p>
	{/if}
</div>

{#if picking}
	<FolderPickerSheet
		initialPath={effective}
		onClose={() => (picking = false)}
		onSelect={(path) => void choose(path)}
	/>
{/if}
