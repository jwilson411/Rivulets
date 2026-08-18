<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import {
		knowledgeBases,
		type KnowledgeBase,
		type KnowledgeBaseDocument
	} from '$lib/api/knowledgeBases';
	import { agents as agentsApi } from '$lib/api/agents';
	import { teams as teamsApi } from '$lib/api/teams';
	import { files } from '$lib/api/files';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import StatusPill from '$lib/ui/StatusPill.svelte';

	// Knowledge base detail (06-screens.md → Knowledge bases, mockup 2h):
	// large dropzone, then document rows with status and Remove.

	let kb = $state<KnowledgeBase | null>(null);
	let documentList = $state<KnowledgeBaseDocument[]>([]);
	let subjectName = $state<string | null>(null);
	let loadError = $state<string | null>(null);

	let uploading = $state(false);
	let uploadError = $state<string | null>(null);
	let actionError = $state<string | null>(null);
	let fileInput = $state<HTMLInputElement | null>(null);
	let dragOver = $state(false);

	async function load(id: string) {
		loadError = null;
		try {
			const [loadedKb, docs] = await Promise.all([
				knowledgeBases.get(id),
				knowledgeBases.listDocuments(id)
			]);
			kb = loadedKb;
			documentList = docs;
			if (loadedKb.scope_type === 'agent' && loadedKb.agent_id) {
				subjectName =
					(await agentsApi.list().catch(() => [])).find((a) => a.id === loadedKb.agent_id)?.name ??
					null;
			} else if (loadedKb.team_id) {
				subjectName =
					(await teamsApi.list().catch(() => [])).find((t) => t.id === loadedKb.team_id)?.name ??
					null;
			}
		} catch {
			loadError = "Couldn't load this knowledge base.";
		}
	}

	load(page.params.id!);

	async function ingest(file: File) {
		if (!kb) return;
		uploading = true;
		uploadError = null;
		try {
			const uploaded = await files.upload(file);
			await knowledgeBases.ingestDocument(kb.id, uploaded.file_id);
			await load(kb.id);
		} catch {
			uploadError = "Couldn't read that file. Markdown, text, or JSON only.";
		} finally {
			uploading = false;
			if (fileInput) fileInput.value = '';
		}
	}

	function handleFileSelect(event: Event) {
		const input = event.target as HTMLInputElement;
		const file = input.files?.[0];
		if (file) ingest(file);
	}

	function handleDrop(event: DragEvent) {
		event.preventDefault();
		dragOver = false;
		const file = event.dataTransfer?.files?.[0];
		if (file) ingest(file);
	}

	async function handleRemove(documentId: string) {
		if (!kb) return;
		actionError = null;
		try {
			await knowledgeBases.removeDocument(kb.id, documentId);
			await load(kb.id);
		} catch {
			actionError = "Couldn't remove that document. Try again.";
		}
	}

	function docLabel(doc: KnowledgeBaseDocument): string {
		return doc.filename || `file ${doc.file_id.slice(0, 8)}…`;
	}

	const statusTone: Record<KnowledgeBaseDocument['status'], 'accent' | 'warn' | 'danger'> = {
		ingested: 'accent',
		pending: 'warn',
		failed: 'danger'
	};
	const statusLabel: Record<KnowledgeBaseDocument['status'], string> = {
		ingested: 'Ready',
		pending: 'Reading',
		failed: "Couldn't read"
	};
</script>

<div class="mx-auto max-w-[720px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
	<a
		href={resolve('/knowledge-bases')}
		class="mb-4 flex items-center gap-2 text-sm font-semibold text-accent hover:text-accent-deep dark:text-accent-dark"
	>
		<Icon name="back" class="h-4 w-4" />
		Bases
	</a>

	{#if loadError}
		<ErrorBanner message={loadError} onRetry={() => load(page.params.id!)} />
	{:else if kb}
		<h1 class="mb-1 font-display text-[28px] font-semibold text-ink dark:text-ink-dark">
			{kb.name}
		</h1>
		<p class="mb-6 text-[15px] text-muted dark:text-muted-dark">
			Belongs to {subjectName ?? (kb.scope_type === 'agent' ? 'an agent' : 'a team')}
		</p>

		<button
			type="button"
			onclick={() => fileInput?.click()}
			ondragover={(e) => {
				e.preventDefault();
				dragOver = true;
			}}
			ondragleave={() => (dragOver = false)}
			ondrop={handleDrop}
			class="mb-5 w-full rounded-2xl border-2 border-dashed bg-surface p-10 text-center dark:bg-surface-dark {dragOver
				? 'border-accent dark:border-accent-dark'
				: 'border-line dark:border-line-dark'}"
		>
			<span class="mb-1 block text-[17px] font-semibold text-ink dark:text-ink-dark">
				{uploading ? 'Reading…' : 'Drop markdown, text, or JSON'}
			</span>
			<span class="block text-sm text-muted dark:text-muted-dark">or click to browse</span>
		</button>
		<input
			bind:this={fileInput}
			type="file"
			accept="text/plain,application/json,.txt,.md,.json"
			disabled={uploading}
			onchange={handleFileSelect}
			class="hidden"
		/>
		{#if uploadError}
			<p class="mb-4 text-sm text-danger">{uploadError}</p>
		{/if}
		{#if actionError}
			<p class="mb-4 text-sm text-danger">{actionError}</p>
		{/if}

		{#if documentList.length === 0}
			<p class="py-4 text-center text-base text-muted dark:text-muted-dark">No documents yet.</p>
		{:else}
			<div class="flex flex-col gap-2">
				{#each documentList as doc (doc.id)}
					<div
						class="flex min-h-14 flex-wrap items-center gap-3 rounded-xl border border-line bg-surface px-4.5 py-2 dark:border-line-dark dark:bg-surface-dark"
					>
						<span class="font-mono text-sm font-medium text-ink dark:text-ink-dark">
							{docLabel(doc)}
						</span>
						<StatusPill tone={statusTone[doc.status]} class="h-[22px] text-xs">
							{statusLabel[doc.status]}
						</StatusPill>
						<span class="text-[13px] text-muted dark:text-muted-dark">
							{doc.chunk_count} chunk{doc.chunk_count === 1 ? '' : 's'}
						</span>
						{#if doc.error_message}
							<span class="text-[13px] text-danger">{doc.error_message}</span>
						{/if}
						<button
							type="button"
							onclick={() => handleRemove(doc.id)}
							class="ml-auto text-sm font-medium text-danger hover:underline"
						>
							Remove
						</button>
					</div>
				{/each}
			</div>
		{/if}
	{/if}
</div>
