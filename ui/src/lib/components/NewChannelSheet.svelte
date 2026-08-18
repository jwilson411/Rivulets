<script lang="ts">
	import { channels, type Channel } from '$lib/api/channels';
	import { teams, type Team } from '$lib/api/teams';
	import { defaultChannelTeamId } from '$lib/teamRouting';
	import Button from '$lib/ui/Button.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';

	// #411: create used to be name-only, so the room opened with No team
	// and the first message could not get a reply. Team is picked here;
	// Starter Team is the default when it exists.

	let { onClose, onCreated }: { onClose: () => void; onCreated: (channel: Channel) => void } =
		$props();

	const inputClass =
		'h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark';

	let newName = $state('');
	let teamList = $state<Team[]>([]);
	let selectedTeamId = $state<string | null>(null);
	let teamsReady = $state(false);
	let createBusy = $state(false);
	let createError = $state<string | null>(null);
	// Server create_channel rejects anything outside 3–80; enable Create
	// only when that rule is already met so `hr`/`ai` don't fail after submit.
	let nameReady = $derived(newName.trim().length >= 3 && newName.trim().length <= 80);

	teams
		.list()
		.then((list) => {
			teamList = list;
			selectedTeamId = defaultChannelTeamId(list);
		})
		.catch(() => {
			teamList = [];
			selectedTeamId = null;
		})
		.finally(() => {
			teamsReady = true;
		});

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		const name = newName.trim();
		if (name.length < 3 || name.length > 80) return;
		createBusy = true;
		createError = null;
		try {
			const created = await channels.create(name, undefined, selectedTeamId);
			onCreated(created);
		} catch (err) {
			createError = err instanceof Error ? err.message : "Couldn't create the channel.";
		} finally {
			createBusy = false;
		}
	}
</script>

<Sheet title="New channel" {onClose}>
	<form id="new-channel-form" onsubmit={handleCreate} class="flex flex-col gap-5">
		<div class="flex flex-col gap-2">
			<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="new-channel-name">
				Name
			</label>
			<input
				id="new-channel-name"
				type="text"
				bind:value={newName}
				placeholder="My Channel"
				minlength="3"
				maxlength="80"
				class={inputClass}
			/>
			<p class="text-sm text-muted dark:text-muted-dark">3–80 characters.</p>
		</div>
		<div class="flex flex-col gap-2">
			<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="new-channel-team">
				Team
			</label>
			<select
				id="new-channel-team"
				value={selectedTeamId ?? ''}
				onchange={(e) => (selectedTeamId = (e.target as HTMLSelectElement).value || null)}
				class={inputClass}
			>
				<option value="">No team — Assistant still answers</option>
				{#each teamList as team (team.id)}
					<option value={team.id}>{team.name}</option>
				{/each}
			</select>
			<p class="text-sm text-muted dark:text-muted-dark">
				A team is who can reply. Starter Team is a good default.
			</p>
		</div>
		{#if createError}
			<p class="text-sm text-danger">{createError}</p>
		{/if}
	</form>
	{#snippet footer()}
		<Button variant="secondary" onclick={onClose}>Cancel</Button>
		<Button
			disabled={createBusy || !teamsReady || !nameReady}
			onclick={() =>
				(document.getElementById('new-channel-form') as HTMLFormElement).requestSubmit()}
		>
			Create channel
		</Button>
	{/snippet}
</Sheet>
