<script lang="ts">
	import type { Workflow } from '$lib/api/workflows';
	import {
		applyMention,
		filterMentionCandidates,
		mentionQueryAt,
		type MentionCandidate
	} from '$lib/mentions';
	import Icon from './Icon.svelte';

	// The Stream Bar (03-design-direction.md's signature): a large rounded
	// composer — white surface, 24px radius, soft shadow, 20px textarea,
	// 40px attach on the left, 48px circular Send on the right, helper line
	// below. Enter sends, Shift+Enter newlines. Typing "/" opens a slash
	// menu of published workflows (drafts listed but not runnable). Typing
	// "@" opens a mention menu of the channel's teammates.
	let {
		placeholder,
		helper = null,
		busy = false,
		error = null,
		slashWorkflows = null,
		mentionCandidates = null,
		onSend
	}: {
		placeholder: string;
		helper?: string | null;
		busy?: boolean;
		error?: string | null;
		slashWorkflows?: Workflow[] | null;
		mentionCandidates?: MentionCandidate[] | null;
		// Returns true when the message was accepted — the bar then clears.
		onSend: (text: string, files: File[]) => Promise<boolean>;
	} = $props();

	let value = $state('');
	let pendingFiles = $state<File[]>([]);
	let fileInput = $state<HTMLInputElement | null>(null);
	let textarea = $state<HTMLTextAreaElement | null>(null);
	let cursor = $state(0);
	let slashIndex = $state(0);
	let mentionIndex = $state(0);
	let mentionDismissed = $state(false);
	let lastMentionKey = $state('');

	// Slash menu shows while the draft is exactly a partial command token.
	let slashQuery = $derived.by(() => {
		if (!slashWorkflows) return null;
		const match = /^\/([a-z0-9-]*)$/.exec(value);
		return match ? match[1] : null;
	});
	let slashMatches = $derived(
		slashQuery === null
			? []
			: (slashWorkflows ?? []).filter((w) => w.name.startsWith(slashQuery ?? ''))
	);

	let mentionQuery = $derived.by(() => {
		if (!mentionCandidates?.length) return null;
		return mentionQueryAt(value, cursor);
	});
	let mentionMatches = $derived(
		mentionQuery === null
			? []
			: filterMentionCandidates(mentionCandidates ?? [], mentionQuery.query)
	);
	let mentionOpen = $derived(mentionMatches.length > 0 && !mentionDismissed);

	$effect(() => {
		const key = mentionQuery ? `${mentionQuery.start}:${mentionQuery.query}` : '';
		if (key !== lastMentionKey) {
			lastMentionKey = key;
			mentionDismissed = false;
			mentionIndex = 0;
		}
	});

	$effect(() => {
		void slashMatches.length;
		slashIndex = 0;
	});

	function pickWorkflow(w: Workflow) {
		if (!w.published) return;
		value = `/${w.name} `;
		textarea?.focus();
	}

	function placeCursor(next: number) {
		cursor = next;
		requestAnimationFrame(() => {
			textarea?.focus();
			textarea?.setSelectionRange(next, next);
			autosize();
		});
	}

	function pickMention(candidate: MentionCandidate) {
		const next = applyMention(value, cursor, candidate.name);
		value = next.text;
		placeCursor(next.cursor);
	}

	export function insertMention(name: string) {
		const at = textarea?.selectionStart ?? value.length;
		cursor = at;
		const next = applyMention(value, cursor, name);
		value = next.text;
		placeCursor(next.cursor);
	}

	function syncCursor() {
		cursor = textarea?.selectionStart ?? value.length;
	}

	function autosize() {
		if (!textarea) return;
		textarea.style.height = 'auto';
		textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
	}

	function handleFileSelect(event: Event) {
		const input = event.target as HTMLInputElement;
		pendingFiles = [...pendingFiles, ...Array.from(input.files ?? [])];
		input.value = ''; // allow re-selecting the same file after removal
	}

	function removePendingFile(index: number) {
		pendingFiles = pendingFiles.filter((_, i) => i !== index);
	}

	async function send() {
		if (busy) return;
		const text = value.trim();
		if (!text && pendingFiles.length === 0) return;
		const accepted = await onSend(text, pendingFiles);
		if (accepted) {
			value = '';
			pendingFiles = [];
			cursor = 0;
			if (textarea) textarea.style.height = 'auto';
		}
	}

	function handleKeydown(event: KeyboardEvent) {
		syncCursor();

		if (mentionOpen) {
			if (event.key === 'ArrowDown') {
				event.preventDefault();
				mentionIndex = (mentionIndex + 1) % mentionMatches.length;
				return;
			}
			if (event.key === 'ArrowUp') {
				event.preventDefault();
				mentionIndex = (mentionIndex - 1 + mentionMatches.length) % mentionMatches.length;
				return;
			}
			if (event.key === 'Enter' || event.key === 'Tab') {
				event.preventDefault();
				pickMention(mentionMatches[mentionIndex]);
				return;
			}
			if (event.key === 'Escape') {
				event.preventDefault();
				mentionDismissed = true;
				return;
			}
		}

		if (slashMatches.length > 0) {
			if (event.key === 'ArrowDown') {
				event.preventDefault();
				slashIndex = (slashIndex + 1) % slashMatches.length;
				return;
			}
			if (event.key === 'ArrowUp') {
				event.preventDefault();
				slashIndex = (slashIndex - 1 + slashMatches.length) % slashMatches.length;
				return;
			}
			if (event.key === 'Enter' || event.key === 'Tab') {
				const picked = slashMatches[slashIndex] ?? slashMatches.find((w) => w.published);
				if (picked?.published) {
					event.preventDefault();
					pickWorkflow(picked);
					return;
				}
			}
			if (event.key === 'Escape') {
				event.preventDefault();
				return;
			}
		}

		if (event.key === 'Enter' && !event.shiftKey) {
			event.preventDefault();
			send();
		}
	}
</script>

<div class="relative min-w-0">
	{#if mentionOpen}
		<div
			class="absolute bottom-full mb-3 w-full max-w-[420px] rounded-xl border border-line bg-surface p-2 shadow-pop dark:border-line-dark dark:bg-surface-dark"
			role="listbox"
			aria-label="Mention a teammate"
		>
			<div
				class="px-3 pt-2 pb-1 text-xs font-semibold tracking-[.04em] text-muted uppercase dark:text-muted-dark"
			>
				Mention
			</div>
			{#each mentionMatches as candidate, i (candidate.id)}
				<button
					type="button"
					role="option"
					aria-selected={i === mentionIndex}
					id="mention-opt-{candidate.id}"
					onclick={() => pickMention(candidate)}
					class="flex h-12 w-full items-center gap-3 rounded-md px-3 text-left {i === mentionIndex
						? 'bg-accent-soft dark:bg-accent-soft-dark'
						: 'hover:bg-accent-soft dark:hover:bg-accent-soft-dark'}"
				>
					<span class="font-mono text-sm font-medium text-accent dark:text-accent-dark"
						>@{candidate.name}</span
					>
					<span class="truncate text-[13px] text-muted dark:text-muted-dark">
						{candidate.kind === 'human' ? 'Person' : 'Teammate'}
					</span>
				</button>
			{/each}
		</div>
	{:else if slashMatches.length > 0}
		<div
			class="absolute bottom-full mb-3 w-full max-w-[420px] rounded-xl border border-line bg-surface p-2 shadow-pop dark:border-line-dark dark:bg-surface-dark"
			role="menu"
		>
			<div
				class="px-3 pt-2 pb-1 text-xs font-semibold tracking-[.04em] text-muted uppercase dark:text-muted-dark"
			>
				Run a workflow
			</div>
			{#each slashMatches as w, i (w.id)}
				<button
					type="button"
					role="menuitem"
					disabled={!w.published}
					onclick={() => pickWorkflow(w)}
					class="flex h-12 w-full items-center gap-3 rounded-md px-3 text-left {w.published
						? i === slashIndex
							? 'bg-accent-soft dark:bg-accent-soft-dark'
							: 'hover:bg-accent-soft dark:hover:bg-accent-soft-dark'
						: 'opacity-50'}"
				>
					<span
						class="font-mono text-sm font-medium {w.published
							? 'text-accent dark:text-accent-dark'
							: 'text-ink dark:text-ink-dark'}">/{w.name}</span
					>
					<span class="truncate text-[13px] text-muted dark:text-muted-dark">
						{w.published ? (w.description ?? '') : 'Draft — publish to run it'}
					</span>
				</button>
			{/each}
		</div>
	{/if}

	{#if error}
		<p class="mb-2 text-sm text-danger">{error}</p>
	{/if}

	{#if pendingFiles.length > 0}
		<div class="mb-2 flex flex-wrap gap-2">
			{#each pendingFiles as file, i (file.name + i)}
				<span
					class="flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1.5 text-[13px] text-ink dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark"
				>
					<Icon name="attach" class="h-3.5 w-3.5 text-muted dark:text-muted-dark" />
					{file.name}
					<button
						type="button"
						onclick={() => removePendingFile(i)}
						aria-label="Remove {file.name}"
						class="text-muted hover:text-danger dark:text-muted-dark"
					>
						<Icon name="close" class="h-3.5 w-3.5" />
					</button>
				</span>
			{/each}
		</div>
	{/if}

	<form
		onsubmit={(e) => {
			e.preventDefault();
			send();
		}}
		class="flex items-end gap-2 rounded-3xl bg-surface p-3 shadow-bar md:gap-3 md:p-4 dark:border dark:border-line-dark dark:bg-surface-dark dark:shadow-bar-dark"
	>
		<input bind:this={fileInput} type="file" multiple onchange={handleFileSelect} class="hidden" />
		<button
			type="button"
			onclick={() => fileInput?.click()}
			title="Attach a file"
			aria-label="Attach a file"
			class="flex h-10 w-10 flex-none items-center justify-center rounded-[14px] text-muted hover:bg-paper hover:text-ink dark:text-muted-dark dark:hover:bg-paper-dark dark:hover:text-ink-dark"
		>
			<Icon name="attach" class="h-5 w-5" />
		</button>
		<textarea
			bind:this={textarea}
			{value}
			oninput={(event) => {
				const el = event.currentTarget;
				value = el.value;
				cursor = el.selectionStart ?? el.value.length;
				autosize();
			}}
			onclick={syncCursor}
			onkeyup={syncCursor}
			onselect={syncCursor}
			onkeydown={handleKeydown}
			rows="1"
			{placeholder}
			class="max-h-[200px] min-w-0 flex-1 resize-none bg-transparent py-2 text-lg text-ink placeholder:text-muted focus:outline-none md:text-xl dark:text-ink-dark dark:placeholder:text-muted-dark"
		></textarea>
		<button
			type="submit"
			disabled={busy}
			title="Send"
			aria-label="Send"
			class="flex h-12 w-12 flex-none items-center justify-center rounded-full bg-accent text-white transition-colors hover:bg-accent-deep disabled:opacity-40 dark:bg-accent-dark dark:text-paper-dark"
		>
			<Icon name="send" class="h-[22px] w-[22px]" />
		</button>
	</form>

	{#if helper}
		<div class="mt-2.5 pl-4 text-[13px] text-muted dark:text-muted-dark">{helper}</div>
	{/if}
</div>
