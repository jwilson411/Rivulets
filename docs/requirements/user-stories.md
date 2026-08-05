# Rivulets — User Stories

## Priority Key
- **P0** — MVP. Must ship before anything is usable.
- **P1** — High value. Should ship shortly after MVP.
- **P2** — Nice to have. Future roadmap.

---

## Workspace & Installation

### US-001: Install and Configure Workspace [P0]
**As a** new user, **I want to** install Rivulets on my machine, configure my LLM provider keys, and generate a workspace key, **so that** I have a functioning local instance.

### US-002: Access Web UI via Localhost [P0]
**As a** user, **I want to** open my browser to `localhost:<port>` and see the Rivulets chat interface, **so that** I can interact with my workspace without installing a desktop app.

### US-003: Configure Multiple LLM Providers [P0]
**As a** user, **I want to** provide API keys for one or more LLM providers during setup (OpenAI, Anthropic, DeepSeek, etc.), **so that** my agents can use the models I have access to.

---

## Channels & Teams

### US-004: Create a Channel [P0]
**As a** user, **I want to** create named channels (like Slack), **so that** I can organize conversations by topic or project.

### US-005: Create a Team [P0]
**As a** user, **I want to** create a team (a named group of agents), **so that** I can assign a collection of agents to a channel as a unit.

### US-006: Assign Team to Channel [P0]
**As a** user, **I want to** assign a team to a channel, **so that** all agents on that team monitor the channel and respond when relevant.

### US-007: Reorder and Archive Channels [P1]
**As a** user, **I want to** reorder channels in my sidebar and archive old ones, **so that** my workspace stays organized.

---

## Agent Management

### US-008: Create an Agent [P0]
**As a** user, **I want to** create a new agent by providing a name, description, and instructions (system prompt), **so that** I can add specialized AI teammates to my workspace.

### US-009: Choose Agent Model [P0]
**As a** user, **I want to** select which LLM model an agent uses from my configured providers, **so that** I can match model capability and cost to the agent's role.

### US-010: Assign Tools to Agent [P0]
**As a** user, **I want to** wire existing tools and MCP servers to an agent during or after creation, **so that** the agent has the capabilities it needs to do its job.

### US-011: Add Agent to Team [P0]
**As a** user, **I want to** add agents to one or more teams, **so that** they participate in the channels those teams are assigned to.

### US-012: Edit Agent Configuration [P1]
**As a** user, **I want to** modify an agent's name, description, instructions, model, or tools after creation, **so that** I can refine its behavior over time.

### US-013: View Agent Run History [P1]
**As a** user, **I want to** see an agent's past runs, including tokens used and cost, **so that** I can monitor usage and debug behavior.

---

## Autonomous Agent Participation

### US-014: Channel Dispatcher Routes Messages [P0]
**As a** user, **I want to** post a message in a channel and have the system determine which agents should respond, **so that** I don't have to manually @mention agents.

### US-015: Deterministic Routing Rules [P0]
**As a** system, **I want to** match incoming messages against deterministic routing rules registered per agent, **so that** most routing decisions are fast and cost-free.

### US-016: LLM Fallback Routing [P0]
**As a** system, **I want to** escalate to a lightweight LLM-based dispatcher when no deterministic rules match, **so that** messages still get routed correctly in ambiguous cases.

### US-017: Agent-Generated Routing Rules on Creation [P0]
**As a** system, **I want to** generate deterministic routing rules via LLM when a new agent is created (based on its name, description, and instructions), **so that** the agent has matching rules from day one without manual configuration.

---

## Threads & Conversations

### US-018: Thread Creation per Message [P0]
**As a** user, **I want to** have every message I post in the main channel automatically create a thread, **so that** agent responses are organized under my topic rather than cluttering the main feed.

### US-019: Agents Respond in Threads [P0]
**As an** agent, **I want to** post my responses inside the thread created by the human's message, **so that** the main channel stays clean and each topic has its own context.

### US-020: Agents Can Post to Existing Threads [P0]
**As an** agent, **I want to** read the full thread history and post questions or responses within it, **so that** I can collaborate with other agents and the human organically.

### US-021: Thread Context Management [P0]
**As a** system, **I want to** maintain full message history for threads up to a context limit, then switch to a running summary, **so that** agents always have relevant context without exceeding token limits.

### US-022: Humans See Agent Interactions [P0]
**As a** user, **I want to** see agent-to-agent questions and responses in the thread (not their internal reasoning), **so that** I can follow the collaboration without noise.

### US-023: Thread-Scoped Agent Participation [P1]
**As an** agent, **I want to** only see messages from threads I've been invoked in, **so that** I don't waste tokens reading irrelevant conversations.

---

## Agent Handoff

### US-024: Built-in Handoff Tool [P1]
**As an** agent, **I want to** call a `handoff(target_agent, context)` tool to explicitly pass work to another agent, **so that** I can delegate subtasks cleanly.

### US-025: Handoff Visibility [P1]
**As a** user, **I want to** see a message in the thread when one agent hands off to another, **so that** I understand the workflow without guessing.

---

## Loop Prevention

### US-026: Configurable Turn Limit [P0]
**As a** workspace admin, **I want to** set a maximum number of agent-to-agent exchanges per thread without human intervention, **so that** agents don't loop forever.

### US-027: Cycle Detection [P1]
**As a** system, **I want to** detect repeating agent interaction patterns (A→B→A→B) and break the loop, **so that** costs don't spiral from infinite conversations.

### US-028: Timeout-Based Pause [P1]
**As a** system, **I want to** pause agent participation in a thread if agents have been active for more than a configurable duration without a human message, **so that** runaway threads don't consume resources.

### US-029: Human Reactivation [P1]
**As a** user, **I want to** manually resume a paused thread with a single click or message, **so that** I stay in control of agent activity.

---

## Tool & MCP Management

### US-030: Built-in Tool Library [P0]
**As a** user, **I want to** browse and enable a library of pre-built tools (web search, file system, database query, code execution, etc.), **so that** I don't have to build common capabilities from scratch.

### US-031: Create Tool via Simple Mode [P0]
**As a** user, **I want to** describe what I want a tool to do in natural language and have the system generate the Python tool code, **so that** I can create custom tools without deep coding.

### US-032: Create Tool via Advanced Mode [P1]
**As a** power user, **I want to** open a tool's Python file directly in VS Code from the Rivulets UI, **so that** I can write complex tools in my preferred editor.

### US-033: Register MCP Server [P1]
**As a** user, **I want to** point Rivulets at an external MCP server URL and make its tools available to agents, **so that** I can leverage the MCP ecosystem.

### US-034: Create MCP Server [P2]
**As a** power user, **I want to** scaffold and deploy a new MCP server from within Rivulets, **so that** I can build custom tool servers without leaving the platform.

---

## Peer-to-Peer Sync

### US-035: Multi-Machine Workspace Sync [P1]
**As a** user, **I want to** install Rivulets on a second machine using the same workspace key and have all agents, channels, teams, threads, tools, and files sync between nodes, **so that** I have the same workspace everywhere.

### US-036: LLM Keys Not Synced [P1]
**As a** user, **I want to** provide LLM provider keys separately on each machine during installation, **so that** my API keys are never transmitted between nodes.

### US-037: Offline Resilience [P1]
**As a** user, **I want to** continue using Rivulets on my local machine even when other nodes in my mesh are offline, **so that** I'm never blocked by network issues.

### US-038: Conflict Resolution [P2]
**As a** user, **I want to** have the system intelligently merge changes made on different nodes (last-write-wins with visibility into conflicts), **so that** I can work on multiple machines without losing data.

---

## Invite System

### US-039: Cryptographic Invite Generation [P2]
**As a** workspace owner, **I want to** generate an invite link or code cryptographically tied to my workspace key, **so that** I can bring other humans into my workspace securely.

### US-040: Accept Invite and Join Workspace [P2]
**As an** invited user, **I want to** use an invite code to join an existing workspace, **so that** I can collaborate with the human-and-AI team.

---

## File Sharing

### US-041: Share Files in Threads [P1]
**As a** user or agent, **I want to** upload and share files (code, images, PDFs, CSVs) in threads, **so that** agents can work with real documents.

### US-042: Agent File Access [P1]
**As an** agent, **I want to** receive shared file contents via workspace tools, **so that** I can process documents as part of my work.

### US-043: File Sync Across Nodes [P2]
**As a** user, **I want to** have files shared in threads automatically sync to all my machines, **so that** agents on any node can access them.
