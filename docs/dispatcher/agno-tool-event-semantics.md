# Agno Run/Event System — Tool-Call Event Emission (Technical Findings)

**Clone:** `/home/hermes/src/oss/clones/agno`
**Version:** `2.9.0` (pyproject `libs/agno/pyproject.toml`), commit `3259cb9a` (2026-08-19)
**Pinned by Rivulets:** `agno==2.8.6` — this clone is NEWER. Core semantics below match 2.8.6's intent but the exact code is post-v2.5 refactor (`_run_options.py` resolution module was introduced in `95ef5bed "feat: v2.5 Phase 1 — Agent/Team refactor"`).

`ToolExecution` is defined at `libs/agno/agno/models/response.py:28` (a `@dataclass`, not a Pydantic model).

---

## 1. Default value of `stream_events`, and does `stream=True` force it on?

### Signatures (`libs/agno/agno/agent/agent.py`)

- `Agent.run(...)`: `stream_events: Optional[bool] = None` — line 1404 (and overloads at 1350, 1377).
- `Agent.arun(...)`: `stream_events: Optional[bool] = None` — line 1520 (overloads at 1466, 1493).
- `Agent.continue_run(...)`: `stream_events: Optional[bool] = False` — **line 1612** (impl). NOTE this differs from `run`/`arun`!
- `Agent.acontinue_run(...)`: `stream_events: Optional[bool] = None` — line 1700.

Agent class attribute default: `stream_events: Optional[bool] = None` — `agent.py:333`.

### Resolution (`libs/agno/agno/agent/_run_options.py:101-111`)

```python
    # stream_events: forced False when not streaming;
    # otherwise call-site > agent.stream_events > False
    resolved_stream_events: bool
    if resolved_stream is False:
        resolved_stream_events = False
    elif stream_events is not None:
        resolved_stream_events = stream_events
    elif agent.stream_events is not None:
        resolved_stream_events = agent.stream_events
    else:
        resolved_stream_events = False
```

`resolved_stream` is computed at `_run_options.py:92-99` (call-site `stream` > `agent.stream` > `False`).

### ANSWER

- **Default is effectively `False`.** The parameter defaults to `None`, but `resolve_run_options` maps `None` (and absent `agent.stream_events`) to `False`.
- **`stream=True` does NOT force `stream_events` on.** It only *unlocks* the call-site/agent default. If neither is set, `stream_events` resolves to `False` even when `stream=True`.
- **`stream=False` forces `stream_events=False` unconditionally** (first branch), regardless of call-site or agent settings.
- **To get tool-call events you MUST explicitly pass `stream_events=True`** (or set `agent.stream_events=True`), AND stream must be on.

### Note: `continue_run` vs `run`/`arun`

`continue_run`'s *implementation* signature defaults `stream_events=False` (agent.py:1612), while the `stream=True` overload declares `stream_events: Optional[bool] = False` (agent.py:1588). Because resolution is "call-site > agent > False", an explicit `False` at the continue_run layer resolves to `False` and overrides an agent-level `stream_events=True`. This is a subtle footgun: a `stream=True, stream_events=<unset>` continue_run will NOT emit events even if `agent.stream_events=True` (the explicit `False` default wins at `_run_options.py:106`). However, `_run.continue_run_dispatch` receives whatever the caller passes; if you pass `stream_events=True` explicitly it works.

---

## 2. Event classes carrying tool-call data

All defined in `libs/agno/agno/run/agent.py` as `@dataclass`, each subclassing `BaseAgentRunEvent` (agent.py:196), which itself already declares `tools: Optional[List[ToolExecution]] = None` (agent.py:214) and a `content` back-compat field (agent.py:217).

### `ToolCallStartedEvent` (agent.py:416-419)
```python
@dataclass
class ToolCallStartedEvent(BaseAgentRunEvent):
    event: str = RunEvent.tool_call_started.value
    tool: Optional[ToolExecution] = None
```
Fields it exposes: `tool` (single `ToolExecution`); inherited `tools` (list) stays `None`; inherited `content` stays `None`.

### `ToolCallCompletedEvent` (agent.py:422-429)
```python
@dataclass
class ToolCallCompletedEvent(BaseAgentRunEvent):
    event: str = RunEvent.tool_call_completed.value
    tool: Optional[ToolExecution] = None
    content: Optional[Any] = None
    images: Optional[List[Image]] = None   # Images produced by the tool call
    videos: Optional[List[Video]] = None   # Videos produced by the tool call
    audio: Optional[List[Audio]] = None   # Audio produced by the tool call
    files: Optional[List[File]] = None    # Files produced by the tool call
```
Exposes `tool` (single `ToolExecution` with populated `.result`, `.tool_call_error`, `.metrics`), plus `content` (the tool's returned content) and media lists.

### `ToolCallErrorEvent` (agent.py:433-437)
```python
@dataclass
class ToolCallErrorEvent(BaseAgentRunEvent):
    event: str = RunEvent.tool_call_error.value
    tool: Optional[ToolExecution] = None
    error: Optional[str] = None
```
Exposes `tool` and `error` (a `str`, set to `str(tool.result)` at emission — see §6).

### `ToolExecution` fields (`libs/agno/agno/models/response.py:28-64`)
```python
@dataclass
class ToolExecution:
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_call_error: Optional[bool] = None
    result: Optional[str] = None
    metrics: Optional[ToolCallMetrics] = None
    child_run_id: Optional[str] = None
    stop_after_tool_call: bool = False
    created_at: int = ...                  # time()
    # HITL fields
    requires_confirmation: Optional[bool] = None
    confirmed: Optional[bool] = None
    confirmation_note: Optional[str] = None
    requires_user_input: Optional[bool] = None
    user_input_schema: Optional[List[UserInputField]] = None
    user_feedback_schema: Optional[List[UserFeedbackQuestion]] = None
    answered: Optional[bool] = None
    external_execution_required: Optional[bool] = None
    external_execution_silent: Optional[bool] = None
    approval_type: Optional[str] = None
    approval_id: Optional[str] = None
```
Key for the consumer: `tool_name` and `tool_call_id` are the identity; `tool_call_error` (`bool`) flags a failed call. **`tool` can be `None` in these events only if the emitter passed `None`, which the production emitters never do** (§6 emitters always pass a concrete `ToolExecution`).

---

## 3. Does `RunCompletedEvent` carry `tools` off the RunOutput?

**No.**

- `RunCompletedEvent` is defined at `agent.py:275-293`. It declares `content`, `reasoning_content`, `citations`, `images`, `videos`, `audio`, `files`, `metadata`, `metrics`, `session_state`, etc. — but NO `tools` field of its own. It only inherits `tools: Optional[List[ToolExecution]] = None` from `BaseAgentRunEvent` (agent.py:214), which is never set for the completed event.
- `create_run_completed_event(from_run_response)` at `libs/agno/agno/utils/events.py:139-162` copies `content`, `session_id`, `agent_id`, `agent_name`, `run_id`, `reasoning_content`, `citations`, `model_provider_data`, `images`, `videos`, `audio`, `files`, `response_audio`, `references`, `additional_input`, `reasoning_steps`, `reasoning_messages`, `metadata`, `metrics`, `session_state`. **`tools` is NOT copied** (no `tools=from_run_response.tools` line).

**Consequence:** a streaming consumer CANNOT rely on `RunCompletedEvent.tools` to learn what tools ran — it must collect `ToolCallCompletedEvent`/`ToolCallErrorEvent`/`ToolCallStartedEvent` instances (or consume the `RunOutput` returned via `yield_run_output=True`).

---

## 4. When can a stream end WITHOUT `RunCompletedEvent`?

`RunCompletedEvent` is yielded at the *end of the model loop* only on success/cancel. In the streaming path (`_run.py`), the terminal-yield logic is:

- **Success:** `completed_event = handle_event(create_run_completed_event(run_response), ...)` then `if stream_events: yield completed_event` — `_run.py:1138-1155`. Note: **if `stream_events` is False, `RunCompletedEvent` is never yielded at all** (only `run_response` via `yield_run_output`).
- **Validation failure** (`InputCheckError`/`OutputCheckError`): yields `RunErrorEvent` and `break` — **NO `RunCompletedEvent`** (`_run.py:1190-1219`).
- **KeyboardInterrupt / cancellation:** yields `RunCancelledEvent` + a `RunCompletedEvent` pair via `_build_cancel_terminal_events` (`_run.py:5695-5730`, emitted at 1220-1243). So cancellation DOES include a completed event (with `status` cancelled).
- **Generic exception** (retries exhausted): yields `RunErrorEvent`, `break` — **NO `RunCompletedEvent`** (`_run.py:1244-1278`).

Async equivalents mirror these at `_run.py:2607-2710` and `_run.py:4041-4111`.

### Provider-specific quirk (what the parent was probing for)

The `stream_model_response` flag is the relevant quirk hook. In `handle_model_response_stream` / `ahandle_model_response_stream` (`_response.py:1071-1074` and `1231-1234`):

```python
stream_model_response = True
if should_parse_structured_output:
    log_debug("Response model set, model response is not streamed.")
    stream_model_response = False
```

When `output_schema` is set AND `agent.parse_response` is True AND no `parser_model`, the model response is *not* streamed by the provider — agno gets a complete `ModelResponse` and the tool events are still emitted (the tool-call event branches are driven by `ModelResponseEvent`, not by provider streaming), but the content deltas (`RunContentEvent`) are effectively replaced by a single completion. This does **not** suppress `RunCompletedEvent` — it's still yielded on success. The only way a stream truly ends without `RunCompletedEvent` is an error path (validation/exception) or `stream_events=False` (events entirely off).

**Reliability takeaway:** a consumer detecting "tools were invoked" should treat `RunErrorEvent`/`RunCancelledEvent` as terminal too, and NOT assume `RunCompletedEvent` always arrives as the final event.

---

## 5. `RunOutput.tools` type and where populated

- **Type:** `tools: Optional[List[ToolExecution]] = None` — `libs/agno/agno/run/agent.py:641`.
- **Primary population (non-stream + end-of-stream):** `update_run_response(...)` in `libs/agno/agno/agent/_response.py:1007-1016`:
  ```python
  if model_response.tool_executions is not None:
      if run_response.tools is None:
          run_response.tools = list(model_response.tool_executions)
      else:
          existing_by_id = {t.tool_call_id: i for i, t in enumerate(run_response.tools) if t.tool_call_id}
          for tool in model_response.tool_executions:
              if tool.tool_call_id and tool.tool_call_id in existing_by_id:
                  run_response.tools[existing_by_id[tool.tool_call_id]] = tool   # in-place dedupe
              else:
                  run_response.tools.append(tool)
  ```
  Called from `_run.py` in BOTH the non-stream path (`_run.py:565`, `1712`, `3674`, `4775`) via the `call_model_with_fallback` result, and stream path end.
- **Streaming increment:** as `ModelResponseEvent` chunks arrive, `run_response.tools` is extended in `_response.py:1570-1594` (`tool_call_paused` / `tool_call_started` events) and updated in-place on `tool_call_completed` (`_response.py:1644-1659`).
- **Tool execution loop** also mutates the in-memory `tool` objects' `.result` / `.tool_call_error` (`_tools.py:738-740` sync, `852-854` async).
- **Checkpoint snapshot:** `mirror_run_response` sets `run_response.tools = list(model_response.tool_executions)` (`_run.py:5980-5981`).
- **Serialization:** `to_dict()` emits `tools` (agent.py:819-825); `from_dict()` reconstructs `ToolExecution` list from `data["tools"]` (agent.py:873-874).

---

## 6. `store_events` / `events_to_skip` semantics

Agent attrs: `store_events: bool = False` and `events_to_skip: Optional[List[RunEvent]] = None` (`agent.py:336-337`), defaulting `events_to_skip` to `[RunEvent.run_content]` (`agent.py:676-677`).

`handle_event(...)` — `libs/agno/agno/utils/events.py:1128-1140`:

```python
def handle_event(event, run_response, events_to_skip=None, store_events=False):
    _events_to_skip = [event.value for event in events_to_skip] if events_to_skip else []
    if store_events and event.event not in _events_to_skip:
        if run_response.events is None:
            run_response.events = []
        run_response.events.append(event)
    return event
```

Every emitted event passes through `handle_event` (e.g. `_response.py:1599-1604`, `1686-1702`; `_tools.py:731, 766, 775`).

**Semantics:**
- `store_events`/`events_to_skip` affect **persistence onto `run_response.events`**, NOT what is *yielded*. `handle_event` ALWAYS returns the event, and the caller always `yield`s it (when `stream_events` is True). So `events_to_skip`/`store_events=False` will NOT hide tool-call events from a streaming consumer.
- `events_to_skip` filtering is by `event.event` string (e.g. `"RunContent"`), so default skip list only drops `RunContent` from storage. Tool events (`"ToolCallCompleted"`, etc.) are stored when `store_events=True`.
- Because the default skip list contains `RunEvent.run_content`, `run_content` events are not persisted even with `store_events=True`, but they ARE still yielded.

**Tool-call error emission detail** (`_response.py:1694-1702` and `_tools.py:773-781`): a `ToolCallErrorEvent` is emitted **in addition to** `ToolCallCompletedEvent` when `tool.tool_call_error` is truthy, with `error=str(tool.result)`. So a failed tool yields BOTH a `ToolCallCompletedEvent` AND a `ToolCallErrorEvent`.

---

## 7. Reliable non-streaming way to get the full tool list

**Yes — `RunOutput.tools` is the reliable, stream-agnostic source.**

With `stream=False` (default), `Agent.run()` / `Agent.arun()` return a single `RunOutput`, and `run_response.tools` is populated by `update_run_response` (`_response.py:1007-1016`) from `model_response.tool_executions` after the model/tool loop completes (`_run.py:565`, etc.). The list contains every `ToolExecution` the model requested, with `result` and `tool_call_error` filled in by the tool-execution loop (`_tools.py:738-740` / `852-854`).

**Recommendation for Rivulets' `run_agent()`:** the most robust "did a tool actually get invoked" signal is `run_output.tools` (a `List[ToolExecution]`) — inspect `tool_name`/`tool_call_id`, and use `tool_call_error` to distinguish failed from successful calls. This works regardless of `stream`/`stream_events`, whereas relying on `ToolCallCompletedEvent`/`ToolCallErrorEvent` requires `stream=True` AND `stream_events=True`, and your current code already both passes those and synthesizes a `RunOutput` from events.

### Edge cases / gotchas to preserve

1. **`stream=True` alone does not emit tool events** — must pass `stream_events=True` (see §1). This is the exact prior bug.
2. **`RunCompletedEvent` has no `tools`** — never rely on it for tool detection (§3).
3. **`continue_run` default-eats agent-level `stream_events=True`** via its `stream_events=False` impl default (§1 note) — pass `stream_events=True` explicitly on continue.
4. **Failed tool still emits `ToolCallCompletedEvent` first** (with `tool.tool_call_error=True`), THEN `ToolCallErrorEvent` — don't double-count if you reconcile events into a tool list by `tool_call_id` (§6).
5. **A run may terminate with `RunErrorEvent`/`RunCancelledEvent` and no `RunCompletedEvent`** (§4) — treat error/cancel as terminal markers too.
6. **Structured-output path (`output_schema` set, no parser_model) sets `stream_model_response=False`** (`_response.py:1071-1074`) — content is not streamed per-delta but tool events still flow; `RunOutput.tools` still populated.
7. **When `agent.store_events=True` and `stream_events=True`**, the full ordered event list is also available on the final `RunOutput.events` (via `yield_run_output=True`) — an alternative signal source.
