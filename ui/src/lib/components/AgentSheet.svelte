<script lang="ts">
	import { resolve } from '$app/paths';
	import { untrack } from 'svelte';
	import {
		agents,
		type Agent,
		type AgentVersion,
		type RoutingRule,
		type RuleType
	} from '$lib/api/agents';
	import type { Provider } from '$lib/api/providers';
	import { auth } from '$lib/api/auth.svelte';
	import type { Tool } from '$lib/api/tools';
	import type { TeamDetail } from '$lib/api/teams';
	import { integrations } from '$lib/api/integrations';
	import {
		inviteGrantMayAssignTool,
		isGoogleIntegrationTool,
		SETTINGS_INTEGRATIONS_SEARCH,
		toolDescriptionLine,
		toolDisplayName,
		toolsByGroup
	} from '$lib/toolCatalog';
	import Button from '$lib/ui/Button.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import SectionLabel from '$lib/ui/SectionLabel.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import StatusPill from '$lib/ui/StatusPill.svelte';
	import {
		describeSpeakRulesList,
		keywordsFromRules,
		speakChoiceFromRules,
		type SpeakChoice
	} from '$lib/teamRouting';
	import ModelPicker from './ModelPicker.svelte';

	// Agent sheet (06-screens.md → Agent sheet, mockup 1j). Everyday fields
	// (including When to speak — #406) up top; tools, fallbacks, schema,
	// unattended use, scopes, history behind "More options" (04's
	// progressive-disclosure table). The list page never shows routing
	// radios. Deleting confirms in a nested sheet, never window.confirm.
	let {
		agent = null,
		providers,
		tools,
		teams,
		scopeCatalog,
		initialTeamIds = [],
		initialRules = [],
		initialToolIds = [],
		initialScopes = [],
		initialPeerTag = '',
		versions = [],
		onClose,
		onSaved
	}: {
		agent?: Agent | null;
		providers: Provider[];
		tools: Tool[];
		teams: TeamDetail[];
		scopeCatalog: string[];
		initialTeamIds?: string[];
		initialRules?: RoutingRule[];
		initialToolIds?: string[];
		initialScopes?: string[];
		initialPeerTag?: string;
		versions?: AgentVersion[];
		onClose: () => void;
		onSaved: () => void;
	} = $props();

	// One-time snapshots: the parent mounts a fresh sheet per open (keyed),
	// so these deliberately don't track the props live.
	let name = $state(untrack(() => agent?.name ?? ''));
	let description = $state(untrack(() => agent?.description ?? ''));
	let instructions = $state(untrack(() => agent?.instructions ?? ''));
	let model = $state(untrack(() => agent?.model ?? 'auto'));
	let teamIds = $state<string[]>(untrack(() => [...initialTeamIds]));
	let fallbackModels = $state(
		untrack(() =>
			(agent?.fallback_models ?? []).map((m) => ({ id: crypto.randomUUID(), value: m }))
		)
	);
	let outputSchemaText = $state(
		untrack(() => (agent?.output_schema ? JSON.stringify(agent.output_schema, null, 2) : ''))
	);
	let selectedToolIds = $state<string[]>(untrack(() => [...initialToolIds]));
	let selectedScopes = $state<string[]>(untrack(() => [...initialScopes]));
	let peerTag = $state(untrack(() => initialPeerTag));
	let unattendedApproved = $state(untrack(() => agent?.approved_for_unattended_tools ?? false));

	// "When to speak" — exclusive everyday radios (copy deck: Always /
	// Only when mentioned / When the message includes…). Generated
	// multi-rule sets (#409) are a fourth "keep current" choice so Save
	// cannot silently collapse them to rules[0].
	const storedRules = untrack(() => initialRules);
	const hadCustomRules = speakChoiceFromRules(storedRules) === 'custom';
	const storedRuleSummary = describeSpeakRulesList(storedRules);
	let ruleType = $state<SpeakChoice>(speakChoiceFromRules(storedRules));
	let keywords = $state(keywordsFromRules(storedRules));

	const isOwner = $derived(auth.grant === 'owner');
	const pickerTools = $derived(isOwner ? tools : tools.filter(inviteGrantMayAssignTool));
	const groupedTools = $derived(toolsByGroup(pickerTools));
	// Invite-grant cannot rewrite a set that already includes owner-only
	// tools: sending those ids 403s, omitting them would strip them.
	const guestCannotRewriteTools = $derived(
		!isOwner &&
			!!agent &&
			initialToolIds.some((id) => {
				const listed = tools.find((tool) => tool.id === id);
				return !listed || !inviteGrantMayAssignTool(listed);
			})
	);

	function assignableToolIds(ids: string[]): string[] {
		return ids.filter((id) => {
			const listed = tools.find((tool) => tool.id === id);
			return listed ? inviteGrantMayAssignTool(listed) : false;
		});
	}

	// null = still loading or the list failed (invite grant 403). Only
	// show the connect hint once we know zero Google accounts exist (#471).
	let googleAccountCount = $state<number | null>(null);
	integrations
		.list()
		.then((accounts) => {
			googleAccountCount = accounts.filter((row) => row.provider === 'google').length;
		})
		.catch(() => {
			googleAccountCount = null;
		});
	const needsGoogleAccount = $derived(googleAccountCount === 0);

	let busy = $state(false);
	let error = $state<string | null>(null);
	let confirmingDelete = $state(false);
	let confirmingReplaceRules = $state(false);
	let deleting = $state(false);

	const inputClass =
		'h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark';

	function toggleTool(toolId: string) {
		selectedToolIds = selectedToolIds.includes(toolId)
			? selectedToolIds.filter((id) => id !== toolId)
			: [...selectedToolIds, toolId];
	}

	function toggleScope(scope: string) {
		selectedScopes = selectedScopes.includes(scope)
			? selectedScopes.filter((s) => s !== scope)
			: [...selectedScopes, scope];
	}

	function toggleTeam(id: string) {
		teamIds = teamIds.includes(id) ? teamIds.filter((teamId) => teamId !== id) : [...teamIds, id];
	}

	function normalizedKeywords(): string {
		return keywords
			.split(',')
			.map((k) => k.trim())
			.filter(Boolean)
			.join(', ');
	}

	function buildRules(): { rule_type: RuleType; pattern: string; priority?: number }[] {
		if (ruleType === 'custom') return [];
		if (ruleType === 'keyword') {
			const list = keywords
				.split(',')
				.map((k) => k.trim())
				.filter(Boolean);
			return [{ rule_type: 'keyword', pattern: JSON.stringify(list), priority: 10 }];
		}
		return [{ rule_type: ruleType, pattern: '', priority: 10 }];
	}

	function rulesNeedWrite(): boolean {
		if (!agent) return true;
		if (ruleType === 'custom') return false;
		const initialChoice = speakChoiceFromRules(storedRules);
		if (initialChoice === 'custom') return true;
		if (ruleType !== initialChoice) return true;
		return ruleType === 'keyword' && normalizedKeywords() !== keywordsFromRules(storedRules);
	}

	function replacementSummary(): string {
		if (ruleType === 'keyword') {
			const list = normalizedKeywords();
			return list ? `When the message includes ${list}` : 'When the message includes…';
		}
		if (ruleType === 'always') return 'Always';
		return 'Only when mentioned';
	}

	async function save() {
		if (!name.trim() || !description.trim() || !instructions.trim() || !model.trim()) {
			error = 'Name, role, instructions, and model are all needed.';
			return;
		}

		if (hadCustomRules && rulesNeedWrite() && !confirmingReplaceRules) {
			confirmingReplaceRules = true;
			return;
		}

		let output_schema: Record<string, unknown> | null = null;
		const trimmedSchema = outputSchemaText.trim();
		if (trimmedSchema) {
			let parsed: unknown;
			try {
				parsed = JSON.parse(trimmedSchema);
			} catch {
				error = 'The reply shape must be valid JSON.';
				return;
			}
			if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
				error = 'The reply shape must be a JSON object.';
				return;
			}
			output_schema = parsed as Record<string, unknown>;
		}

		busy = true;
		error = null;
		try {
			const values = {
				name: name.trim(),
				description: description.trim(),
				instructions: instructions.trim(),
				model: model.trim(),
				fallback_models: fallbackModels.map((f) => f.value.trim()).filter((v) => v !== ''),
				output_schema,
				team_ids: [...teamIds],
				...(isOwner
					? { tool_ids: selectedToolIds }
					: guestCannotRewriteTools
						? {}
						: { tool_ids: assignableToolIds(selectedToolIds) })
			};

			let saved: Agent;
			if (agent) {
				saved = await agents.update(agent.id, {
					...values,
					...(isOwner ? { approved_for_unattended_tools: unattendedApproved } : {})
				});
			} else {
				saved = await agents.create(values);
				if (isOwner && unattendedApproved) {
					await agents.update(saved.id, { approved_for_unattended_tools: true });
				}
			}

			if (rulesNeedWrite()) {
				await agents.setRoutingRules(saved.id, buildRules());
			}
			if (peerTag.trim() || initialPeerTag) {
				await agents.setPeerPreference(saved.id, peerTag.trim() || null);
			}
			// PUT replaces the whole granted set. Owner-only server-side (#472
			// hides the picker from invite-grant so we never fire the 403).
			if (
				isOwner &&
				(agent ? selectedScopes.join() !== initialScopes.join() : selectedScopes.length > 0)
			) {
				await agents.setToolScopes(saved.id, selectedScopes);
			}

			onSaved();
		} catch (err) {
			error = err instanceof Error ? err.message : "Couldn't save this agent. Try again.";
		} finally {
			busy = false;
		}
	}

	async function handleDelete() {
		if (!agent) return;
		deleting = true;
		error = null;
		try {
			await agents.remove(agent.id);
			onSaved();
		} catch {
			error = "Couldn't delete this agent. Try again.";
			confirmingDelete = false;
		} finally {
			deleting = false;
		}
	}

	// #104: reverts instructions/model to a prior version and records the
	// rollback itself as a new version, so history stays diffable.
	async function handleRollback(version: number) {
		if (!agent) return;
		error = null;
		try {
			await agents.rollback(agent.id, version);
			onSaved();
		} catch {
			error = "Couldn't roll back. Try again.";
		}
	}
</script>

{#if !confirmingDelete && !confirmingReplaceRules}
	<Sheet title={agent ? agent.name : 'New agent'} {onClose}>
		<div class="flex flex-col gap-2">
			<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="agent-name">Name</label>
			<input
				id="agent-name"
				type="text"
				bind:value={name}
				placeholder="Writer"
				class={inputClass}
			/>
		</div>

		<div class="flex flex-col gap-2">
			<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="agent-role">
				What this agent does
			</label>
			<input
				id="agent-role"
				type="text"
				bind:value={description}
				placeholder="Drafts and edits prose."
				class={inputClass}
			/>
		</div>

		<div class="flex flex-col gap-2">
			<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="agent-instructions">
				How it should behave
			</label>
			<textarea
				id="agent-instructions"
				rows="6"
				bind:value={instructions}
				class="rounded-lg border border-line bg-surface px-4 py-3 text-base leading-normal text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark"
			></textarea>
		</div>

		<div class="flex flex-col gap-2">
			<span class="text-sm font-semibold text-ink dark:text-ink-dark">Model</span>
			<ModelPicker {providers} bind:value={model} />
		</div>

		<div class="flex flex-col gap-2.5">
			<span class="text-sm font-semibold text-ink dark:text-ink-dark">Team</span>
			{#if teams.length === 0}
				<p class="text-[13px] text-muted dark:text-muted-dark">
					No teams yet. Create one under
					<a
						href={resolve('/teams')}
						class="font-semibold text-accent hover:underline dark:text-accent-dark"
					>
						>Teams</a
					>.
				</p>
			{:else}
				<div class="flex flex-col gap-2" role="group" aria-label="Team">
					{#each teams as team (team.id)}
						<label
							class="flex h-12 cursor-pointer items-center gap-3 rounded-lg border border-line px-4 dark:border-line-dark"
						>
							<input
								type="checkbox"
								checked={teamIds.includes(team.id)}
								onchange={() => toggleTeam(team.id)}
								class="accent-(--color-accent)"
							/>
							<span class="text-[15px] text-ink dark:text-ink-dark">{team.name}</span>
						</label>
					{/each}
				</div>
				<p class="text-[13px] text-muted dark:text-muted-dark">
					An agent can belong to more than one team.
				</p>
			{/if}
		</div>

		<div class="flex flex-col gap-2.5">
			<span class="text-sm font-semibold text-ink dark:text-ink-dark">When to speak</span>
			<div class="flex flex-col gap-2" role="radiogroup" aria-label="When to speak">
				{#each [{ value: 'always', label: 'Always' }, { value: 'mention_only', label: 'Only when mentioned' }, { value: 'keyword', label: 'When the message includes…' }] as option (option.value)}
					<label
						class="flex h-12 cursor-pointer items-center gap-3 rounded-lg border px-4 {ruleType ===
						option.value
							? 'border-accent bg-accent-soft dark:border-accent-dark dark:bg-accent-soft-dark'
							: 'border-line dark:border-line-dark'}"
					>
						<input
							type="radio"
							name="agent-when"
							value={option.value}
							bind:group={ruleType}
							class="accent-(--color-accent)"
						/>
						<span class="text-[15px] text-ink dark:text-ink-dark">{option.label}</span>
					</label>
				{/each}
				{#if hadCustomRules}
					<label
						class="flex min-h-12 cursor-pointer items-start gap-3 rounded-lg border px-4 py-3 {ruleType ===
						'custom'
							? 'border-accent bg-accent-soft dark:border-accent-dark dark:bg-accent-soft-dark'
							: 'border-line dark:border-line-dark'}"
					>
						<input
							type="radio"
							name="agent-when"
							value="custom"
							bind:group={ruleType}
							class="mt-1 accent-(--color-accent)"
						/>
						<span class="flex min-w-0 flex-col gap-1">
							<span class="text-[15px] text-ink dark:text-ink-dark">Keep the current rules</span>
							{#each storedRuleSummary as line (line)}
								<span class="text-[13px] text-muted dark:text-muted-dark">{line}</span>
							{/each}
						</span>
					</label>
				{/if}
			</div>
			{#if ruleType === 'keyword'}
				<input
					type="text"
					bind:value={keywords}
					placeholder="retry, eval, coverage"
					aria-label="Keywords, separated by commas"
					class={inputClass}
				/>
			{/if}
		</div>

		<details class="border-t border-line pt-4 dark:border-line-dark">
			<summary
				class="flex cursor-pointer items-center gap-2 text-base font-medium text-ink dark:text-ink-dark"
			>
				<Icon name="chevron-right" class="h-4 w-4 text-muted dark:text-muted-dark" />
				More options
				<span class="text-sm font-normal text-muted dark:text-muted-dark">
					Tools · Fallbacks · Advanced
				</span>
			</summary>
			<div class="mt-5 flex flex-col gap-6">
				{#if pickerTools.length > 0}
					<div class="flex flex-col gap-2.5">
						<span class="text-sm font-semibold text-ink dark:text-ink-dark">Tools</span>
						{#if selectedToolIds.length === 0}
							<p class="text-[13px] text-muted dark:text-muted-dark">
								{isOwner
									? 'No tools assigned. New agents start with every tool checked.'
									: 'No tools assigned. New agents start with every tool you can assign.'}
							</p>
						{/if}
						<div class="flex flex-col gap-4">
							{#each groupedTools as group (group.key)}
								<div class="flex flex-col gap-2">
									<SectionLabel>{group.label}</SectionLabel>
									{#if group.key === 'integrations' && needsGoogleAccount}
										<p class="text-[13px] leading-snug text-muted dark:text-muted-dark">
											These tools need a connected Google account.
											<a
												href={`${resolve('/settings')}${SETTINGS_INTEGRATIONS_SEARCH}`}
												class="text-accent underline dark:text-accent-dark"
											>
												Settings → Integrations
											</a>
										</p>
									{/if}
									{#each group.tools as tool (tool.id)}
										<label
											class="flex min-h-12 cursor-pointer items-start gap-3 rounded-lg border border-line px-4 py-2.5 dark:border-line-dark"
										>
											<input
												type="checkbox"
												checked={selectedToolIds.includes(tool.id)}
												disabled={guestCannotRewriteTools}
												onchange={() => toggleTool(tool.id)}
												class="mt-1 accent-(--color-accent)"
											/>
											<span class="flex min-w-0 flex-1 flex-col gap-0.5">
												<span class="flex items-center gap-2">
													<span class="text-[15px] text-ink dark:text-ink-dark">
														{toolDisplayName(tool)}
													</span>
													{#if tool.sensitive}
														<StatusPill tone="warn" class="ml-auto">Sensitive</StatusPill>
													{/if}
												</span>
												{#if tool.description}
													<span class="text-[13px] leading-snug text-muted dark:text-muted-dark">
														{toolDescriptionLine(tool.description)}
													</span>
												{/if}
												{#if needsGoogleAccount && isGoogleIntegrationTool(tool.name)}
													<span class="text-[13px] leading-snug text-muted dark:text-muted-dark">
														Needs a connected account
													</span>
												{/if}
											</span>
										</label>
									{/each}
								</div>
							{/each}
						</div>
						<p class="text-[13px] text-muted dark:text-muted-dark">
							{isOwner
								? "New agents start with every tool and permission. Uncheck any you want to hold back. Sensitive tools still need the owner's OK for unattended runs."
								: guestCannotRewriteTools
									? 'This agent already has tools only the owner can assign, so the set stays as-is.'
									: 'New agents start with every tool you can assign. Uncheck any you want to hold back.'}
						</p>
					</div>
				{/if}

				<div class="flex flex-col gap-2.5">
					<div class="flex items-center justify-between">
						<span class="text-sm font-semibold text-ink dark:text-ink-dark">
							Backup models — tried in order if the model above fails
						</span>
						<button
							type="button"
							onclick={() => fallbackModels.push({ id: crypto.randomUUID(), value: '' })}
							class="text-sm font-semibold text-accent hover:underline dark:text-accent-dark"
						>
							Add
						</button>
					</div>
					{#each fallbackModels as fallback, index (fallback.id)}
						<div class="flex items-center gap-2">
							<span class="w-4 text-sm text-muted dark:text-muted-dark">{index + 1}.</span>
							<div class="flex-1">
								<ModelPicker {providers} bind:value={fallback.value} showAuto={false} />
							</div>
							<button
								type="button"
								onclick={() =>
									(fallbackModels = fallbackModels.filter((f) => f.id !== fallback.id))}
								aria-label="Remove backup model"
								class="text-sm text-muted hover:text-danger dark:text-muted-dark"
							>
								Remove
							</button>
						</div>
					{/each}
				</div>

				<details>
					<summary
						class="flex cursor-pointer items-center gap-2 text-sm font-semibold text-ink dark:text-ink-dark"
					>
						<Icon name="chevron-right" class="h-3.5 w-3.5 text-muted dark:text-muted-dark" />
						Advanced
					</summary>
					<div class="mt-4 flex flex-col gap-5">
						<div class="flex flex-col gap-2">
							<span class="text-sm font-semibold text-ink dark:text-ink-dark">
								Reply shape (JSON)
							</span>
							<textarea
								rows="4"
								bind:value={outputSchemaText}
								placeholder="Leave blank for normal replies"
								class="rounded-lg border border-line bg-surface px-4 py-3 font-mono text-[13px] text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark"
							></textarea>
							<p class="text-[13px] text-muted dark:text-muted-dark">
								A JSON Schema object. When set, this agent's replies are constrained to match it.
							</p>
						</div>

						{#if isOwner}
							<label class="flex cursor-pointer items-start gap-3">
								<input
									type="checkbox"
									bind:checked={unattendedApproved}
									class="mt-1 accent-(--color-accent)"
								/>
								<span class="text-[15px] leading-normal text-ink dark:text-ink-dark">
									Let this agent use its sensitive tools when nobody is watching
									<span class="block text-[13px] text-muted dark:text-muted-dark">
										Applies to schedules and automatic runs. Ordinary chat is never affected.
									</span>
								</span>
							</label>
						{/if}

						{#if isOwner && scopeCatalog.length > 0}
							<div class="flex flex-col gap-2">
								<span class="text-sm font-semibold text-ink dark:text-ink-dark">Permissions</span>
								{#each scopeCatalog as scope (scope)}
									<label class="flex cursor-pointer items-center gap-3">
										<input
											type="checkbox"
											checked={selectedScopes.includes(scope)}
											onchange={() => toggleScope(scope)}
											class="accent-(--color-accent)"
										/>
										<span class="font-mono text-sm text-ink dark:text-ink-dark">{scope}</span>
									</label>
								{/each}
								<p class="text-[13px] text-muted dark:text-muted-dark">
									Granting a permission is owner-only. Some tools stay inert until their permission
									is granted.
								</p>
							</div>
						{/if}

						<div class="flex flex-col gap-2">
							<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="agent-peer-tag">
								Preferred machine
							</label>
							<input
								id="agent-peer-tag"
								type="text"
								bind:value={peerTag}
								placeholder="e.g. gpu — blank for no preference"
								class={inputClass}
							/>
							<p class="text-[13px] text-muted dark:text-muted-dark">
								Only matters when this workspace syncs across machines — the agent prefers to run
								where this label is advertised.
							</p>
						</div>

						{#if agent && versions.length > 0}
							<div class="flex flex-col gap-2">
								<span class="text-sm font-semibold text-ink dark:text-ink-dark">History</span>
								{#each versions as version (version.version)}
									<div
										class="flex items-center justify-between gap-2 text-sm text-muted dark:text-muted-dark"
									>
										<span class="truncate">
											v{version.version} · {new Date(version.created_at).toLocaleString()} ·
											<span class="font-mono text-[13px]">{version.model}</span>
										</span>
										<button
											type="button"
											onclick={() => handleRollback(version.version)}
											class="flex-none font-semibold text-accent hover:underline dark:text-accent-dark"
										>
											Roll back
										</button>
									</div>
								{/each}
							</div>
						{/if}
					</div>
				</details>
			</div>
		</details>

		{#if error}
			<p class="text-sm text-danger">{error}</p>
		{/if}

		{#snippet footer()}
			{#if agent}
				<button
					type="button"
					onclick={() => (confirmingDelete = true)}
					class="mr-auto text-[15px] font-medium text-danger hover:underline"
				>
					Delete agent
				</button>
			{/if}
			<Button variant="secondary" onclick={onClose}>Cancel</Button>
			<Button onclick={save} disabled={busy}>
				{busy ? 'Saving…' : agent ? 'Save' : 'Create agent'}
			</Button>
		{/snippet}
	</Sheet>
{/if}

{#if confirmingReplaceRules && agent}
	<Sheet
		title="Replace When to speak?"
		onClose={() => (confirmingReplaceRules = false)}
		width={480}
	>
		<p class="text-base leading-normal text-ink dark:text-ink-dark">
			This agent has {storedRules.length}
			{storedRules.length === 1 ? 'rule' : 'rules'} the form cannot show one-by-one. Saving will replace
			them with: {replacementSummary()}.
		</p>
		{#if error}
			<p class="text-sm text-danger">{error}</p>
		{/if}
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (confirmingReplaceRules = false)}>Cancel</Button>
			<Button onclick={save} disabled={busy}>
				{busy ? 'Saving…' : 'Replace rules'}
			</Button>
		{/snippet}
	</Sheet>
{/if}

{#if confirmingDelete && agent}
	<Sheet title="Delete {agent.name}?" onClose={() => (confirmingDelete = false)} width={480}>
		<p class="text-base leading-normal text-ink dark:text-ink-dark">
			Conversations stay. This agent will stop answering.
		</p>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (confirmingDelete = false)}>Cancel</Button>
			<Button variant="destructive" onclick={handleDelete} disabled={deleting}>
				{deleting ? 'Deleting…' : 'Delete agent'}
			</Button>
		{/snippet}
	</Sheet>
{/if}
