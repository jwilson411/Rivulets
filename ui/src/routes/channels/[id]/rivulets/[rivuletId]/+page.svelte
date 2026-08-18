<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { rivulets, type Rivulet, type Message } from '$lib/api/rivulets';
	import { channels, type Channel } from '$lib/api/channels';
	import { teams, type Team, type TeamDetail } from '$lib/api/teams';
	import { agents, type Agent } from '$lib/api/agents';
	import { workflows, type Workflow } from '$lib/api/workflows';
	import { runs, type RunTrace } from '$lib/api/runs';
	import { files as filesApi } from '$lib/api/files';
	import { sync } from '$lib/api/sync';
	import { auth } from '$lib/api/auth.svelte';
	import { agentInkMap, INK_AVATAR, INK_BUBBLE, HUMAN_AVATAR, type AgentInk } from '$lib/ink';
	import { formatBytes, formatClock } from '$lib/format';
	import { mentionNamesOf, type MentionCandidate } from '$lib/mentions';
	import { isTeamEngaged, lockedTeamComposerHint, teamComposerHint } from '$lib/teamRouting';
	import { renderMarkdown } from '$lib/markdown';
	import WorkingDirectoryControl from '$lib/components/WorkingDirectoryControl.svelte';
	import Button from '$lib/ui/Button.svelte';
	import Disc from '$lib/ui/Disc.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import StreamBar from '$lib/ui/StreamBar.svelte';

	// Rivulet page (06-screens.md → Rivulet, mockups 1g/2o): the full
	// conversation. The Stream Bar here REPLIES in place — new conversations
	// are started from the channel page. Back goes to the channel, not home.

	let channel = $state<Channel | null>(null);
	let rivulet = $state<Rivulet | null>(null);
	let messages = $state<Message[]>([]);
	let teamList = $state<Team[]>([]);
	let teamMembers = $state<Agent[]>([]);
	let workflowList = $state<Workflow[]>([]);
	let latestRun = $state<RunTrace | null>(null);
	let loadError = $state<string | null>(null);
	let sending = $state(false);
	let sendError = $state<string | null>(null);
	let resuming = $state(false);
	let resumeError = $state<string | null>(null);
	let engaging = $state(false);
	let engageError = $state<string | null>(null);
	let confirmingArchive = $state(false);
	let archiveBusy = $state(false);
	let archiveError = $state<string | null>(null);
	let folderBusy = $state(false);
	let folderError = $state<string | null>(null);
	let isOwner = $derived(auth.grant === 'owner');
	let downloadError = $state<string | null>(null);
	let scroller = $state<HTMLDivElement | null>(null);
	// Issue #10: this node's own node_id, fetched once, so a remotely
	// executed reply's "ran on" note only shows when it actually differs
	// -- avoids noise on the common (local-execution) case.
	let myNodeId = $state<string | null>(null);

	sync
		.status()
		.then((status) => {
			myNodeId = status.node_id;
		})
		.catch(() => {}); // best-effort -- note just stays hidden if this fails

	function shortNodeId(id: string): string {
		return id.length > 16 ? `${id.slice(0, 8)}…${id.slice(-6)}` : id;
	}

	async function handleDownload(attachment: Message['attachments'][number]) {
		downloadError = null;
		try {
			await filesApi.download(attachment.file_id, attachment.filename);
		} catch {
			downloadError = "Couldn't download that file. Try again.";
		}
	}

	// R-9's "agent status indicators" (#30): what an agent is doing before
	// its first token streams — set from agent_status events, cleared to
	// 'streaming' the moment real content starts arriving.
	type LiveStatus = 'routing' | 'thinking' | 'executing_tool' | 'waiting_for_handoff' | 'streaming';

	// Filled in token-by-token from the SSE stream below (FR-12.3) while an
	// agent is mid-run, then cleared once its agent_message event lands —
	// at which point the message is already in `messages` too. #413: the
	// human POST returns before dispatch finishes, so this row also starts
	// from an optimistic "Routing…" the instant Send is pressed (and from
	// dispatch_status / a still-running latestRun when landing mid-round).
	let liveMessage = $state<{
		agentId: string;
		agentName: string;
		content: string;
		status: LiveStatus;
		statusDetail: string | null;
	} | null>(null);

	// Copy deck: "Routing…" / "Thinking…" / "Using web search…" / "Handing off…".
	// Never renders tool args, only the tool name the backend already limited
	// itself to forwarding.
	function statusLabel(status: LiveStatus, detail: string | null): string {
		if (status === 'routing') return 'Routing…';
		if (status === 'executing_tool') return detail ? `Using ${detail}…` : 'Using a tool…';
		if (status === 'waiting_for_handoff') return 'Handing off…';
		return 'Thinking…';
	}

	function routingLive(): NonNullable<typeof liveMessage> {
		return {
			agentId: '',
			agentName: '',
			content: '',
			status: 'routing',
			statusDetail: null
		};
	}

	function runIsRecentlyRunning(run: RunTrace | null): boolean {
		if (run?.status !== 'running') return false;
		const started = Date.parse(run.started_at);
		return Number.isFinite(started) && Date.now() - started < 5 * 60 * 1000;
	}

	function ensureRoutingStatus() {
		if (liveMessage) return;
		if (!runIsRecentlyRunning(latestRun)) return;
		const last = [...messages]
			.reverse()
			.find((m) => m.content_type === 'text' || m.content_type === 'system_alert');
		if (last?.sender_type === 'human') liveMessage = routingLive();
	}

	let pauseNotice = $derived(
		[...messages].reverse().find((m) => m.content_type === 'system_alert')?.content ?? null
	);

	// Includes the currently-streaming agent (if any) so it gets a stable ink
	// immediately, before its message is persisted to `messages`.
	let inkMap = $derived(
		agentInkMap([
			...messages,
			...(liveMessage?.agentId
				? [{ sender_type: 'agent' as const, sender_id: liveMessage.agentId }]
				: [])
		])
	);

	let title = $derived(
		rivulet?.title || messages.find((m) => m.sender_type === 'human')?.content || 'Conversation'
	);

	// inkMap already holds one entry per distinct agent sender_id, in
	// first-appearance order — just attach each one's display name.
	let participants = $derived.by(() => {
		const list: { name: string; ink: AgentInk }[] = [];
		for (const [senderId, ink] of inkMap) {
			const name = messages.find((m) => m.sender_id === senderId)?.sender_name;
			if (name) list.push({ name, ink });
		}
		return list;
	});

	let humanName = $derived(messages.find((m) => m.sender_type === 'human')?.sender_name ?? null);

	let routedTeam = $derived(teamList.find((t) => t.id === channel?.team_id) ?? null);
	let mentionCandidates = $derived<MentionCandidate[]>(
		teamMembers.map((member) => ({ id: member.id, name: member.name, kind: 'agent' }))
	);
	let mentionNames = $derived(mentionNamesOf(mentionCandidates));
	let teamEngaged = $derived(isTeamEngaged(messages));
	let helper = $derived(
		!teamEngaged
			? `${lockedTeamComposerHint()} · type @ to mention someone anyway`
			: routedTeam
				? `${teamComposerHint(routedTeam.name)} · type @ to mention · type / to run a workflow`
				: 'Assistant is listening'
	);

	async function loadTeamMembers(teamId: string | null) {
		teamMembers = [];
		try {
			const agentList = await agents.list();
			const byId = new Map(agentList.map((agent) => [agent.id, agent]));
			if (teamId) {
				const detail = (await teams.get(teamId)) as TeamDetail;
				teamMembers = detail.agent_ids
					.map((id) => byId.get(id))
					.filter((a): a is Agent => a !== undefined);
			}
			const assistant = agentList.find((agent) => agent.name.toLowerCase() === 'assistant');
			if (assistant && !teamMembers.some((member) => member.id === assistant.id)) {
				teamMembers = [assistant, ...teamMembers];
			}
		} catch {
			// Picker just stays empty — typing the exact name still works.
		}
	}

	async function load(rivuletId: string, channelId: string) {
		loadError = null;
		try {
			const [
				loadedChannel,
				loadedRivulet,
				loadedMessages,
				loadedTeams,
				loadedWorkflows,
				loadedRuns
			] = await Promise.all([
				channels.get(channelId),
				rivulets.get(rivuletId),
				rivulets.listMessages(rivuletId),
				teams.list().catch(() => [] as Team[]),
				workflows.list().catch(() => [] as Workflow[]),
				runs.list({ rivuletId, limit: 1 }).catch(() => [] as RunTrace[])
			]);
			channel = loadedChannel;
			rivulet = loadedRivulet;
			messages = loadedMessages;
			teamList = loadedTeams;
			workflowList = loadedWorkflows;
			latestRun = loadedRuns[0] ?? null;
			await loadTeamMembers(loadedChannel.team_id);
			ensureRoutingStatus();
		} catch {
			loadError = "Couldn't load this conversation.";
		}
	}

	$effect(() => {
		load(page.params.rivuletId!, page.params.id!);
	});

	// Keep the newest message in view as replies arrive or stream in.
	$effect(() => {
		void messages.length;
		void liveMessage?.content;
		if (scroller) scroller.scrollTop = scroller.scrollHeight;
	});

	$effect(() => {
		const rivuletId = page.params.rivuletId!;
		liveMessage = null;
		if (!auth.token) return;

		let source: EventSource | null = null;
		let cancelled = false;

		// EventSource can't set an Authorization header, so *some* token has
		// to go in the query string for this one endpoint (api/deps.py's
		// get_current_workspace_id_for_stream) — everything else keeps
		// header-only auth. mintStreamTicket() exchanges the real session
		// token for a short-lived, single-purpose one first, so what
		// actually ends up in this URL (and therefore in server logs/
		// browser history) is far less valuable than the session token
		// itself would be.
		auth
			.mintStreamTicket()
			.then((ticket) => {
				if (cancelled) return;
				source = connectStream(rivuletId, ticket);
			})
			.catch(() => {}); // best-effort -- live streaming just won't start this time

		return () => {
			cancelled = true;
			source?.close();
		};
	});

	function connectStream(rivuletId: string, ticket: string): EventSource {
		const source = new EventSource(
			`/api/v1/rivulets/${rivuletId}/stream?token=${encodeURIComponent(ticket)}`
		);

		// #413: published as soon as the human message is committed, before
		// any agent is matched — upgrades the optimistic Routing… row (or
		// starts one if we landed mid-round).
		source.addEventListener('dispatch_status', (event) => {
			const data = JSON.parse((event as MessageEvent).data) as { status?: string };
			if (data.status !== 'routing') return;
			if (!liveMessage || liveMessage.status === 'routing') {
				liveMessage = routingLive();
			}
		});

		// Arrives before an agent's first token (invocation, tool calls,
		// handoff) — see run_agent's on_status in agentos/service.py. Content
		// deltas below take over as soon as they start arriving, so this
		// only ever drives the pre-content status pill.
		source.addEventListener('agent_status', (event) => {
			const data = JSON.parse((event as MessageEvent).data);
			if (!liveMessage || liveMessage.agentId !== data.agent_id) {
				liveMessage = {
					agentId: data.agent_id,
					agentName: data.agent_name,
					content: '',
					status: data.status,
					statusDetail: data.detail
				};
			} else {
				liveMessage.status = data.status;
				liveMessage.statusDetail = data.detail;
			}
		});

		source.addEventListener('agent_token', (event) => {
			const data = JSON.parse((event as MessageEvent).data);
			if (!liveMessage || liveMessage.agentId !== data.agent_id) {
				liveMessage = {
					agentId: data.agent_id,
					agentName: data.agent_name,
					content: '',
					status: 'streaming',
					statusDetail: null
				};
			}
			liveMessage.content += data.token;
			liveMessage.status = 'streaming';
		});

		source.addEventListener('agent_message', (event) => {
			// Persist the reply into `messages` before dropping the live
			// bubble — otherwise the streamed text vanishes until the
			// post-POST refetch lands (show / blink away / show again).
			const data = JSON.parse((event as MessageEvent).data) as {
				agent_id?: string;
				agent_name?: string;
				message_id?: string;
				content?: string;
			};
			if (data.message_id && !messages.some((m) => m.id === data.message_id)) {
				const persisted: Message = {
					id: data.message_id,
					rivulet_id: rivuletId,
					sender_type: 'agent',
					sender_id: data.agent_id ?? liveMessage?.agentId ?? null,
					sender_name: data.agent_name ?? liveMessage?.agentName ?? 'Agent',
					content: data.content ?? liveMessage?.content ?? '',
					content_type: 'text',
					created_at: new Date().toISOString(),
					attachments: [],
					model_used: null,
					tier: null,
					executed_node_id: null,
					served_model: null
				};
				messages = [...messages, persisted];
			}
			liveMessage = null;
		});

		source.addEventListener('system_alert', () => {
			void refreshAfterDispatch(rivuletId);
		});

		// The handoff message itself is a persisted Message row (content_type
		// 'handoff', rendered as a divider below). Stop showing a stale
		// "still typing" bubble for the handing-off agent once it fires;
		// the next agent's agent_status will start a new live row.
		source.addEventListener('handoff', () => {
			liveMessage = null;
		});

		// 'error' is both api-design.md's custom event name AND EventSource's
		// own reserved name for connection-level failures, so this also
		// fires on plain network hiccups, not just our agent-run-failed
		// payloads — deliberately not parsing event.data here since its
		// shape differs between the two cases. Agent-failure paths persist
		// a system_alert Message (#405); refetch so that row replaces
		// Thinking… instead of leaving a silent empty transcript (#413).
		source.addEventListener('error', () => {
			void refreshAfterDispatch(rivuletId);
		});

		source.addEventListener('done', () => {
			void refreshAfterDispatch(rivuletId);
		});

		return source;
	}

	async function refreshAfterDispatch(rivuletId: string) {
		liveMessage = null;
		try {
			const [loadedMessages, loadedRivulet, loadedRuns] = await Promise.all([
				rivulets.listMessages(rivuletId),
				rivulets.get(rivuletId),
				runs.list({ rivuletId, limit: 1 }).catch(() => [] as RunTrace[])
			]);
			messages = loadedMessages;
			rivulet = loadedRivulet;
			latestRun = loadedRuns[0] ?? null;
		} catch {
			// Keep whatever is already on screen — the next event or a
			// manual retry can recover.
		}
	}

	async function handleReply(text: string, files: File[]): Promise<boolean> {
		const rivuletId = page.params.rivuletId!;
		sending = true;
		sendError = null;
		const pendingId = `pending-${crypto.randomUUID()}`;
		const pending: Message = {
			id: pendingId,
			rivulet_id: rivuletId,
			sender_type: 'human',
			sender_id: auth.humanId,
			sender_name: auth.displayName ?? humanName ?? 'You',
			content: text,
			content_type: 'text',
			created_at: new Date().toISOString(),
			attachments: files.map((file, index) => ({
				file_id: `pending-file-${index}`,
				filename: file.name,
				mime_type: file.type || 'application/octet-stream',
				size_bytes: file.size
			})),
			model_used: null,
			tier: null,
			executed_node_id: null,
			served_model: null
		};
		messages = [...messages, pending];
		if (!liveMessage) liveMessage = routingLive();
		try {
			// Uploads happen first, as their own step — a file has to exist on
			// the server (POST /files/upload) before it can be referenced by
			// file_id in the message body that attaches it.
			const uploaded = await Promise.all(files.map((f) => filesApi.upload(f)));
			// POST returns the human message as soon as it is committed;
			// dispatch continues in the background and arrives over SSE (#413).
			const posted = await rivulets.postMessage(
				rivuletId,
				text,
				uploaded.map((f) => f.file_id)
			);
			const loaded = await rivulets.listMessages(rivuletId);
			messages = loaded.some((m) => m.id === posted.id)
				? loaded
				: [...loaded.filter((m) => m.id !== pendingId), posted];
			const last = messages.at(-1);
			if (
				liveMessage?.status === 'routing' &&
				last &&
				(last.sender_type !== 'human' || last.content_type === 'system_alert')
			) {
				liveMessage = null;
			}
			return true;
		} catch {
			messages = messages.filter((m) => m.id !== pendingId);
			if (liveMessage?.status === 'routing') liveMessage = null;
			sendError = "Couldn't send that. Try again.";
			return false;
		} finally {
			sending = false;
		}
	}

	async function handleEngageTeam() {
		const rivuletId = page.params.rivuletId!;
		engaging = true;
		engageError = null;
		try {
			await rivulets.engageTeam(rivuletId);
			messages = await rivulets.listMessages(rivuletId);
			if (!liveMessage) liveMessage = routingLive();
		} catch {
			engageError = "Couldn't engage the team. Try again.";
		} finally {
			engaging = false;
		}
	}

	async function handleResume() {
		const rivuletId = page.params.rivuletId!;
		const wasPaused = rivulet?.status === 'paused';
		resuming = true;
		resumeError = null;
		try {
			rivulet = await rivulets.resume(rivuletId);
			messages = await rivulets.listMessages(rivuletId);
			// Loop-guard resume continues dispatch in the background — show
			// Routing… the same way Send does so the thread isn't silent
			// until the first SSE event arrives.
			if (wasPaused && !liveMessage) liveMessage = routingLive();
		} catch {
			resumeError =
				rivulet?.status === 'closed'
					? "Couldn't unarchive this conversation. Try again."
					: "Couldn't resume this conversation. Try again.";
		} finally {
			resuming = false;
		}
	}

	async function handleWorkingDirectory(path: string | null) {
		if (!rivulet) return;
		folderBusy = true;
		folderError = null;
		try {
			rivulet = await rivulets.update(rivulet.id, { working_directory: path });
		} catch {
			folderError = "Couldn't save that folder. Try again.";
		} finally {
			folderBusy = false;
		}
	}

	async function handleArchive() {
		const rivuletId = page.params.rivuletId!;
		archiveBusy = true;
		archiveError = null;
		try {
			await rivulets.close(rivuletId);
			confirmingArchive = false;
			goto(resolve('/channels/[id]', { id: page.params.id! }));
		} catch {
			archiveError = "Couldn't archive this conversation. Try again.";
		} finally {
			archiveBusy = false;
		}
	}
</script>

<div class="flex h-full flex-col">
	<header class="border-b border-line px-4 pt-7 pb-5 md:px-10 dark:border-line-dark">
		<a
			href={resolve('/channels/[id]', { id: page.params.id! })}
			class="mb-2.5 flex items-center gap-2 text-sm font-semibold text-accent hover:text-accent-deep dark:text-accent-dark"
		>
			<Icon name="back" class="h-4 w-4" />
			{channel ? `#${channel.name}` : 'Back'}
		</a>
		<div class="flex items-start justify-between gap-4">
			<div
				class="max-w-[68ch] font-display text-xl leading-snug font-semibold text-ink dark:text-ink-dark"
			>
				{title}
			</div>
			<div class="flex flex-none flex-col items-end gap-2">
				{#if rivulet && rivulet.status !== 'closed'}
					<Button variant="secondary" size="md" onclick={() => (confirmingArchive = true)}>
						Archive
					</Button>
				{/if}
				<WorkingDirectoryControl
					storedPath={rivulet?.working_directory ?? null}
					inheritedPath={rivulet?.working_directory
						? null
						: (rivulet?.effective_working_directory ??
							channel?.effective_working_directory ??
							null)}
					inheritedLabel="channel default"
					canEdit={isOwner && rivulet?.status !== 'closed'}
					busy={folderBusy}
					error={folderError}
					onSave={handleWorkingDirectory}
				/>
			</div>
		</div>
		{#if latestRun?.status === 'running' || latestRun?.status === 'error' || latestRun?.status === 'cancelled'}
			<p class="mt-2 text-sm {latestRun.status === 'error' ? 'text-danger' : 'text-warn'}">
				{#if latestRun.status === 'running' && latestRun.span_count === 0}
					Last run is still marked running with no steps.
				{:else if latestRun.status === 'running'}
					A run is still in progress.
				{:else if latestRun.status === 'error'}
					Last run failed.
				{:else}
					Last run was cancelled.
				{/if}
			</p>
		{/if}
		{#if humanName || participants.length}
			<div class="mt-3.5 flex flex-wrap gap-2">
				{#if humanName}
					<span
						class="flex h-7 items-center gap-1.5 rounded-full border border-line bg-surface py-0 pr-2.5 pl-1 dark:border-line-dark dark:bg-surface-dark"
					>
						<Disc name={humanName} colorClass={HUMAN_AVATAR} size={20} />
						<span class="text-[13px] font-medium text-ink dark:text-ink-dark">{humanName}</span>
					</span>
				{/if}
				{#each participants as p (p.name)}
					<span
						class="flex h-7 items-center gap-1.5 rounded-full border border-line bg-surface py-0 pr-2.5 pl-1 dark:border-line-dark dark:bg-surface-dark"
					>
						<Disc name={p.name} colorClass={INK_AVATAR[p.ink]} size={20} />
						<span class="text-[13px] font-medium text-ink dark:text-ink-dark">{p.name}</span>
					</span>
				{/each}
			</div>
		{/if}
	</header>

	{#if rivulet?.status === 'closed'}
		<div class="px-4 pt-5 md:px-10">
			<div
				class="flex flex-wrap items-center gap-3.5 rounded-xl border border-line bg-surface px-5 py-4 dark:border-line-dark dark:bg-surface-dark"
			>
				<span class="text-[15px] font-semibold text-ink dark:text-ink-dark">
					This conversation is archived.
				</span>
				<Button variant="secondary" class="ml-auto" onclick={handleResume} disabled={resuming}>
					{resuming ? 'Unarchiving…' : 'Unarchive'}
				</Button>
			</div>
			{#if resumeError}
				<p class="mt-2 text-sm text-danger">{resumeError}</p>
			{/if}
		</div>
	{:else if rivulet?.status === 'paused'}
		<div class="px-4 pt-5 md:px-10">
			<div
				class="flex flex-wrap items-center gap-3.5 rounded-xl border border-warn-line bg-warn-soft px-5 py-4 dark:border-warn-line-dark dark:bg-warn-soft-dark"
			>
				<span class="text-[15px] font-semibold text-warn">This conversation is paused.</span>
				{#if pauseNotice}
					<span class="text-sm text-warn-ink dark:text-warn-ink-dark">{pauseNotice}</span>
				{/if}
				<Button variant="warn" class="ml-auto" onclick={handleResume} disabled={resuming}>
					{resuming ? 'Resuming…' : 'Resume'}
				</Button>
			</div>
			{#if resumeError}
				<p class="mt-2 text-sm text-danger">{resumeError}</p>
			{/if}
		</div>
	{:else if !teamEngaged && rivulet}
		<div class="px-4 pt-5 md:px-10">
			<div
				class="flex flex-wrap items-center gap-3.5 rounded-xl border border-line bg-surface px-5 py-4 dark:border-line-dark dark:bg-surface-dark"
			>
				<span class="text-[15px] font-semibold text-ink dark:text-ink-dark">
					Assistant is gathering context.
				</span>
				<span class="text-sm text-muted dark:text-muted-dark">
					Other agents stay quiet until the team is engaged. @mention someone to call them in now.
				</span>
				<Button variant="secondary" class="ml-auto" onclick={handleEngageTeam} disabled={engaging}>
					{engaging ? 'Engaging…' : 'Engage team'}
				</Button>
			</div>
			{#if engageError}
				<p class="mt-2 text-sm text-danger">{engageError}</p>
			{/if}
		</div>
	{/if}

	<div bind:this={scroller} class="flex-1 overflow-y-auto px-4 py-7 md:px-10">
		{#if loadError}
			<ErrorBanner
				message={loadError}
				onRetry={() => load(page.params.rivuletId!, page.params.id!)}
			/>
		{:else}
			{#if downloadError}
				<p class="mb-3 text-sm text-danger">{downloadError}</p>
			{/if}
			<div class="flex flex-col gap-6">
				{#each messages as message (message.id)}
					{#if message.content_type === 'handoff'}
						<!-- Handoff as a first-class event: dashed agent-b rule + the
						     words "Handed off", never just another chat bubble. -->
						<div class="flex items-center gap-3.5 px-4 md:px-11">
							<span class="flex-1 border-t-2 border-dashed border-agent-b opacity-50"></span>
							<span class="text-[13px] whitespace-nowrap text-muted dark:text-muted-dark">
								<strong class="text-agent-b">Handed off</strong> · {message.content}
							</span>
							<span class="flex-1 border-t-2 border-dashed border-agent-b opacity-50"></span>
						</div>
					{:else if message.content_type === 'team_engaged'}
						<div class="flex items-center gap-3.5 px-4 md:px-11">
							<span class="flex-1 border-t-2 border-dashed border-agent-b opacity-50"></span>
							<span class="text-[13px] whitespace-nowrap text-muted dark:text-muted-dark">
								<strong class="text-agent-b">Team engaged</strong> · {message.content}
							</span>
							<span class="flex-1 border-t-2 border-dashed border-agent-b opacity-50"></span>
						</div>
					{:else if message.content_type === 'workflow_step'}
						<!-- Workflow steps read as a quiet rail event, not a bubble. -->
						<div
							class="flex items-center gap-2.5 px-4 font-mono text-[13px] text-muted md:px-11 dark:text-muted-dark"
						>
							<span aria-hidden="true">▶</span>
							{message.content}
						</div>
					{:else if message.content_type === 'system_alert' || message.sender_type === 'system'}
						<div
							class="max-w-[68ch] rounded-xl border border-warn-line bg-warn-soft px-4.5 py-3 text-[15px] text-warn-ink dark:border-warn-line-dark dark:bg-warn-soft-dark dark:text-warn-ink-dark"
						>
							{message.content}
						</div>
					{:else}
						{@const ink =
							message.sender_type === 'agent' && message.sender_id
								? inkMap.get(message.sender_id)
								: null}
						<div class="flex max-w-[68ch] gap-3.5">
							<Disc
								name={message.sender_name}
								colorClass={ink ? INK_AVATAR[ink] : HUMAN_AVATAR}
								size={32}
							/>
							<div class="min-w-0">
								<div class="mb-1 text-sm">
									<strong class="text-ink dark:text-ink-dark">{message.sender_name}</strong>
									<span class="text-muted dark:text-muted-dark">
										· {formatClock(message.created_at)}</span
									>
									{#if message.model_used}
										<span
											class="cursor-help font-mono text-xs text-muted dark:text-muted-dark"
											title="This reply was generated by {message.model_used}, chosen automatically for {message.tier ===
											'cheap'
												? 'cost'
												: 'quality'} based on how complex your message seemed."
										>
											· {message.model_used}
										</span>
									{/if}
									{#if message.executed_node_id && message.executed_node_id !== myNodeId}
										<span
											class="cursor-help text-xs text-muted dark:text-muted-dark"
											title="This reply ran on a different machine in your synced workspace (id: {message.executed_node_id}), not this one."
										>
											· ran on {shortNodeId(message.executed_node_id)}
										</span>
									{/if}
									{#if message.served_model}
										<span
											class="cursor-help font-mono text-xs text-warn"
											title="The configured model failed with a retryable error, so this reply came from its backup model instead."
										>
											· backup: {message.served_model}
										</span>
									{/if}
								</div>
								{#if message.sender_type === 'agent'}
									<div
										class="msg-md rounded-tl-[4px] rounded-tr-[18px] rounded-b-[18px] px-4.5 py-3.5 text-base leading-normal text-ink dark:text-ink-dark {ink
											? INK_BUBBLE[ink]
											: 'bg-surface dark:bg-surface-dark'}"
									>
										<!-- eslint-disable-next-line svelte/no-at-html-tags -- renderMarkdown escapes all input before building tags -->
										{@html renderMarkdown(message.content, mentionNames)}
									</div>
								{:else}
									<div class="msg-md text-base leading-normal text-ink dark:text-ink-dark">
										<!-- eslint-disable-next-line svelte/no-at-html-tags -- renderMarkdown escapes all input before building tags -->
										{@html renderMarkdown(message.content, mentionNames)}
									</div>
								{/if}
								{#if message.attachments.length > 0}
									<div class="mt-2 flex flex-col gap-2">
										{#each message.attachments as attachment (attachment.file_id)}
											<button
												onclick={() => handleDownload(attachment)}
												class="flex h-12 w-fit items-center gap-2.5 rounded-lg border border-line bg-surface px-4 text-left hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
											>
												<Icon
													name="attach"
													class="h-4 w-4 flex-none text-muted dark:text-muted-dark"
												/>
												<span class="font-mono text-sm text-ink dark:text-ink-dark">
													{attachment.filename}
												</span>
												<span class="text-[13px] text-muted dark:text-muted-dark">
													{formatBytes(attachment.size_bytes)}
												</span>
											</button>
										{/each}
									</div>
								{/if}
							</div>
						</div>
					{/if}
				{/each}

				{#if liveMessage}
					{@const ink = inkMap.get(liveMessage.agentId)}
					<div class="flex max-w-[68ch] gap-3.5">
						{#if liveMessage.agentName}
							<Disc
								name={liveMessage.agentName}
								colorClass={ink ? INK_AVATAR[ink] : HUMAN_AVATAR}
								size={32}
							/>
						{/if}
						<div class="min-w-0">
							<div class="mb-1.5 flex items-center gap-2.5 text-sm">
								{#if liveMessage.agentName}
									<strong class="text-ink dark:text-ink-dark">{liveMessage.agentName}</strong>
								{/if}
								{#if liveMessage.status !== 'streaming' || !liveMessage.content}
									<span
										class="flex h-6 items-center gap-1.5 rounded-full bg-accent-soft px-2.5 text-[13px] font-semibold text-accent dark:bg-accent-soft-dark dark:text-accent-dark"
									>
										<span class="breath h-1.5 w-1.5 rounded-full bg-current"></span>
										{statusLabel(liveMessage.status, liveMessage.statusDetail)}
									</span>
								{/if}
							</div>
							{#if liveMessage.content}
								<div
									class="rounded-tl-[4px] rounded-tr-[18px] rounded-b-[18px] px-4.5 py-3.5 text-base leading-normal whitespace-pre-wrap text-ink dark:text-ink-dark {ink
										? INK_BUBBLE[ink]
										: 'bg-surface dark:bg-surface-dark'}"
								>
									{liveMessage.content}<span
										class="caret-blink ml-0.5 inline-block h-[18px] w-0.5 translate-y-[3px] bg-accent dark:bg-accent-dark"
									></span>
								</div>
							{/if}
						</div>
					</div>
				{/if}
			</div>
		{/if}
	</div>

	{#if rivulet?.status !== 'closed'}
		<div class="px-4 pb-24 md:px-10 md:pb-7">
			<StreamBar
				placeholder="Reply to this conversation…"
				{helper}
				busy={sending}
				error={sendError}
				slashWorkflows={workflowList}
				{mentionCandidates}
				onSend={handleReply}
			/>
		</div>
	{/if}
</div>

{#if confirmingArchive}
	<Sheet title="Archive this conversation?" onClose={() => (confirmingArchive = false)} width={480}>
		<p class="text-base leading-normal text-ink dark:text-ink-dark">
			It leaves the channel list. You can find it under Archived and restore it.
		</p>
		{#if archiveError}
			<p class="text-sm text-danger">{archiveError}</p>
		{/if}
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (confirmingArchive = false)}>Cancel</Button>
			<Button variant="destructive" onclick={handleArchive} disabled={archiveBusy}>
				{archiveBusy ? 'Archiving…' : 'Archive'}
			</Button>
		{/snippet}
	</Sheet>
{/if}

<style>
	/* Markdown inside chat messages: readable, not a blog. */
	.msg-md :global(p) {
		margin: 0;
	}
	.msg-md :global(p + p),
	.msg-md :global(ul),
	.msg-md :global(ol),
	.msg-md :global(pre),
	.msg-md :global(blockquote),
	.msg-md :global(h1),
	.msg-md :global(h2),
	.msg-md :global(h3) {
		margin-top: 0.6em;
	}
	.msg-md :global(h1),
	.msg-md :global(h2) {
		font-size: 1.1em;
		font-weight: 600;
	}
	.msg-md :global(h3) {
		font-size: 1em;
		font-weight: 600;
	}
	.msg-md :global(ul) {
		list-style: disc;
		padding-left: 1.4em;
	}
	.msg-md :global(ol) {
		list-style: decimal;
		padding-left: 1.4em;
	}
	.msg-md :global(code) {
		font-family: var(--font-mono);
		font-size: 0.85em;
		background: color-mix(in srgb, currentColor 8%, transparent);
		border-radius: 6px;
		padding: 0.1em 0.35em;
	}
	.msg-md :global(pre) {
		background: color-mix(in srgb, currentColor 6%, transparent);
		border-radius: 12px;
		padding: 0.8em 1em;
		overflow-x: auto;
	}
	.msg-md :global(pre code) {
		background: none;
		padding: 0;
		font-size: 0.85em;
	}
	.msg-md :global(blockquote) {
		border-left: 3px solid var(--color-line);
		padding-left: 0.9em;
		color: var(--color-muted);
	}
	.msg-md :global(a) {
		color: var(--color-accent);
		text-decoration: underline;
	}
	.msg-md :global(.mention) {
		color: var(--color-accent);
		font-weight: 600;
	}
</style>
