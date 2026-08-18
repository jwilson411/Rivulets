<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { auth } from '$lib/api/auth.svelte';
	import { channels } from '$lib/api/channels';
	import { workflows } from '$lib/api/workflows';
	import { SETTINGS_INTEGRATIONS_HREF } from '$lib/toolCatalog';
	import { approvalsBadge } from '$lib/approvalsBadge.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import SectionLabel from '$lib/ui/SectionLabel.svelte';

	// ⌘K command palette (04-information-architecture.md): a stub that
	// navigates — plain substring match, no fuzzy engine. Guests don't see
	// owner commands (hidden surfaces are omitted, not disabled).
	let { onClose }: { onClose: () => void } = $props();

	interface Command {
		group: 'Jump to' | 'Actions';
		label: string;
		hint: string | null;
		mono?: boolean;
		badge?: number;
		run: () => void;
	}

	let query = $state('');
	let selectedIndex = $state(0);
	let commandList = $state<Command[]>([]);
	let input = $state<HTMLInputElement | null>(null);

	$effect(() => {
		input?.focus();
	});

	async function load() {
		const [channelList, workflowList] = await Promise.all([
			channels.list().catch(() => []),
			workflows.list().catch(() => [])
		]);
		const list: Command[] = [];
		for (const c of channelList.filter((c) => !c.archived)) {
			list.push({
				group: 'Jump to',
				label: c.name,
				hint: 'Channel',
				run: () => go(`/channels/${c.id}`)
			});
			list.push({
				group: 'Actions',
				label: `New conversation in #${c.name}`,
				hint: null,
				run: () => go(`/channels/${c.id}`)
			});
		}
		for (const w of workflowList) {
			list.push({
				group: 'Jump to',
				label: `/${w.name}`,
				hint: 'Workflow',
				mono: true,
				run: () => go(`/workflows/${w.id}`)
			});
		}
		list.push({
			group: 'Actions',
			label: 'Open Approvals',
			hint: null,
			badge: approvalsBadge.count ?? undefined,
			run: () => go('/approvals')
		});
		list.push({ group: 'Actions', label: 'New agent', hint: null, run: () => go('/agents') });
		list.push({
			group: 'Actions',
			label: 'New workflow',
			hint: null,
			run: () => go('/workflows')
		});
		list.push({ group: 'Actions', label: 'Open Settings', hint: null, run: () => go('/settings') });
		if (auth.grant === 'owner') {
			list.push({
				group: 'Actions',
				label: 'Open Providers',
				hint: null,
				run: () => go('/providers')
			});
			// Google Workspace lives on Settings → Integrations, not Providers (#471).
			list.push({
				group: 'Actions',
				label: 'Open Integrations',
				hint: 'Gmail · Drive · Calendar',
				run: () => go(SETTINGS_INTEGRATIONS_HREF)
			});
			list.push({ group: 'Actions', label: 'Open Sync', hint: null, run: () => go('/sync') });
			list.push({ group: 'Actions', label: 'Open Invites', hint: null, run: () => go('/invites') });
		}
		commandList = list;
	}

	load();

	function go(path: string) {
		onClose();
		const [pathname, search = ''] = path.split('?');
		goto(resolve(pathname as '/') + (search ? `?${search}` : ''));
	}

	let matches = $derived.by(() => {
		const q = query.trim().toLowerCase();
		const filtered = q
			? commandList.filter(
					(c) =>
						c.label.toLowerCase().includes(q) ||
						(c.hint != null && c.hint.toLowerCase().includes(q))
				)
			: commandList;
		// Jump-to entries first, then actions, capped so the list stays short.
		return [
			...filtered.filter((c) => c.group === 'Jump to'),
			...filtered.filter((c) => c.group === 'Actions')
		].slice(0, 9);
	});

	$effect(() => {
		void matches;
		selectedIndex = 0;
	});

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			onClose();
		} else if (event.key === 'ArrowDown') {
			event.preventDefault();
			selectedIndex = Math.min(selectedIndex + 1, matches.length - 1);
		} else if (event.key === 'ArrowUp') {
			event.preventDefault();
			selectedIndex = Math.max(selectedIndex - 1, 0);
		} else if (event.key === 'Enter') {
			event.preventDefault();
			matches[selectedIndex]?.run();
		}
	}
</script>

<div
	class="fixed inset-0 z-50 flex items-start justify-center bg-ink/40 px-4 pt-[12vh] dark:bg-black/60"
	onclick={(e) => {
		if (e.target === e.currentTarget) onClose();
	}}
	role="presentation"
>
	<div
		role="dialog"
		aria-modal="true"
		aria-label="Command palette"
		class="w-full max-w-[520px] overflow-hidden rounded-2xl bg-surface shadow-palette dark:bg-surface-dark"
	>
		<div class="flex items-center gap-3 border-b border-line px-5 py-4 dark:border-line-dark">
			<Icon name="search" class="h-5 w-5 flex-none text-muted dark:text-muted-dark" />
			<input
				bind:this={input}
				bind:value={query}
				onkeydown={handleKeydown}
				type="text"
				placeholder="Jump to a channel, run an action…"
				aria-label="Command palette search"
				class="min-w-0 flex-1 bg-transparent text-[17px] text-ink placeholder:text-muted focus:outline-none dark:text-ink-dark dark:placeholder:text-muted-dark"
			/>
			<span
				class="rounded-md bg-paper px-2 py-1 font-mono text-xs text-muted dark:bg-paper-dark dark:text-muted-dark"
			>
				esc
			</span>
		</div>
		<div class="p-2">
			{#each ['Jump to', 'Actions'] as group (group)}
				{@const groupMatches = matches.filter((m) => m.group === group)}
				{#if groupMatches.length > 0}
					<SectionLabel class="px-3 pt-2 pb-1 text-xs">{group}</SectionLabel>
					{#each groupMatches as command (command.label)}
						{@const index = matches.indexOf(command)}
						<button
							type="button"
							onclick={command.run}
							onmouseenter={() => (selectedIndex = index)}
							class="flex h-12 w-full items-center gap-3 rounded-md px-3 text-left {index ===
							selectedIndex
								? 'bg-accent-soft dark:bg-accent-soft-dark'
								: ''}"
						>
							{#if command.hint === 'Channel'}
								<span class="text-[15px] text-accent dark:text-accent-dark">#</span>
							{/if}
							<span
								class="truncate text-[15px] {command.mono ? 'font-mono text-sm' : ''} {index ===
								selectedIndex
									? 'font-semibold'
									: ''} text-ink dark:text-ink-dark"
							>
								{command.label}
							</span>
							{#if command.badge}
								<span
									class="ml-auto flex h-5 items-center rounded-full bg-danger-soft px-2 text-xs font-semibold text-danger dark:bg-danger-soft-dark dark:text-danger-ink-dark"
								>
									{command.badge}
								</span>
							{:else if command.hint}
								<span class="ml-auto text-[13px] text-muted dark:text-muted-dark"
									>{command.hint}</span
								>
							{/if}
						</button>
					{/each}
				{/if}
			{/each}
		</div>
	</div>
</div>
