<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { providers, type Provider, type ProviderKind } from '$lib/api/providers';
	import { channels, type Channel } from '$lib/api/channels';
	import { teams } from '$lib/api/teams';
	import { defaultChannelTeamId } from '$lib/teamRouting';
	import Button from '$lib/ui/Button.svelte';
	import Icon from '$lib/ui/Icon.svelte';

	// First-value path (04-information-architecture.md): a reply cannot
	// happen until a provider, a channel, and a first message exist. These
	// three step cards walk an owner through exactly that — shown on the
	// dedicated first-run screen and embedded on Home while setup is
	// incomplete. Completed cards collapse to a check + label; the current
	// step is expanded with a 48px primary.
	let {
		providerList,
		channelList,
		onProviderAdded
	}: {
		providerList: Provider[];
		channelList: Channel[];
		onProviderAdded: () => void;
	} = $props();

	const PROVIDER_CHOICES: { kind: ProviderKind; label: string }[] = [
		{ kind: 'anthropic', label: 'Anthropic' },
		{ kind: 'openai', label: 'OpenAI' },
		{ kind: 'google', label: 'Google AI' },
		{ kind: 'xai', label: 'xAI' },
		{ kind: 'ollama', label: 'Ollama' },
		{ kind: 'openai_compatible', label: 'OpenAI-compatible' }
	];

	let selectedKind = $state<ProviderKind>('anthropic');
	let apiKey = $state('');
	let baseUrl = $state('');
	let saving = $state(false);
	let saveError = $state<string | null>(null);
	let openingChannel = $state(false);
	let channelError = $state<string | null>(null);

	let hasProvider = $derived(providerList.length > 0);
	let generalChannel = $derived(
		channelList.find((c) => c.name === 'general') ?? channelList[0] ?? null
	);
	let hasChannel = $derived(channelList.length > 0);
	let needsAddress = $derived(selectedKind === 'ollama' || selectedKind === 'openai_compatible');
	let providerLabel = $derived(
		PROVIDER_CHOICES.find((c) => c.kind === selectedKind)?.label ?? selectedKind
	);

	async function saveKey(event: SubmitEvent) {
		event.preventDefault();
		saving = true;
		saveError = null;
		try {
			await providers.create({
				provider: selectedKind,
				label: providerLabel,
				// Ollama authenticates with a local address, not a key.
				api_key: selectedKind === 'ollama' ? apiKey || 'ollama' : apiKey,
				base_url: needsAddress && baseUrl ? baseUrl : undefined
			});
			apiKey = '';
			onProviderAdded();
		} catch {
			saveError = 'That key was rejected. Check it and try again.';
		} finally {
			saving = false;
		}
	}

	async function openGeneral() {
		openingChannel = true;
		channelError = null;
		try {
			let target = generalChannel;
			if (!target) {
				// #411: assign the team in the same create so #general is
				// answerable on the first message.
				const teamList = await teams.list().catch(() => []);
				target = await channels.create('general', 'Default room', defaultChannelTeamId(teamList));
			}
			goto(resolve('/channels/[id]', { id: target.id }));
		} catch {
			channelError = "Couldn't open the channel. Try again.";
			openingChannel = false;
		}
	}
</script>

{#snippet doneCard(label: string, hint: string | null)}
	<div
		class="flex min-h-[68px] items-center gap-3.5 rounded-2xl border border-line bg-surface px-6 py-5 dark:border-line-dark dark:bg-surface-dark"
	>
		<span
			class="flex h-7 w-7 flex-none items-center justify-center rounded-full bg-accent text-white dark:bg-accent-dark dark:text-paper-dark"
		>
			<Icon name="check" class="h-[15px] w-[15px]" />
		</span>
		<span class="text-base font-semibold text-ink dark:text-ink-dark">{label}</span>
		{#if hint}
			<span class="ml-auto text-sm text-muted dark:text-muted-dark">{hint}</span>
		{/if}
	</div>
{/snippet}

{#snippet pendingCard(step: number, label: string)}
	<div
		class="flex min-h-[68px] items-center gap-3.5 rounded-2xl border border-line bg-surface px-6 py-5 opacity-55 dark:border-line-dark dark:bg-surface-dark"
	>
		<span
			class="flex h-7 w-7 flex-none items-center justify-center rounded-full border-2 border-line text-sm font-semibold text-muted dark:border-line-dark dark:text-muted-dark"
		>
			{step}
		</span>
		<span class="text-base font-semibold text-ink dark:text-ink-dark">{label}</span>
	</div>
{/snippet}

<div class="flex flex-col gap-4">
	{#if hasProvider}
		{@render doneCard('Add an API key', `${providerList[0].label} · Connected`)}
	{:else}
		<div
			class="rounded-2xl border border-accent bg-surface p-7 shadow-[0_4px_16px_rgba(31,138,112,.08)] dark:border-accent-dark dark:bg-surface-dark"
		>
			<div class="mb-2.5 flex items-center gap-3.5">
				<span
					class="flex h-7 w-7 flex-none items-center justify-center rounded-full border-2 border-accent text-sm font-semibold text-accent dark:border-accent-dark dark:text-accent-dark"
				>
					1
				</span>
				<span class="font-display text-xl font-semibold text-ink dark:text-ink-dark">
					Add an API key
				</span>
			</div>
			<p class="mb-5 ml-[42px] text-base leading-normal text-muted dark:text-muted-dark">
				Agents need a model provider to answer. The key stays on this machine.
			</p>
			<form onsubmit={saveKey} class="ml-[42px] flex flex-col gap-4">
				<div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
					{#each PROVIDER_CHOICES as choice (choice.kind)}
						<button
							type="button"
							onclick={() => (selectedKind = choice.kind)}
							aria-pressed={selectedKind === choice.kind}
							class="flex h-[64px] items-center justify-center rounded-xl bg-surface px-2 text-center text-[15px] dark:bg-surface-dark {selectedKind ===
							choice.kind
								? 'border-2 border-accent font-semibold text-ink dark:border-accent-dark dark:text-ink-dark'
								: 'border border-line font-medium text-ink hover:border-accent dark:border-line-dark dark:text-ink-dark dark:hover:border-accent-dark'}"
						>
							{choice.label}
						</button>
					{/each}
				</div>
				{#if selectedKind !== 'ollama'}
					<label class="flex flex-col gap-2">
						<span class="text-sm font-semibold text-ink dark:text-ink-dark">API key</span>
						<input
							type="password"
							bind:value={apiKey}
							placeholder={selectedKind === 'anthropic' ? 'sk-ant-…' : 'sk-…'}
							class="h-12 rounded-lg border border-line bg-surface px-4 font-mono text-sm text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark"
						/>
					</label>
				{/if}
				{#if needsAddress}
					<label class="flex flex-col gap-2">
						<span class="text-sm font-semibold text-ink dark:text-ink-dark">
							{selectedKind === 'ollama' ? 'Local address' : 'Base address'}
						</span>
						<input
							type="text"
							bind:value={baseUrl}
							placeholder="http://localhost:11434"
							class="h-12 rounded-lg border border-line bg-surface px-4 font-mono text-sm text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark"
						/>
					</label>
				{/if}
				<p class="text-[13px] text-muted dark:text-muted-dark">We won't show this key again.</p>
				{#if saveError}
					<p class="text-sm text-danger">{saveError}</p>
				{/if}
				<Button
					type="submit"
					disabled={saving || (selectedKind !== 'ollama' && !apiKey.trim())}
					class="self-start"
				>
					{saving ? 'Saving…' : 'Save key'}
				</Button>
			</form>
		</div>
	{/if}

	{#if !hasProvider}
		{@render pendingCard(2, 'Pick a room')}
	{:else if hasChannel}
		{@render doneCard('Pick a room', generalChannel ? `#${generalChannel.name}` : null)}
	{:else}
		<div
			class="rounded-2xl border border-accent bg-surface p-7 shadow-[0_4px_16px_rgba(31,138,112,.08)] dark:border-accent-dark dark:bg-surface-dark"
		>
			<div class="mb-2.5 flex items-center gap-3.5">
				<span
					class="flex h-7 w-7 flex-none items-center justify-center rounded-full border-2 border-accent text-sm font-semibold text-accent dark:border-accent-dark dark:text-accent-dark"
				>
					2
				</span>
				<span class="font-display text-xl font-semibold text-ink dark:text-ink-dark">
					Pick a room
				</span>
			</div>
			<p class="mb-6 ml-[42px] text-base leading-normal text-muted dark:text-muted-dark">
				We'll put you in <strong class="text-ink dark:text-ink-dark">#general</strong> with Starter Team
				— Assistant, Coder, Researcher, and Writer.
			</p>
			{#if channelError}
				<p class="mb-3 ml-[42px] text-sm text-danger">{channelError}</p>
			{/if}
			<div class="ml-[42px]">
				<Button onclick={openGeneral} disabled={openingChannel}>Open #general</Button>
			</div>
		</div>
	{/if}

	{#if !hasProvider || !hasChannel}
		{@render pendingCard(3, 'Say something')}
	{:else}
		<div
			class="rounded-2xl border border-accent bg-surface p-7 shadow-[0_4px_16px_rgba(31,138,112,.08)] dark:border-accent-dark dark:bg-surface-dark"
		>
			<div class="mb-2.5 flex items-center gap-3.5">
				<span
					class="flex h-7 w-7 flex-none items-center justify-center rounded-full border-2 border-accent text-sm font-semibold text-accent dark:border-accent-dark dark:text-accent-dark"
				>
					3
				</span>
				<span class="font-display text-xl font-semibold text-ink dark:text-ink-dark">
					Say something
				</span>
			</div>
			<p class="mb-6 ml-[42px] text-base leading-normal text-muted dark:text-muted-dark">
				Ask the team a real question. They'll jump in if it's relevant.
			</p>
			<div class="ml-[42px]">
				<Button onclick={openGeneral} disabled={openingChannel}>
					Take me to #{generalChannel?.name ?? 'general'}
				</Button>
			</div>
		</div>
	{/if}
</div>
