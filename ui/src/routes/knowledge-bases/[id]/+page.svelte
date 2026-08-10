<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import {
		knowledgeBases,
		type KnowledgeBase,
		type KnowledgeBaseDocument
	} from '$lib/api/knowledgeBases';
	import { files } from '$lib/api/files';

	let kb = $state<KnowledgeBase | null>(null);
	let documentList = $state<KnowledgeBaseDocument[]>([]);
	let loadError = $state<string | null>(null);

	let uploading = $state(false);
	let uploadError = $state<string | null>(null);
	let actionError = $state<string | null>(null);
	let fileInput = $state<HTMLInputElement | null>(null);

	async function load(id: string) {
		loadError = null;
		try {
			const [loadedKb, docs] = await Promise.all([
				knowledgeBases.get(id),
				knowledgeBases.listDocuments(id)
			]);
			kb = loadedKb;
			documentList = docs;
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load knowledge base';
		}
	}

	load(page.params.id!);

	async function handleUpload(event: Event) {
		const input = event.target as HTMLInputElement;
		const file = input.files?.[0];
		if (!file || !kb) return;
		uploading = true;
		uploadError = null;
		try {
			const uploaded = await files.upload(file);
			await knowledgeBases.ingestDocument(kb.id, uploaded.file_id);
			await load(kb.id);
		} catch (err) {
			uploadError = err instanceof Error ? err.message : 'Failed to ingest document';
		} finally {
			uploading = false;
			if (fileInput) fileInput.value = '';
		}
	}

	async function handleDeleteDocument(documentId: string) {
		if (!kb) return;
		actionError = null;
		try {
			await knowledgeBases.removeDocument(kb.id, documentId);
			await load(kb.id);
		} catch (err) {
			actionError = err instanceof Error ? err.message : 'Failed to remove document';
		}
	}

	const statusLabel: Record<KnowledgeBaseDocument['status'], string> = {
		pending: 'Pending',
		ingested: 'Ingested',
		failed: 'Failed'
	};
	const statusClass: Record<KnowledgeBaseDocument['status'], string> = {
		pending: 'text-neutral-500',
		ingested: 'text-agent-cyan-700 dark:text-agent-cyan-400',
		failed: 'text-agent-magenta-700 dark:text-agent-magenta-400'
	};
</script>

<div class="mx-auto flex max-w-2xl flex-col gap-8 px-6 py-8">
	<a
		href={resolve('/knowledge-bases')}
		class="text-sm text-neutral-600 hover:underline dark:text-neutral-400"
	>
		&larr; Knowledge Bases
	</a>

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{:else if kb}
		<header>
			<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">{kb.name}</h1>
			<p class="text-sm text-neutral-600 dark:text-neutral-400">
				{kb.scope_type === 'agent' ? 'Agent' : 'Team'}-scoped · {documentList.length} document{documentList.length ===
				1
					? ''
					: 's'}
			</p>
		</header>

		<section
			class="flex flex-col gap-2 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
		>
			<h2 class="text-sm font-semibold text-ink dark:text-ink-dark">Add a document</h2>
			<p class="text-xs text-neutral-600 dark:text-neutral-400">
				v1 supports text files only (plain text, JSON). The file is uploaded, chunked, and embedded
				immediately — large files may take a moment.
			</p>
			<input
				bind:this={fileInput}
				type="file"
				accept="text/plain,application/json,.txt,.md,.json"
				disabled={uploading}
				onchange={handleUpload}
				class="text-sm text-ink dark:text-ink-dark"
			/>
			{#if uploading}
				<p class="text-xs text-neutral-500">Ingesting…</p>
			{/if}
			{#if uploadError}
				<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{uploadError}</p>
			{/if}
		</section>

		{#if actionError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{actionError}</p>
		{/if}

		{#if documentList.length === 0}
			<p class="text-sm text-neutral-500">No documents ingested yet — add one above.</p>
		{:else}
			<ul class="flex flex-col gap-2">
				{#each documentList as doc (doc.id)}
					<li class="rounded-lg border border-ink/12 p-4 dark:border-white/10">
						<div class="flex items-center justify-between">
							<span class="text-sm font-medium {statusClass[doc.status]}">
								{statusLabel[doc.status]}
							</span>
							<button
								onclick={() => handleDeleteDocument(doc.id)}
								class="text-xs text-neutral-500 hover:text-agent-magenta-600"
							>
								Remove
							</button>
						</div>
						<p class="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
							{doc.chunk_count} chunk{doc.chunk_count === 1 ? '' : 's'}
						</p>
						{#if doc.error_message}
							<p class="mt-1 text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
								{doc.error_message}
							</p>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
</div>
