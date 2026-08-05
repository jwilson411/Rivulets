# Rivulets — Acceptance Criteria

Each criterion is formatted as **AC-###** and maps to one or more user stories. Criteria are testable: a QA engineer can verify each one without ambiguity.

---

## Workspace & Installation

### AC-001: Fresh Installation
**Maps to:** US-001, US-002, US-003
1. Run the install command on a clean machine.
2. Observe: setup wizard prompts for LLM provider (at minimum OpenAI-compatible API key + base URL).
3. Provide valid credentials.
4. Observe: system generates and displays a workspace key with a clear warning to save it.
5. Observe: system starts and prints the localhost URL.
6. Open browser to the URL. Observe: the Rivulets chat UI loads.
7. Enter the workspace key when prompted. Observe: you are authenticated and see an empty workspace.
**Expected:** Full flow completes without errors. Workspace key is a hex string ≥64 characters.

### AC-002: Multi-Provider Configuration
**Maps to:** US-003
1. During setup, add two LLM providers (e.g., OpenAI + DeepSeek) with valid API keys.
2. Complete setup. Navigate to Settings > Providers.
3. Observe: both providers listed with status indicators (connected/error).
4. Add a third provider. Observe: it appears in the list.
5. Remove a provider. Observe: warning appears if any agents are using it.
**Expected:** Provider management works. Agents using a removed provider show a warning.

### AC-003: Workspace Key Import
**Maps to:** US-035
1. On machine A, complete setup and note the workspace key.
2. On machine B, run install and choose "Join existing workspace."
3. Paste the workspace key from machine A.
4. Observe: machine B connects to machine A (or discovers it) and begins syncing.
5. Observe: after sync, machine B shows the same channels, agents, and threads as machine A.
**Expected:** Machine B has identical workspace state after sync.

---

## Channels & Teams

### AC-004: Channel Lifecycle
**Maps to:** US-004, US-006
1. Click "Create Channel." Enter name "general" and optional description.
2. Observe: channel appears in sidebar.
3. Create a team called "Core Team" with no agents. Assign it to "general."
4. Observe: channel shows team name in header.
5. Rename channel to "general-discussion."
6. Observe: sidebar updates.
7. Delete the channel. Observe: confirmation prompt, then channel removed.
**Expected:** Full CRUD works. Deleted channels are recoverable from trash/archive for 30 days.

### AC-005: Team Assignment
**Maps to:** US-005, US-006
1. Create agents A, B, C.
2. Create team "Engineering" with agents A and B.
3. Create channel "engineering" and assign "Engineering" team.
4. Post a message in "engineering" that matches agent A's routing rules.
5. Observe: agent A responds. Agent C does not (not on team).
6. Reassign "Engineering" team to a different channel. Observe: old channel shows "no team assigned."
**Expected:** Team assignment gates which agents monitor which channels.

---

## Agent Management

### AC-006: Agent Creation and Routing Rule Generation
**Maps to:** US-008, US-009, US-010, US-011, US-017
1. Click "Create Agent."
2. Fill in: Name="Code Reviewer", Description="Reviews pull requests and code changes for quality, security, and best practices", Instructions="You are a senior code reviewer. Focus on...", Model="DeepSeek V4", Tools=["File System"], Team="Engineering".
3. Submit. Observe: agent appears in agent list.
4. Inspect generated routing rules (admin view). Observe: rules include keywords like "review," "PR," "pull request," "code quality," "security review."
5. Post a message in a channel the agent's team monitors: "Can someone review my PR?"
6. Observe: "Code Reviewer" agent is invoked and responds in the thread.
**Expected:** Agent created, routing rules auto-generated and effective.

### AC-007: Agent Model Selection
**Maps to:** US-009
1. Create agent with model "GPT-4o".
2. Create another agent with model "Claude Sonnet 4".
3. Post messages that trigger each agent. Observe agent responses.
4. Check run history for each. Observe: model field shows the selected model.
**Expected:** Each agent uses its assigned model. Cross-model routing works.

### AC-008: Agent Editing
**Maps to:** US-012
1. Open an existing agent's settings.
2. Change description from "Reviews code" to "Reviews infrastructure-as-code and Terraform configs."
3. Save.
4. Observe: routing rules are regenerated. Old code-review keywords are removed or deprioritized. New IaC keywords appear.
5. Post "Can someone check my Terraform?" — agent responds.
6. Post "Can someone review my Python PR?" — agent does NOT respond (rules updated).
**Expected:** Editing description/instructions triggers rule regeneration. Old rules don't persist.

---

## Channel Dispatcher

### AC-009: Deterministic Routing Match
**Maps to:** US-014, US-015
1. Create agent "DBA" with description focused on databases, SQL, schema design.
2. Post message: "I need help designing a PostgreSQL schema for user profiles."
3. Observe: DBA agent is invoked (deterministic keyword match on "PostgreSQL," "schema").
4. Post message: "What's the weather like today?"
5. Observe: DBA agent is NOT invoked.
**Expected:** Deterministic rules correctly match and filter.

### AC-010: LLM Fallback Routing
**Maps to:** US-016
1. Post a message that doesn't match any deterministic rules but is semantically related to an agent's domain.
2. Example: Agent "Data Scientist" has keywords "pandas, numpy, ML." Post: "I'm trying to figure out why my model keeps overfitting."
3. Observe: LLM dispatcher evaluates and routes to "Data Scientist."
**Expected:** Fallback routing catches semantically relevant messages that keyword matching misses.

### AC-011: @Mention Override
**Maps to:** US-014
1. Post message that would NOT match any agent's routing rules: "Hey @DBA what's up?"
2. Observe: DBA agent is invoked despite no rule match.
3. Post message: "@CodeReviewer @DBA can you both look at this?"
4. Observe: Both agents invoked.
**Expected:** @mentions bypass all routing logic.

### AC-012: Empty Dispatch
**Maps to:** US-016
1. Post message: "Nice weather today."
2. No agent routing rules match. LLM dispatcher also returns empty.
3. Observe: No agent responds. No error. Message sits in channel.
**Expected:** Silent no-op when nothing matches.

---

## Threads & Context

### AC-013: Thread Creation and Agent Responses
**Maps to:** US-018, US-019
1. Post message "Help me debug this Python error" in a channel.
2. Observe: thread is created. Agent responses appear inside the thread.
3. Observe: main channel shows the human message + thread preview ("Debug Agent replied — 2 messages").
**Expected:** Clean main channel, agent activity in threads.

### AC-014: Multi-Agent Thread Collaboration
**Maps to:** US-020, US-022
1. Post message: "I need to build a user authentication system."
2. Agent "Architect" responds with a design.
3. Architect posts: "@DBA what do you think about the users table schema I proposed?"
4. Observe: DBA agent sees the full thread context including Architect's message and responds with feedback.
5. Observe: Human sees both agents' messages in the thread.
**Expected:** Agents see each other's messages and can @mention each other.

### AC-015: Context Summarization
**Maps to:** US-021
1. Create a thread with 200 messages (scripted test data).
2. Post a new message that invokes an agent.
3. Observe: the context sent to the agent includes a summary of messages 1-180 and the full text of messages 181-200.
4. Observe: the human browsing the thread sees all 200 messages in full.
**Expected:** Agents receive summarized context when thread exceeds limits. Humans see full history.

### AC-016: Internal Reasoning Hidden
**Maps to:** US-022
1. Post a message requiring the agent to use a tool (e.g., "search the web for Python 3.13 release date").
2. Observe: agent response in thread shows only the final answer. No chain-of-thought, no tool call JSON, no "Let me search..." thinking blocks.
**Expected:** Only final output visible. Internal reasoning suppressed.

---

## Agent Handoff

### AC-017: Handoff Tool Execution
**Maps to:** US-024, US-025
1. Agent A is invoked in a thread.
2. Agent A calls `handoff(target_agent_name="DBA", context="Need schema review for users table")`.
3. Observe: a handoff message appears in the thread: "@Agent A handed off to @DBA: Need schema review for users table."
4. Observe: DBA agent is invoked with the handoff context and full thread history.
5. DBA responds addressing the schema question.
**Expected:** Handoff visible, target agent invoked with context.

---

## Loop Prevention

### AC-018: Turn Limit Enforcement
**Maps to:** US-026
1. Set turn limit to 5 in workspace settings.
2. Create a scenario where two agents go back and forth.
3. After the 5th agent message without a human message, observe: system posts "Agent conversation has reached the turn limit."
4. Observe: further agent messages in that thread are suppressed.
5. Human posts "Continue." Observe: counters reset, agents resume.
**Expected:** Turn limit enforced. Human reactivation works.

### AC-019: Cycle Detection
**Maps to:** US-027
1. Set cycle detection to trigger on 3+ repetitions.
2. Orchestrate a cycle: Agent A → Agent B → Agent A → Agent B → Agent A → Agent B.
3. Observe: system detects the cycle after the 3rd A→B→A pattern, posts a message identifying the loop, and pauses both agents.
**Expected:** Cycle detected and broken automatically.

### AC-020: Time-Based Pause
**Maps to:** US-028
1. Set time-based pause to 2 minutes (for testing).
2. Post a message that triggers agent activity. Let agents converse for >2 minutes with no human message.
3. Observe: system pauses agent activity at the 2-minute mark.
**Expected:** Timeout enforced. Agents paused.

---

## Tools & MCP

### AC-021: Built-in Tool Usage
**Maps to:** US-030
1. Create an agent with the "Web Search" tool enabled.
2. Post: "What's the latest version of Python?"
3. Observe: agent uses web search tool, returns answer with current version.
**Expected:** Built-in tools function and return results via AgentOS.

### AC-022: Simple Tool Creation
**Maps to:** US-031
1. Open Tool Creator in Simple Mode.
2. Enter description: "A tool that takes a city name and returns the current time in that city using the worldtimeapi.org API."
3. Submit. Observe: system generates Python tool code using Agno SDK patterns.
4. Review the code. Approve.
5. Create an agent and assign the new tool.
6. Post: "What time is it in Tokyo?" — observe agent uses the tool.
**Expected:** Natural language → working tool → agent uses it.

### AC-023: Advanced Tool Creation
**Maps to:** US-032
1. Open Tool Creator in Advanced Mode.
2. Observe: a tool file opens in the system's default editor (VS Code or equivalent) at a known path.
3. Edit the file, save.
4. Observe: Rivulets detects the file change and re-registers the tool.
**Expected:** External editor workflow functions. Save triggers re-registration.

### AC-024: MCP Server Registration
**Maps to:** US-033
1. Navigate to MCP Servers. Click "Add Server."
2. Enter name and URL of a running MCP server.
3. Observe: system connects, discovers tools, lists them.
4. Assign one of the discovered MCP tools to an agent.
5. Post a message that triggers use of that tool.
6. Observe: agent uses the MCP tool and returns results.
**Expected:** External MCP tools usable by agents.

---

## P2P Sync

### AC-025: Full Workspace Sync
**Maps to:** US-035, US-037
1. Set up machine A with full workspace (3 channels, 5 agents, 20 threads with messages, 2 tools).
2. Install on machine B with same workspace key.
3. Observe: after sync, machine B has identical channels, agents, threads, tools.
4. On machine B, create a new agent.
5. Observe: within 5 seconds, the new agent appears on machine A.
**Expected:** Bidirectional sync. Changes on any node propagate.

### AC-026: LLM Key Isolation in Sync
**Maps to:** US-036
1. Set up machine A with OpenAI key "sk-aaa".
2. Set up machine B with OpenAI key "sk-bbb" (same workspace).
3. Inspect sync traffic or sync database on machine B.
4. Verify: machine A's "sk-aaa" key is never present in machine B's configuration or sync data.
**Expected:** Provider keys never leave their origin machine.

### AC-027: Offline Operation
**Maps to:** US-037
1. Machine A and B are synced.
2. Disconnect machine B from the network.
3. On machine B: create a new channel, post messages, create an agent.
4. Observe: all operations succeed on machine B.
5. Reconnect machine B.
6. Observe: changes from B propagate to A. Changes from A (made during disconnect) propagate to B.
**Expected:** Full offline functionality. Sync reconciles on reconnect.

---

## File Handling

### AC-028: File Upload and Agent Access
**Maps to:** US-041, US-042
1. In a thread, upload a CSV file: "sales_data.csv."
2. Post message: "Analyze the sales data in the attached CSV."
3. Observe: agent receives file reference, reads the CSV via workspace tool, responds with analysis.
**Expected:** File upload → agent access → meaningful response.

### AC-029: File Sync Across Nodes
**Maps to:** US-043
1. On machine A, upload a file to a thread.
2. Observe: file syncs to machine B.
3. On machine B, post a message that causes an agent to access the file.
4. Observe: agent on machine B successfully reads the file from its local copy.
**Expected:** Files replicated. Agents on any node can access.

---

## Security

### AC-030: Workspace Key Storage
**Maps to:** NFR-3.1
1. After installation, locate the workspace key file on disk.
2. Verify: file permissions are 0600 (owner read/write only).
3. Verify: key is NOT stored in plaintext in any log file.
**Expected:** Key is protected at rest.

### AC-031: Localhost-Only Binding
**Maps to:** NFR-3.4
1. After installation, run `netstat` or equivalent.
2. Verify: Rivulets web server is bound to 127.0.0.1, not 0.0.0.0.
3. From another machine on the same network, attempt to access `http://<machine-ip>:8484`.
4. Observe: connection refused.
**Expected:** UI only accessible from localhost.
