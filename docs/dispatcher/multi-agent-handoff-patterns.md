# Multi-Agent Handoff/Dispatch: Design Patterns Across Five Frameworks

**Goal:** extract *mechanisms* (not marketing) for making agent-to-agent handoff **deterministic, verifiable, and failure-safe** — directly analogous to Rivulets' broken "Assistant says 'I'll engage the coder' but the handoff tool is silently dropped."

The universal finding across all five systems: **a handoff is only real if it is a structured, framework-visible artifact (a tool call / typed message / state update), never prose.** Every mature framework routes on that artifact, and every one has an explicit way to *observe* it and *bound* it.

---

## 1. OpenAI Swarm → Agents SDK

**Deterministic trigger — tool call, not prose.** A handoff is a *function/tool* whose return value is another `Agent`. In Swarm: `func transfer_to_agent_b() -> Agent`. The runtime loop is explicit: (1) get completion, (2) **execute tool calls and append results**, (3) **switch Agent if necessary**, (4) update context, (5) return if no function calls. "If an Agent calls multiple handoff functions, only the last handoff is used." `client.run()` accepts `max_turns` (default `inf`) to bound loops. Source: https://github.com/openai/swarm

**Agents SDK (production successor):** handoffs are registered per-agent and exposed to the LLM as tools named `transfer_to_<agent>`. The `handoff()` wrapper adds `on_handoff` callback, `input_type` (Pydantic schema the LLM must fill — validated locally before dispatch), `input_filter`, and `is_enabled` (bool or runtime predicate to gate handoffs). The target is fixed at registration ("always transfers control to the specific agent you passed"), so the model only *chooses among N registered handoff tools* — it cannot free-text a target. Source: https://openai.github.io/openai-agents-python/handoffs/

**Observability/verification:** `on_handoff(ctx, input_data)` fires *synchronously when the tool is invoked* — this is the verifiable side-effect equivalent to Rivulets' missing hook. Built-in tracing surfaces handoff events in the dashboard. **Loops:** bounded by `max_turns`/run loop; `is_enabled` disables redundant paths. **Failure:** tool-call execution is part of the runner loop; a dropped call means no `HandoffOutputItem`, so the runner stays on the current agent (the drop is *detectable by absence*, though the SDK assumes tool-call plumbing is reliable).

---

## 2. Microsoft AutoGen v0.4 (AgentChat)

**Deterministic trigger — a typed `HandoffMessage` carrying `target` and `context`.** `AssistantAgent(handoffs=["flights_refunder","user"])` registers *named* handoff targets; the framework synthesizes a handoff *tool* that, when called, emits a **`HandoffMessage`** (`target: str`, `context: List[LLMMessage]`). This is a first-class structured message — exactly the artifact Rivulets lacks. Source: https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.teams.html

**Target selection/invocation:** the `Swarm` team's `select_speaker` reads **the most recent `HandoffMessage`** and picks that `target` as next speaker — purely deterministic, no LLM guessing. If no handoff message, the current speaker continues. `SelectorGroupChat` offers the alternative: an LLM (or a user function) *selects* the next speaker rather than the agents emitting handoffs. Source: https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.teams.html

**Loop/cycle prevention & completion:** `termination_condition` is *mandatory for bounded runs* — e.g. `HandoffTermination(target="user") | TextMentionTermination("TERMINATE") | MaxMessageTermination(3)`. `HandoffTermination` stops the team and yields control (human-in-the-loop) when an agent hands off to `"user"`. Noteworthy: `validate_group_state` checks the latest handoff `target` is a *valid participant* and rejects resume-with-bad-target. Source: https://raw.githubusercontent.com/microsoft/autogen/main/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_swarm_group_chat.py

**Observability:** every turn broadcasts typed messages/events (`HandoffMessage`, `ToolCallRequestEvent`, `ToolCallExecutionEvent`); `run_stream` yields these, so a handoff is observable as a concrete event, not inferred from text.

---

## 3. LangGraph — Supervisor + `Send` + `Command(goto=...)`

Two distinct mechanisms, both **state/routing-based, not LLM-prose-based**:

**(a) Handoff tool returning `Command(goto=...)`.** The canonical pattern: a tool writes a `ToolMessage` (with matching `tool_call_id`) *and* returns `Command(goto="<target_node>", update={...}, graph=Command.PARENT)`. Critically, **`graph=Command.PARENT`** routes to a node in the *parent* graph so a sub-agent can transfer to a sibling specialist. This is what makes cross-agent dispatch deterministic in a graph of subgraphs. Source: https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs and https://docs.langchain.com/oss/python/langgraph/graph-api

**Why the `ToolMessage` is mandatory:** "When an LLM calls a tool, it expects a response. The ToolMessage with matching tool_call_id completes the request-response cycle — without it, the conversation history becomes malformed." This is the exact class of bug Rivulets hit: an un-answered tool call. Source: https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs

**(b) Supervisor + `Send` (dynamic fan-out).** The supervisor node forces **structured output** (`next`/`next_agent` field) and returns `Command(goto=response["next_agent"])`; workers return `Command(goto="supervisor")`. `Send` dynamically branches to multiple workers in parallel from functions.

**Loops/prevention:** recursion is bounded by `recursion_limit` (default 25 "super-steps"); the supervisor must explicitly emit a `FINISH`/`__end__` signal to halt, otherwise the graph raises `GraphRecursionError`. **Known footgun:** combining a static `add_edge("tools","supervisor")` with a tool that returns `Command(goto=...)` schedules *both* routes → parallel/duplicate execution (the authoritative doc of a real firing bug). Source: https://github.com/langchain-ai/langgraph/issues/6436

**Observability:** state (`active_agent`, `current_step`) persists across turns; every transition is a graph edge recorded in the checkpointer/LangSmith trace.

---

## 4. CrewAI — Hierarchical Process & Delegation

**Trigger — rule/config + tool, not free-text.** Delegation is exercised via `allow_delegation` on an agent (default **off** now) and `delegate_work_to_coworker` tooling. The manager agent (auto-created or custom) *allocates tasks* to crew members by role/capability. Source: https://docs.crewai.com/en/learn/hierarchical-process

**How it actually dispatches:** the manager receives the goal and decides "which workers, in what order, with what inputs," validating outcomes (`Process.hierarchical`). The manager is itself an LLM agent, so target selection is LLM-driven — which is CrewAI's documented weak point: a known failure mode is the manager "delegating to the wrong agent" or executing non-related agents sequentially. Source: https://community.crewai.com/t/manager-agent-delegates-task-to-wrong-agent-in-a-hierarchical-process/3179 and https://towardsdatascience.com/why-crewais-manager-worker-architecture-fails-and-how-to-fix-it/

**Bounding/loops:** `max_iter` (max iterations toward a final answer) and `max_rpm` (rate limit). **Observability:** CrewAI Tracing (and integrations with Langfuse/OpenTelemetry/Opik/etc.) record the delegation call chain.

**Takeaway for Rivulets:** CrewAI is the counterexample — it *does* rely on an LLM manager to pick targets, and that is precisely where it fails. Prefer fixed-registration routing (Swarm/Agents-SDK/AutoGen) over LLM-chosen targets for a local-first app.

---

## 5. Anthropic — Orchestrator-Workers (cookbook)

**Deterministic trigger — structured output parsed by code, then programmatic dispatch.** The orchestrator LLM emits **XML** with `<task><type>…<description>…` blocks; `parse_tasks()` (deterministic Python) turns the XML into a list of task dicts; then `FlexibleOrchestrator.process()` *forks* one worker LLM call per task. The model never "invokes" a worker — **code does the dispatch after structured parsing.** This inverts the failure mode: the model produces structured data, and the handoff is a plain function call in your code. Source: https://raw.githubusercontent.com/anthropics/anthropic-cookbook/main/patterns/agents/orchestrator_workers.ipynb and https://www.anthropic.com/engineering/multi-agent-research-system

**Verification/failure:** "Error handling validates that workers return non-empty responses" — empty output is caught explicitly; the orchestrator is called again on retry. The production write-up stresses "solid heuristics, observability, and tight feedback loops."

---

## Common Footguns (directly relevant to Rivulets)

1. **Silent tool-call drops on streaming paths.** The #1 reported failure. The model emits a tool call as a streaming chunk, but the aggregator/pipeline (e.g. agno's streaming path) never materializes/executes it. Result: the Assistant's *text* says "I'll engage the coder" while the tool call vanished. **Fix:** every framework materializes handoffs as a *typed, loop-executed* artifact (Swarm's `run()` loop, AutoGen's `HandoffMessage`, LangGraph's `Command`) — never defer to prose. Add an explicit assertion: after each model turn, if the transcript declares an action but no tool-call record exists, treat it as a no-op and re-drive.

2. **Relying on LLM self-report without a verifiable side-effect.** "I'll engage the coder" is text; it is not dispatch. Mature frameworks bind the claim to a side-effect: `on_handoff` (Agents SDK), `HandoffMessage` (AutoGen), `Command(goto=...)` + state write (LangGraph), or parsed-XML → code dispatch (Anthropic). **Fix for Rivulets:** the `handoff`/`engage_team` tool must, on invocation, write a durable state field (e.g. `active_agent`), then the router reads that field — never infer routing from assistant text.

3. **Non-idempotent re-dispatch.** If a handoff is retried (timeout, crash, re-run) and the tool has a side effect (e.g. sending a message, kicking a job), you get duplicate work. **Fix:** make handoff *idempotent* — the dispatch itself only sets state (`active_agent=...`), and the *side effect* is idempotent keyed on a stable id (`tool_call_id`/`run_id`). LangGraph's required tool_call_id pairing is the canonical example of keeping the tool-call ledger consistent so re-dispatch doesn't fabricate duplicate tool_results.

4. **Unbounded loops / ping-pong.** A→B→A without a termination condition. Every framework exposes a bound: `max_turns` (Swarm/Agents SDK), `termination_condition` (AutoGen), `recursion_limit` (LangGraph), `max_iter` (CrewAI). Rivulets' `engage_team` should carry a turn budget and a "return-to-Assistant" rule.

---

## Recommended fix pattern for Rivulets (synthesis)

Adopt the **AutoGen/Agents-SDK shape**, which is the closest fit to an Assistant-as-orchestrator + named specialists:

- Specialists are **registered by name** (`handoffs=["Coder"]`), so the model selects among a fixed set, never free-texts a target.
- The `handoff(target, context)` tool returns/emits a **typed `HandoffMessage`**, and the runtime's `select_speaker` reads *only* that message to pick the next agent.
- An `on_handoff` callback fires synchronously to persist `active_agent` and emit the *verifiable* trace.
- A mandatory `termination_condition` / `max_turns` prevents ping-pong.
- After each streaming turn, **assert the tool-call ledger is balanced** (every assistant tool call has a matching tool result); if the model *claims* a handoff in prose but no tool call was recorded, flag it and re-drive rather than silently dropping.
