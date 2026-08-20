# Rivulets Dispatch & Handoff Stabilization Plan

**Status:** Ready for coding-agent handoff
**Date:** 2026-08-20
**Repo:** `github.com/jwilson411/Rivulets` (v0.7.0, `main` @ `5bcbeb1`)
**Scope:** Root-cause the broken Agent dispatch / "engage the team" / handoff orchestration and stabilize it.

---

## 1. Executive summary

The user-facing symptom is: *"I ask for an NFL player tracker. The Assistant asks a couple of questions, but never actually hands the work to a Coder. If I unlock the team, the Assistant replies 'I'm going to engage the coder,' but the coder is never invoked."*

The dispatch system is **not** a single bug — it is a **chain of four independent fragilities**, each of which can silently produce the same symptom (Assistant *says* it will hand off, nobody runs):

| # | Fragility | Where | Effect |
|---|-----------|-------|--------|
| F1 | Handoff routing depends on the LLM *actually emitting* a `handoff` tool call; prose wins when it doesn't | LLM behavior + tool prompt | Assistant says "I'll engage the coder" as text, never calls the tool |
| F2 | Tool-call detection depends on fragile streaming event collection in `run_agent()` | `agentos/service.py` | Tool call emitted by agno but silently dropped on the streaming path |
| F3 | `engage_team` is a **no-op** by design — it posts a marker but invokes nobody | `dispatch/service.py:_handle_engage_team` | Even a successful "engage" routes to nothing |
| F4 | Orchestrator lock + `pick_default_teammate`/`is_team_engaged` leave no deterministic path from "task is clear" → "specialist runs" | `dispatch/orchestration.py`, `_dispatch_and_respond` | No state machine; routing is emergent, not guaranteed |

The two most recent commits (`1567644` and `3033590`, both 2026-08-20) patched **F2 partially** — they fixed `stream_events=True` and event-based tool collection. But F1, F3, and F4 remain, and F2's fix is unverified against a real provider (see §3.4).

---

## 2. Current architecture (verified from source)

### 2.1 Two-stage dispatcher — `dispatch/engine.py`

`DispatchEngine.dispatch(message, agents, speaker_id)` resolves routing in order:

1. **`@mention`** — regex over `@name`, case-insensitive. Bypasses everything.
2. **`speaker_id` recursion guard** — if an agent (not human) is speaking, filter the roster via `_unsolicited_for_agent_reply`, keeping only KEYWORD/REGEX/SEMANTIC specialists (never ALWAYS/MENTION_ONLY/empty-rule agents), and never the speaker themselves.
3. **Deterministic rule match** (`match_deterministic`) — keyword/regex/semantic, priority-ordered, first agent wins per rule set.
4. **LLM fallback** (`llm_fallback.py`) — one cheap-model call to pick zero-or-more agents from name+description. Prefers zero (empty) over guessing.
5. **Default** — nothing matched.

`DispatchMethod` enum: `mentor/deterministic/llm/default/none`.

### 2.2 Assistant-as-orchestrator — `dispatch/orchestration.py`

There is a hard-coded, special "Assistant" agent (`ORCHESTRATOR_NAME = "Assistant"`):

- `merge_orchestrator()` forces Assistant onto every roster, with an `ALWAYS` rule (so every *human* message routes to Assistant first).
- `apply_orchestrator_lock()` rewrites the result:
  - human message → always `[Assistant]` (DEFAULT), unless a `@mention` matched.
  - specialist reply → `[Assistant]` (bounce back to orchestrator).
  - Assistant's own reply → `[]` (NONE — no self-rematch).
- `is_team_engaged()` / `post_team_engaged_message()` track a `team_engaged`/`handoff` content-type marker in the Message table.

**Key fact:** with an orchestrator present, the *only* ways a specialist ever runs are (a) explicit `@mention`, or (b) Assistant calling the `handoff(target, context)` tool. Keyword rematch does **not** wake specialists. This is intentional (see module docstring — the whole point is to prevent A→B→A ping-pong), but it means **everything rides on the `handoff` tool call actually firing and being captured.**

### 2.3 Invocation & handoff pipeline — `dispatch/service.py`

- `dispatch_and_respond` → `_dispatch_and_respond` (public/private split for the top-level `done` SSE event).
- `_dispatch_and_respond` builds the roster, runs `engine.dispatch`, then applies `pick_default_teammate` (nobody matched + human + no orchestrator → Assistant or first non-mention-only) and `apply_orchestrator_lock`, then loops `result.agent_ids` → `_invoke_agent` (or remote peer RPC).
- `_invoke_agent` (line 1499) is the shared core: budget check → guard reservation → `run_agent()` → persist reply → **then** inspect `run_output.tools` for side-effect tool calls in this order:
  1. `_find_hire_teammate_call` → `_handle_hire_teammate` (orchestrator only)
  2. `_find_handoff_call` → `_handle_handoff` (invoke the named target)
  3. `_find_engage_team_call` → `_handle_engage_team` (posts marker only)
  4. `apply_builtin_tool_triggers` (run_workflow, create_channel, etc.)
  5. recursive `dispatch_and_respond` on this agent's reply (FR-5.6)
- `_handle_handoff` (line 2174): finds target by name among `team_agents`; if found, posts a `content_type="handoff"` system message, then `_invoke_agent(target, f"[Handoff from X]: {context}")`. If target name unknown → logs warning, returns `[]` (silent).
- `_handle_engage_team` (line 2264): **only** calls `post_team_engaged_message()`. Returns the marker message. **It never invokes anyone.**

### 2.4 The tools — `tools/builtin/{handoff,engage_team,hire_teammate}.py`

All three are **side-effect-free stubs**. They return a confirmation *string* to the model; the real work happens in `dispatch/service.py` by inspecting the completed run's recorded tool calls. This is the "detect-and-act after the run" pattern. `handoff` and `engage_team` attach to **every** agent; `hire_teammate` attaches only to Assistant.

### 2.5 The streaming wrapper — `agentos/service.py:run_agent()`

`run_agent()` always calls `agno_agent.arun(message, stream=True, stream_events=True, ...)`, iterates the async event stream, accumulates `RunContentEvent` content, and (after the two recent commits) collects `ToolExecution`s from `ToolCallCompletedEvent` / `ToolCallErrorEvent` into `collected_tools`, then synthesizes a `RunOutput(content=..., status=..., tools=event.tools or collected_tools or None, metrics=...)`.

---

## 3. Root-cause findings

### 3.1 F1 — the model default is "say it, don't call it" (prompt/tool-design gap)

The Assistant's instructions (`starter_content.py:_ASSISTANT_ORCHESTRATOR_INSTRUCTIONS`) say *"call handoff with their name and the context they need.* Pick exactly one teammate." But nothing in the system **forces** the choice. The Assistant answers clarifying questions (a valid behavior), but on the turn where the task is clear, it is free to write a reassuring prose reply ("I'm going to engage the coder") without invoking `handoff`. There is no verification that a declared intent produced an actual tool call, and no re-drive.

This is the #1 footgun identified across every mature framework we researched (see §5): **a handoff is only real when it is a structured, framework-visible artifact — never prose.**

### 3.2 F2 — tool calls dropped on the streaming path (partially fixed, unverified)

Confirmed by reading agno source (see §6). The original code relied on `RunCompletedEvent.tools`, which is **always `None`** — agno's `create_run_completed_event` never copies `tools` off `RunOutput`. And because `stream_events` defaults to `False` even when `stream=True`, no `ToolCallCompletedEvent` streamed at all. Both fix commits address this:

- `1567644` — collect from `ToolCallCompletedEvent`/`ToolCallErrorEvent`.
- `3033590` — pass `stream_events=True` so those events actually emit.

**Remaining risk:** the fix is only covered by `_scripted_arun` tests that fake the async generator. It has **never been exercised against a real provider end-to-end** (§3.4). Also, agno emits **both** `ToolCallCompletedEvent` and (on failure) `ToolCallErrorEvent` for the same tool — the current dedup logic appends both, which is benign for `_find_handoff_call` (it just scans for a `tool_name == "handoff"`) but should be tightened.

### 3.3 F3 — `engage_team` routes to nothing

`_handle_engage_team` posts a `team_engaged` marker and returns. The docstring is honest: *"Does not open keyword rematch — the caller should hand off to the one teammate who should act."* But nothing forces the caller to hand off afterward. So "engage the team" is a semantic dead-end: the user sees a system line "@Assistant engaged the team: …" and then silence. There is no follow-up dispatch, and (per F4) no deterministic path from "engaged" to a specific specialist.

### 3.4 F4 — no deterministic "task → specialist" path; routing is emergent

The orchestrator-lock model deliberately removed keyword rematch (to kill ping-pong loops). That's the right call for loop-prevention, but it over-corrected: it left **zero guaranteed path** from "Assistant has a clear task" to "the right specialist runs." The only bridge is the `handoff` tool call, which is itself subject to F1 + F2. The result is the observed "loop of frustration": the human unlocks the team, the Assistant still won't (or can't) reach the coder.

The `team_engaged` marker (`is_team_engaged`) is computed but **not actually used** to change routing anymore — `apply_orchestrator_lock` explicitly ignores it (`_ = team_engaged`), preserving it only as a no-op for older callers. So "unlock the team" (which is what the user does) has **no routing effect whatsoever**.

---

## 4. The fix plan (phased, each phase independently shippable)

> Principle adopted from research (§5): **fixed-name registration + framework-visible handoff artifact + a deterministic router that reads that artifact + explicit termination/cycle bounds + observability that verifies the handoff actually happened.** Never infer routing from assistant prose.

### Phase 0 — Instrument & reproduce (do this FIRST; blocks Phases 2–3 verification)

1. Add `dispatcher` / `handoff` debug logging at: `run_agent` tool-collection, `_find_handoff_call`, `_handle_handoff` (both found-target and unknown-target branches), and `_handle_engage_team`.
2. Add an end-to-end test that drives a **real agno agent** (not `_scripted_arun`) with a stub model whose `response()` returns a tool call for `handoff` — or, failing that, a dedicated integration test gated behind an env var that hits a live provider. The current tests monkeypatch `run_agent`/`arun` and therefore *cannot* catch F1/F2. **This is the single highest-value test gap.**
3. Capture the exact failure mode with logging on: (a) does the Assistant emit a `handoff` tool call at all? (b) if yes, is it captured in `run_output.tools`? (c) if captured, does `_handle_handoff` find the target and invoke it?

### Phase 1 — Make handoff detection authoritative, not emergent (fixes F2 + F3)

1. **Treat `RunOutput.tools` as the canonical source of truth** for tool detection. Per §6.7, `stream=False` (non-streamed `arun`) reliably populates `run_output.tools` with every `ToolExecution` (name, args, `tool_call_error`). Consider a dedicated non-streamed "decision run" for the orchestrator's routing turn — or keep streaming for UX but **verify** the tool list is non-empty and reconcile by `tool_call_id` (not append-both, which double-counts failed tools).

2. **Reconcile tool collection by `tool_call_id`.** In `run_agent`, when a `ToolCallErrorEvent` arrives, update the already-collected `ToolCallCompletedEvent` entry for the same id (set `tool_call_error=True`) rather than appending a duplicate. This is the correct behavior per agno's emission (§6).

3. **Make `_handle_engage_team` act, not just mark.** Replace the no-op with a deterministic follow-up: after posting the marker, **re-dispatch the human's last message through a *specialist-only* routing pass** (deterministic rules + LLM fallback, but excluding Assistant and excluding ALWAYS/MENTION_ONLY), invoking the single highest-confidence match. If none, surface a clear "no specialist matched — tell me who to @mention or which role to hire" notice instead of silence. This directly fixes the "unlock the team → nothing happens" symptom.

4. **De-duplicate/guard `is_team_engaged` state** so a second `engage_team` in the same thread is a no-op (already handled) but a *fresh* human turn can re-engage (verify `reset_guard_state` coexists correctly with the marker).

### Phase 2 — Make handoff deterministic & verified (fixes F1 + F4)

1. **Fixed-name registration (`handoffs=[...]`).** Restrict the `handoff` tool's `target_agent_name` to the *actual roster*, surfaced to the Assistant as an enumerated list of valid targets (today the roster is injected as prose in `format_team_roster` / `wrap_with_roster`). Give the `handoff` tool an `enum` constraint on `target_agent_name` derived from `team_agents` at build time, so the model cannot free-text a wrong name (mirrors OpenAI Agents SDK / AutoGen `handoffs=`).

2. **Handoff as a first-class, durable artifact.** Currently the handoff's only durable trace is a `content_type="handoff"` Message row. Strengthen it: on handoff, write a per-rivulet `active_agent`/`pending_handoff` state (a column or the existing guard-state JSON), and have `_invoke_agent`'s recursion read/consume it. This makes routing re-runnable and crashes/idempotency-safe (§5 footgun 3).

3. **Close the "claims without calling" gap.** After any orchestrator turn, compare the model's *declared* intent against the *recorded* tool list. If the reply asserts a handoff/engage ("I'm going to engage…", "handing off to…") **and `run_output.tools` contains no matching `handoff`/`engage_team` call**, do **not** silently continue: either (a) re-drive the Assistant with an explicit "you said you would hand off; call the handoff tool now," or (b) surface a visible system notice that no handoff was actually performed. A lightweight deterministic keyphrase check is sufficient; do not over-engineer.

4. **Explicit specialist re-entry after a specialist reply.** Verify the current bounce-back (specialist → Assistant) actually re-invokes Assistant with the specialist's reply as input (it should via the recursion at `_invoke_agent:1868`), and that Assistant then either summarizes or hands off to a *different* specialist (per its instructions) without re-bouncing the same specialist (§5 footgun 4). Add a test for A→B→(summarize) and A→B→C chains.

### Phase 3 — Loop, cycle, and failure hardening (fixes F4 residuals)

1. **Cycle detection already exists** (`guards.py`, `cycle_window=8`, `cycle_threshold=3`, `turn_limit=10`). Add explicit tests for the handoff ping-pong (A hands to B, B hands to A) and the self-handoff (A hands to A) cases, asserting the guard pauses before the turn limit silently caps a legitimately long task. Verify guard state is reset correctly on a new human message.

2. **Handoff-to-unknown-target must be visible, not silent.** `_handle_handoff` currently logs + returns `[]` on an unknown name. If Phase 2.1's enum constraint is in place this should be rare, but for defense-in-depth post a `system_alert` ("@Assistant tried to hand off to @NoSuchAgent, who isn't on the team") so the human sees *why* nothing happened.

3. **Timeout/retry semantics.** A provider error currently returns a `system_alert` without counting toward guard limits. Verify `_run_agent_with_fallback`'s retry logic (`_is_retryable_error`) treats handoff-tool failures correctly and doesn't silently swallow a dropped handoff (a failed specialist run after a successful handoff should surface, not vanish).

4. **Idempotent handoff delivery.** If the handoff target invocation fails mid-way, ensure a retry does not double-post the `content_type="handoff"` message or double-invoke the target. Key handoff messages on a stable `(rivulet_id, tool_call_id)` and dedupe on insert.

### Phase 4 — Observability & acceptance

1. **Trace every handoff.** Ensure the `handoff` SSE event (`_handle_handoff` line 2214) and the `agent_status=waiting_for_handoff` signal fire even on the non-streamed path (if Phase 1.1 introduces a non-streamed decision run, wire `on_status` equivalently).
2. **Acceptance test matrix** (the shared "does dispatch work" gate):
   - human → Assistant → `@Coder` handoff → Coder replies → Assistant summarizes.
   - human → Assistant → `engage_team` → *the correct specialist actually runs* (regression for F3).
   - human "build me an NFL player tracker for my draft" → Assistant asks ≤1 clarifying question → on the clear turn, Coder is invoked without further human prompting.
   - `@mention` of a quiet specialist still works (FR-4.5).
   - A→B→A and self-handoff trip the cycle guard, not infinite loop.
   - assistant declares intent without a tool call → either re-driven or surfaced (F1 regression).
   - unknown handoff target → visible `system_alert`.

---

## 5. How other dispatchers solve this (research synthesis)

Full source-cited write-up: `/home/hermes/src/rivulets/multi-agent-handoff-patterns.md` (and copy below). The universal finding: **a handoff is only real if it's a structured, framework-visible artifact (tool call / typed message / state update), never prose.**

| Framework | Deterministic trigger | Target selection | Loop bound | Verification |
|-----------|----------------------|------------------|------------|--------------|
| OpenAI Swarm / Agents SDK | Handoff **tool** returning `Agent` (`transfer_to_<agent>`) | Fixed at registration; model picks among N tools, cannot free-text | `max_turns` | `on_handoff(ctx)` fires synchronously on invoke |
| AutoGen v0.4 Swarm | Typed **`HandoffMessage(target, context)`** | `select_speaker` reads the most recent `HandoffMessage` — no LLM guess | mandatory `termination_condition` / `MaxMessageTermination` | `HandoffMessage` broadcast as a concrete event |
| LangGraph | Handoff tool returns **`Command(goto=...)`** + mandatory matching `ToolMessage` | `graph=Command.PARENT` routes to named node | `recursion_limit` (default 25) | state write (`active_agent`) + traced graph edge |
| CrewAI hierarchical | `allow_delegation` + `delegate_work_to_coworker` | LLM manager (their documented weak point) | `max_iter`, `max_rpm` | tracing integrations |
| Anthropic orchestrator-workers | **Structured output (XML) parsed by code**, then *code* dispatches workers | deterministic `parse_tasks()` → fork | n/a (single orchestration pass) | "validate workers return non-empty" |

**Direct lessons for Rivulets** (all four map 1:1 to the fragilities in §3):

1. Silent tool-call drops on streaming paths → materialize handoffs as a typed loop-executed artifact; after each turn assert the tool ledger is balanced.
2. LLM self-report "I'll engage the coder" is text, not dispatch → bind the claim to a durable side effect (`active_agent`) read by the router.
3. Non-idempotent re-dispatch → key the handoff side effect on `tool_call_id`/`run_id`.
4. Unbounded ping-pong → every framework has an explicit bound; Rivulets has guards but must *test* they trip on handoff loops.

Adopt the **AutoGen/Agents-SDK shape** (closest to Assistant-as-orchestrator + named specialists): fixed-name registration, handoff-as-typed-artifact, synchronous `on_handoff`-style side effect, and a termination bound.

---

## 6. Agno event semantics (verified against source)

Full line-cited findings: `/home/hermes/src/oss/clones/agno/FINDINGS_agno_tool_events.md`. Key facts the fix must respect:

1. **`stream_events` defaults to `False` even when `stream=True`.** `_run_options.py:101-111` resolves `None`/unset → `False`. You MUST pass `stream_events=True` explicitly (the recent commit `3033590` now does).
2. **`RunCompletedEvent` never carries `tools`.** `create_run_completed_event` (`utils/events.py:139`) does not copy `tools`. Never rely on `event.tools` for detection.
3. **`ToolCallCompletedEvent` / `ToolCallErrorEvent` carry a single `tool: ToolExecution`** (`run/agent.py:422-437`). `ToolExecution` exposes `tool_name`, `tool_call_id`, `tool_args`, `tool_call_error`, `result`.
4. **Failed tools emit BOTH** `ToolCallCompletedEvent` (with `tool_call_error=True`) **and** `ToolCallErrorEvent` — reconcile by `tool_call_id`, don't double-append.
5. A stream can end with `RunErrorEvent`/`RunCancelledEvent` and **no** `RunCompletedEvent` — treat error/cancel as terminal too (the existing "no terminal event" fallback in `run_agent` already handles the ollama-style case).
6. **`store_events`/`events_to_skip` do NOT affect what is yielded** — only what's persisted to `run_response.events`. Tool events always yield when `stream_events=True`.
7. **The most robust "did a tool run" signal is `RunOutput.tools` from the non-streamed `stream=False` path** — populated by `update_run_response` (`_response.py:1007-1016`) from `model_response.tool_executions`, with `result`/`tool_call_error` filled by the tool-execution loop. Prefer this over event collection for the routing decision turn.

---

## 7. Files to touch (map for coding agents)

| File | Phase | Change |
|------|-------|--------|
| `server/src/rivulets/agentos/service.py` | 1 | Reconcile tool collection by `tool_call_id`; consider non-streamed decision path; expose a stable "did tools run" accessor |
| `server/src/rivulets/dispatch/service.py` | 1–4 | `_handle_engage_team` real dispatch; `_handle_handoff` unknown-target `system_alert` + idempotency; declared-vs-recorded intent check; verification logging |
| `server/src/rivulets/dispatch/orchestration.py` | 2 | `handoffs=[...]` roster → schema/enum; consume `team_engaged` meaningfully or remove it |
| `server/src/rivulets/tools/builtin/handoff.py` | 2 | Enum-constrain `target_agent_name` (or validate at handler against roster) |
| `server/src/rivulets/tools/builtin/engage_team.py` | 1 | Update docstring/contract to reflect that engage *does* route post-fix |
| `server/src/rivulets/agentos/starter_content.py` | 2 | Strengthen Assistant instructions: "you must call the handoff tool, not describe it" |
| `server/src/rivulets/dispatch/guards.py` | 3 | Verify cycle/turn guards trip on handoff ping-pong; no change likely, add tests |
| `server/tests/test_agentos_service.py` | 0 | Replace/augment `_scripted_arun` with a model-level test that emits real tool-call events |
| `server/tests/test_handoff.py` | 2–3 | Add regression tests: engage-now-dispatches, unknown-target alert, A→B→A guard trip, declared-without-call |
| `server/tests/test_dispatch.py`, `test_coordinator.py` | 2 | Orchestrator-lock + handoff-enum coverage |

---

## 8. Verification checklist (definition of done)

- [x] `engage_team` from Assistant results in a specialist actually running (not just a marker). *(Phase 1.3 — `_handle_engage_team` now runs a specialist-only routing pass and invokes the best match via the shared handoff pipeline; no match surfaces a visible notice.)*
- [x] `handoff` to a named specialist reliably invokes it, verified through agno's **real streaming pipeline** (a stub `Model` subclass drives the genuine tool-execution/event loop end-to-end — `test_run_agent_collects_handoff_from_a_real_agno_agent_run`). A live-provider smoke run is still worth doing manually.
- [x] Assistant declaring intent without a tool call is detected and re-driven once, then surfaced (not silent). *(Phase 2.3 — keyphrase check + `_INTENT_REDRIVE_PROMPT` in `_invoke_agent`.)*
- [x] Handoff to an unknown/off-roster name produces a visible `system_alert` with a roster hint. *(Phase 3.2.)*
- [x] Handoff ping-pong (A↔B) and self-handoff trip the cycle guard, don't hang. *(Regression tests added; guard logic itself needed no change.)*
- [ ] The "NFL player tracker for my draft" scenario reaches the Coder with ≤1 clarifying round-trip — needs a live-provider manual run.
- [x] Existing `test_dispatch.py`, `test_coordinator.py`, `test_handoff.py`, `test_agentos_service.py` all pass (full suite: 1726 passed).
- [x] Full CI gate (ruff check, ruff format, pyright, pytest per CONTRIBUTING.md) green locally.

Also landed: tool-collection reconciliation by `tool_call_id` in `run_agent`
(Phase 1.2 — a failed tool is one entry, not a completed+error duplicate),
Phase 0.1 debug logging at every link of the detection chain, and a
strengthened Assistant prompt + roster footer ("a handoff only happens when
you actually call the tool"). Not done (deliberately deferred): the
`handoffs=[...]` enum constraint on the tool schema (Phase 2.1 — the tool is
attached at agent build time, before any channel roster exists; the
handler-side validation + visible alert is the plan's sanctioned alternative)
and the durable `active_agent` state column (Phase 2.2).
