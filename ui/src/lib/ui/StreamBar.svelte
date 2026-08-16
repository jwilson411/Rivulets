<script lang="ts">
	import type { Workflow } from '$lib/api/workflows';
	import Icon from './Icon.svelte';

	// The Stream Bar (03-design-direction.md's signature): a large rounded
	// composer — white surface, 24px radius, soft shadow, 20px textarea,
	// 40px attach on the left, 48px circular Send on the right, helper line
	// below. Enter sends, Shift+Enter newlines. Typing "/" opens a slash
	// menu of published workflows (drafts listed but not runnable).
	let {
		placeholder,
		helper = null,
		busy = false,
		error = null,
		slashWorkflows = null,
		onSend
	}: {
		placeholder: string;
		helper?: string | null;
		busy?: boolean;
		error?: string | null;
		slashWorkflows?: Workflow[] | null;
		// Returns true when the message was accepted — the bar then clears.
		onSend: (text: string, files: File[]) => Promise<boolean>;
	} = $props();

	let value = $state('');
	let pendingFiles = $state<File[]>([]);
	let fileInput = $state<HTMLInputElement | null>(null);
	let textarea = $state<HTMLTextAreaElement | null>(null);

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

	function pickWorkflow(w: Workflow) {
		if (!w.published) return;
		value = `/${w.name} `;
		textarea?.focus();
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
			if (textarea) textarea.style.height = 'auto';
		}
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter' && !event.shiftKey) {
			event.preventDefault();
			send();
		}
	}
</script>

<div class="relative">
	{#if slashMatches.length > 0}
		<div
			class="absolute bottom-full mb-3 w-full max-w-[420px] rounded-xl border border-line bg-surface p-2 shadow-pop dark:border-line-dark dark:bg-surface-dark"
			role="menu"
		>
			<div
				class="px-3 pt-2 pb-1 text-xs font-semibold tracking-[.04em] text-muted uppercase dark:text-muted-dark"
			>
				Run a workflow
			</div>
			{#each slashMatches as w (w.id)}
				<button
					type="button"
					role="menuitem"
					disabled={!w.published}
					onclick={() => pickWorkflow(w)}
					class="flex h-12 w-full items-center gap-3 rounded-md px-3 text-left {w.published
						? 'hover:bg-accent-soft dark:hover:bg-accent-soft-dark'
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
		class="flex items-end gap-3 rounded-3xl bg-surface p-4 shadow-bar dark:border dark:border-line-dark dark:bg-surface-dark dark:shadow-bar-dark"
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
			bind:value
			oninput={autosize}
			onkeydown={handleKeydown}
			rows="1"
			{placeholder}
			class="max-h-[200px] min-w-0 flex-1 resize-none bg-transparent py-2 text-xl text-ink placeholder:text-muted focus:outline-none dark:text-ink-dark dark:placeholder:text-muted-dark"
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
