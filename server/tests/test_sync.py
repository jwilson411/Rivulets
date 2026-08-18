"""P2P sync (FR-9). Three layers of coverage here:

  - Pure vector-clock comparison/merge logic (sync/apply.py) — no network,
    no DB.
  - record_local_change / apply_remote_change (the generic entity path)
    and apply_remote_tool_change (tool's bespoke source-code-aware path)
    against a real (in-memory) DB via the db_session fixture — no network.
  - One real end-to-end SyncEngine test: two actual libp2p hosts in their
    own trio threads, matching PNet PSK, manual connect (FR-9.3, no mDNS
    involved in the connect path itself — though engine.start() does
    still register a real mDNS/zeroconf service as a side effect, same as
    production), gossipsub message delivery. This is the same scenario
    validated against a throwaway script before sync/engine.py was wired
    into the app at all — kept here as a real regression test since this
    is the most novel, highest-risk code in the whole feature and
    deserves more than "trust me, I ran it once."

The HTTP layer (api/sync.py) is tested via the client fixture, where
SyncEngine.start()/.stop() are no-op'd (see conftest.py) — status/
connect/disconnect are exercised against a not-running engine, which is
exactly what FR-9.5 says should still work.
"""

import asyncio
import hashlib
import json
import os
import struct
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import trio
from fastapi.testclient import TestClient
from libp2p.peer.id import ID as _ID  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.tool_resolution import resolve_agent_tools
from rivulets.config import get_settings
from rivulets.db.models import (
    Agent,
    AgentPeerPreference,
    AgentRoutingRule,
    AgentTool,
    AgentToolScope,
    Channel,
    File,
    MCPServer,
    Message,
    Rivulet,
    SyncConflict,
    SyncPendingInbound,
    SyncResolution,
    Team,
    TeamAgent,
    Tool,
    ToolVersion,
    VectorClockTracker,
    Workflow,
    WorkflowNode,
    WorkspaceSetting,
)
from rivulets.db.session import session_scope
from rivulets.sync.agent_dispatch import AgentDispatchRequest
from rivulets.sync.apply import (
    AGENT_PEER_PREFERENCE_SPEC,
    AGENT_ROUTING_RULE_SPEC,
    AGENT_SPEC,
    AGENT_TOOL_SCOPE_SPEC,
    AGENT_TOOL_SPEC,
    CHANNEL_SPEC,
    MCP_SERVER_SPEC,
    MESSAGE_SPEC,
    RESOLUTION_FIELD,
    RESOLUTION_NODE_FIELD,
    RIVULET_SPEC,
    TEAM_AGENT_SPEC,
    TEAM_SPEC,
    TOMBSTONE_FIELD,
    WORKFLOW_NODE_SPEC,
    WORKFLOW_SPEC,
    WORKSPACE_SETTING_SPEC,
    ClockComparison,
    apply_remote_change,
    apply_remote_delete,
    apply_remote_file_change,
    apply_remote_tool_change,
    compare_vector_clocks,
    current_vector_clock,
    encode_entity_id,
    entity_pk_value,
    handle_incoming_state_change,
    merge_vector_clocks,
    record_local_change,
    record_resolution,
    retry_pending_inbound,
)
from rivulets.sync.engine import (
    CoordinatorStatus,
    PeerInfo,
    SyncEngine,
    _bound_port,  # pyright: ignore[reportPrivateUsage]
    _dialable_own_address,  # pyright: ignore[reportPrivateUsage]
    _is_lan_address,  # pyright: ignore[reportPrivateUsage]
    _PeerConnectionNotifee,  # pyright: ignore[reportPrivateUsage]
    get_sync_engine,
    init_sync_engine,
    own_address_scope,
    reset_sync_engine_for_testing,
    running_in_container,
)
from rivulets.sync.file_transfer import HASH_LEN, HIT_PREFIX, MAX_FILE_BYTES, MISS_MARKER
from rivulets.validation import local_path_for_content_hash

_AGENT_FIELDS = {
    "description": "A test agent used only in sync tests.",
    "instructions": "Do the thing.",
    "model": "openai:gpt-4",
}


def test_compare_vector_clocks_equal() -> None:
    assert compare_vector_clocks({"a": 1}, {"a": 1}) is ClockComparison.EQUAL
    assert compare_vector_clocks({}, {}) is ClockComparison.EQUAL


def test_compare_vector_clocks_remote_newer() -> None:
    assert compare_vector_clocks({"a": 1}, {"a": 2}) is ClockComparison.REMOTE_NEWER
    assert compare_vector_clocks({}, {"a": 1}) is ClockComparison.REMOTE_NEWER


def test_compare_vector_clocks_local_newer() -> None:
    assert compare_vector_clocks({"a": 2}, {"a": 1}) is ClockComparison.LOCAL_NEWER
    assert compare_vector_clocks({"a": 1}, {}) is ClockComparison.LOCAL_NEWER


def test_compare_vector_clocks_concurrent() -> None:
    assert compare_vector_clocks({"a": 2, "b": 0}, {"a": 1, "b": 1}) is ClockComparison.CONCURRENT


def test_merge_vector_clocks() -> None:
    assert merge_vector_clocks({"a": 2, "b": 0}, {"a": 1, "b": 3}) == {"a": 2, "b": 3}


async def test_record_local_change_bumps_own_component(db_session: AsyncSession) -> None:
    vc = await record_local_change(db_session, "agent", "agent-1", "node-a")
    assert vc == {"node-a": 1}
    vc2 = await record_local_change(db_session, "agent", "agent-1", "node-a")
    assert vc2 == {"node-a": 2}


async def test_apply_remote_agent_change_creates_new_agent(db_session: AsyncSession) -> None:
    result = await apply_remote_change(
        db_session,
        AGENT_SPEC,
        "agent-1",
        {"node-b": 1},
        "node-b",
        {"name": "Remote Agent", **_AGENT_FIELDS},
    )
    assert result.applied is True
    assert result.conflict is False

    agent = await db_session.get(Agent, "agent-1")
    assert agent is not None
    assert agent.name == "Remote Agent"
    assert agent.vector_clock == 1


async def test_apply_remote_agent_change_ignores_stale(db_session: AsyncSession) -> None:
    await record_local_change(db_session, "agent", "agent-1", "node-a")  # local at {node-a: 1}

    result = await apply_remote_change(
        db_session,
        AGENT_SPEC,
        "agent-1",
        {"node-a": 0},
        "node-b",
        {"name": "Stale", **_AGENT_FIELDS},
    )

    assert result.applied is False
    assert result.conflict is False
    assert await db_session.get(Agent, "agent-1") is None  # never created


async def test_apply_remote_agent_change_detects_conflict(db_session: AsyncSession) -> None:
    db_session.add(Agent(id="agent-1", name="Local", **_AGENT_FIELDS))
    await db_session.commit()
    await record_local_change(db_session, "agent", "agent-1", "node-a")  # local at {node-a: 1}

    result = await apply_remote_change(
        db_session,
        AGENT_SPEC,
        "agent-1",
        {"node-b": 1},
        "node-b",
        {"name": "Remote", **_AGENT_FIELDS},
    )

    assert result.applied is False
    assert result.conflict is True

    agent = await db_session.get(Agent, "agent-1")
    assert agent is not None
    assert agent.name == "Local"  # untouched

    conflicts = list((await db_session.execute(select(SyncConflict))).scalars().all())
    assert len(conflicts) == 1
    assert conflicts[0].remote_node_id == "node-b"
    assert conflicts[0].entity_id == "agent-1"
    assert conflicts[0].resolved is False


async def test_apply_remote_channel_change_creates_channel(db_session: AsyncSession) -> None:
    result = await apply_remote_change(
        db_session,
        CHANNEL_SPEC,
        "chan-1",
        {"node-b": 1},
        "node-b",
        {"name": "general", "description": "General chat", "position": 0, "archived": False},
    )
    assert result.applied is True
    channel = await db_session.get(Channel, "chan-1")
    assert channel is not None
    assert channel.name == "general"


async def test_apply_remote_team_change_creates_team(db_session: AsyncSession) -> None:
    result = await apply_remote_change(
        db_session,
        TEAM_SPEC,
        "team-1",
        {"node-b": 1},
        "node-b",
        {"name": "Eng", "description": None},
    )
    assert result.applied is True
    team = await db_session.get(Team, "team-1")
    assert team is not None
    assert team.name == "Eng"


def test_encode_entity_id_roundtrips_single_and_composite_keys() -> None:
    assert entity_pk_value(TEAM_SPEC, encode_entity_id(TEAM_SPEC, ("team-1",))) == "team-1"
    composite = encode_entity_id(TEAM_AGENT_SPEC, ("team-1", "agent-1"))
    assert composite == "team-1:agent-1"
    assert entity_pk_value(TEAM_AGENT_SPEC, composite) == ("team-1", "agent-1")


def test_encode_entity_id_survives_colon_in_scope_name() -> None:
    """#317: AgentToolScope.scope values contain their own colons (e.g.
    "channels:manage", agentos/tool_scopes.py's TOOL_SCOPES) -- decoding
    must only ever split on the *first* colon, or a scope name would get
    mangled into the wrong tuple."""
    entity_id = encode_entity_id(AGENT_TOOL_SCOPE_SPEC, ("agent-1", "channels:manage"))
    assert entity_id == "agent-1:channels:manage"
    assert entity_pk_value(AGENT_TOOL_SCOPE_SPEC, entity_id) == ("agent-1", "channels:manage")


def test_encode_entity_id_rejects_colon_in_non_last_component() -> None:
    with pytest.raises(ValueError, match="must not contain"):
        encode_entity_id(TEAM_AGENT_SPEC, ("team:1", "agent-1"))


async def test_apply_remote_team_agent_change_creates_membership(db_session: AsyncSession) -> None:
    """#317: team membership is its own synced entity (TEAM_AGENT_SPEC),
    keyed by the (team_id, agent_id) pair -- previously this join table
    wasn't synced at all, so a second node had a team with no members."""
    db_session.add(Team(id="team-1", name="Support"))
    db_session.add(Agent(id="agent-1", name="Rex", **_AGENT_FIELDS))
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        TEAM_AGENT_SPEC,
        encode_entity_id(TEAM_AGENT_SPEC, ("team-1", "agent-1")),
        {"node-b": 1},
        "node-b",
        {"position": 2},
    )
    assert result.applied is True
    membership = await db_session.get(TeamAgent, ("team-1", "agent-1"))
    assert membership is not None
    assert membership.position == 2


async def test_apply_remote_team_agent_change_ignores_stale(db_session: AsyncSession) -> None:
    entity_id = encode_entity_id(TEAM_AGENT_SPEC, ("team-1", "agent-1"))
    await record_local_change(db_session, "team_agent", entity_id, "node-a")

    result = await apply_remote_change(
        db_session, TEAM_AGENT_SPEC, entity_id, {"node-a": 0}, "node-b", {"position": 0}
    )
    assert result.applied is False
    assert await db_session.get(TeamAgent, ("team-1", "agent-1")) is None


async def test_apply_remote_delete_removes_team_agent_membership(db_session: AsyncSession) -> None:
    db_session.add(Team(id="team-1", name="Support"))
    db_session.add(Agent(id="agent-1", name="Rex", **_AGENT_FIELDS))
    db_session.add(TeamAgent(team_id="team-1", agent_id="agent-1", position=0))
    await db_session.commit()
    entity_id = encode_entity_id(TEAM_AGENT_SPEC, ("team-1", "agent-1"))

    result = await apply_remote_delete(db_session, "team_agent", entity_id, {"node-b": 1}, "node-b")
    assert result.applied is True
    assert await db_session.get(TeamAgent, ("team-1", "agent-1")) is None


async def test_apply_remote_team_agent_change_detects_conflict_with_local_snapshot(
    db_session: AsyncSession,
) -> None:
    """#349: the CONCURRENT branch passed the wire entity_id
    ("team-1:agent-1") straight to db.get(), which is wrong for a
    composite-key mapper like TeamAgent -- the local snapshot came back
    empty (or the handler died) for the exact join entities #317 started
    syncing. It must decode through entity_pk_value like REMOTE_NEWER."""
    db_session.add(Team(id="team-1", name="Support"))
    db_session.add(Agent(id="agent-1", name="Rex", **_AGENT_FIELDS))
    db_session.add(TeamAgent(team_id="team-1", agent_id="agent-1", position=3))
    await db_session.commit()
    entity_id = encode_entity_id(TEAM_AGENT_SPEC, ("team-1", "agent-1"))
    await record_local_change(db_session, "team_agent", entity_id, "node-a")

    result = await apply_remote_change(
        db_session,
        TEAM_AGENT_SPEC,
        entity_id,
        {"node-b": 1},  # concurrent with local's {node-a: 1} -- neither dominates
        "node-b",
        {"position": 7},
    )
    assert result.applied is False
    assert result.conflict is True

    membership = await db_session.get(TeamAgent, ("team-1", "agent-1"))
    assert membership is not None
    assert membership.position == 3  # untouched

    conflicts = list((await db_session.execute(select(SyncConflict))).scalars().all())
    assert len(conflicts) == 1
    assert conflicts[0].entity_id == entity_id
    assert json.loads(conflicts[0].local_snapshot) == {"position": 3}
    assert json.loads(conflicts[0].remote_snapshot) == {"position": 7}


async def test_apply_remote_agent_tool_change_creates_assignment(db_session: AsyncSession) -> None:
    """#317: tool assignment (agent_tool) is a synced fact independent of
    Tool itself -- an agent's tools existing on a peer without this join
    row meant the agent replied with none of them."""
    db_session.add(Agent(id="agent-1", name="Rex", **_AGENT_FIELDS))
    db_session.add(
        Tool(id="tool-1", name="lookup", description="Looks something up.", tool_type="builtin")
    )
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        AGENT_TOOL_SPEC,
        encode_entity_id(AGENT_TOOL_SPEC, ("agent-1", "tool-1")),
        {"node-b": 1},
        "node-b",
        {},
    )
    assert result.applied is True
    assert await db_session.get(AgentTool, ("agent-1", "tool-1")) is not None


async def test_apply_remote_agent_tool_scope_change_creates_grant(db_session: AsyncSession) -> None:
    """#317 reverses AgentToolScope's earlier "not P2P-synced" design --
    see db/models.py's docstring. Uses a real scope name with a colon in
    it (agentos/tool_scopes.py's TOOL_SCOPES) to exercise the composite
    entity_id decode with real data, not just the unit-level encode/decode
    tests above."""
    db_session.add(Agent(id="agent-1", name="Rex", **_AGENT_FIELDS))
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        AGENT_TOOL_SCOPE_SPEC,
        encode_entity_id(AGENT_TOOL_SCOPE_SPEC, ("agent-1", "channels:manage")),
        {"node-b": 1},
        "node-b",
        {},
    )
    assert result.applied is True
    assert await db_session.get(AgentToolScope, ("agent-1", "channels:manage")) is not None


async def test_apply_remote_agent_routing_rule_change_creates_rule(
    db_session: AsyncSession,
) -> None:
    """#317: AgentRoutingRule has a real `id`, so it's on the ordinary
    single-key generic path -- FR-3.3's deterministic routing previously
    only ever worked on whichever node generated the rules."""
    db_session.add(Agent(id="agent-1", name="Rex", **_AGENT_FIELDS))
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        AGENT_ROUTING_RULE_SPEC,
        "rule-1",
        {"node-b": 1},
        "node-b",
        {"agent_id": "agent-1", "rule_type": "keyword", "pattern": '["support"]', "priority": 0},
    )
    assert result.applied is True
    rule = await db_session.get(AgentRoutingRule, "rule-1")
    assert rule is not None
    assert rule.agent_id == "agent-1"
    assert rule.rule_type == "keyword"


async def test_apply_remote_channel_change_syncs_team_id(db_session: AsyncSession) -> None:
    """#317: Channel.team_id used to be excluded from CHANNEL_SPEC on the
    theory that FK-ordering needed a retry queue this module didn't have
    -- it already did (SyncPendingInbound), so this now syncs like any
    other optional FK (Rivulet.channel_id's existing hazard)."""
    db_session.add(Team(id="team-1", name="Support"))
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        CHANNEL_SPEC,
        "chan-1",
        {"node-b": 1},
        "node-b",
        {
            "name": "general",
            "description": None,
            "position": 0,
            "archived": False,
            "team_id": "team-1",
        },
    )
    assert result.applied is True
    channel = await db_session.get(Channel, "chan-1")
    assert channel is not None
    assert channel.team_id == "team-1"


async def test_apply_remote_channel_change_queues_when_team_missing(
    db_session: AsyncSession,
) -> None:
    """Same FK-ordering hazard as Rivulet.channel_id (module docstring):
    a channel's team assignment can arrive before the team's own create
    message has, over gossipsub's no-cross-type ordering guarantee."""
    result = await apply_remote_change(
        db_session,
        CHANNEL_SPEC,
        "chan-1",
        {"node-b": 1},
        "node-b",
        {
            "name": "general",
            "description": None,
            "position": 0,
            "archived": False,
            "team_id": "missing-team",
        },
    )
    assert result.applied is False
    assert result.conflict is False
    assert await db_session.get(Channel, "chan-1") is None
    pending = list((await db_session.execute(select(SyncPendingInbound))).scalars().all())
    assert len(pending) == 1
    assert pending[0].entity_type == "channel"


async def test_apply_remote_agent_change_syncs_approved_for_unattended_tools(
    db_session: AsyncSession,
) -> None:
    """#317: an owner's unattended-tool approval used to stay behind on
    whichever node the owner happened to use -- every other node kept
    failing the agent's sensitive tools closed regardless."""
    result = await apply_remote_change(
        db_session,
        AGENT_SPEC,
        "agent-1",
        {"node-b": 1},
        "node-b",
        {"name": "Rex", "approved_for_unattended_tools": True, **_AGENT_FIELDS},
    )
    assert result.applied is True
    agent = await db_session.get(Agent, "agent-1")
    assert agent is not None
    assert agent.approved_for_unattended_tools is True


async def test_multi_peer_dispatch_topology_replicates(db_session: AsyncSession) -> None:
    """#317's core scenario: a second node receiving every dispatch-
    topology entity type purely through sync-apply -- no live libp2p
    needed to prove this (see the module docstring's three-tier split) --
    ends up with a *working* topology, not just rows sitting unconnected.

    Deliberately applies team_agent before its team exists, to exercise
    the FK-ordering retry queue (module docstring) for one of #317's new
    join entities the same way it already covers Rivulet.channel_id.
    Finishes by calling tool_resolution.resolve_agent_tools -- the same
    function agentos/service.py's _build_agno_agent calls when actually
    building the agent to run -- to prove the tool is genuinely usable
    once assignment (agent_tool) *and* scope (agent_tool_scope) have both
    landed, not merely present as disconnected DB rows."""
    remote = "node-a"

    membership_id = encode_entity_id(TEAM_AGENT_SPEC, ("team-1", "agent-1"))
    result = await apply_remote_change(
        db_session, TEAM_AGENT_SPEC, membership_id, {remote: 1}, remote, {"position": 0}
    )
    assert result.applied is False  # team-1 doesn't exist here yet
    assert await db_session.get(TeamAgent, ("team-1", "agent-1")) is None

    await apply_remote_change(
        db_session,
        TEAM_SPEC,
        "team-1",
        {remote: 1},
        remote,
        {"name": "Support", "description": None},
    )
    await apply_remote_change(
        db_session,
        AGENT_SPEC,
        "agent-1",
        {remote: 1},
        remote,
        {"name": "Rex", "approved_for_unattended_tools": True, **_AGENT_FIELDS},
    )
    # handle_incoming_state_change calls this after every successful apply
    # (module docstring) -- called explicitly here since this test drives
    # apply_remote_change directly rather than through that entry point.
    await retry_pending_inbound(db_session)

    membership = await db_session.get(TeamAgent, ("team-1", "agent-1"))
    assert membership is not None, "team_agent never got retried once its team arrived"
    assert membership.position == 0

    await apply_remote_change(
        db_session,
        CHANNEL_SPEC,
        "chan-1",
        {remote: 1},
        remote,
        {
            "name": "support-chat",
            "description": None,
            "position": 0,
            "archived": False,
            "team_id": "team-1",
        },
    )
    channel = await db_session.get(Channel, "chan-1")
    assert channel is not None
    assert channel.team_id == "team-1"

    # A builtin tool -- seeded locally by every node's own startup, not
    # synced (module docstring: "'builtin' tools aren't user-created at
    # all"), unlike the custom-tool case apply_remote_tool_change already
    # covers elsewhere in this file.
    db_session.add(
        Tool(
            id="tool-1",
            name="create_channel",
            description="Creates a channel.",
            tool_type="builtin",
            required_scope="channels:manage",
        )
    )
    await db_session.commit()

    await apply_remote_change(
        db_session,
        AGENT_TOOL_SPEC,
        encode_entity_id(AGENT_TOOL_SPEC, ("agent-1", "tool-1")),
        {remote: 1},
        remote,
        {},
    )
    assert await db_session.get(AgentTool, ("agent-1", "tool-1")) is not None

    agent = await db_session.get(Agent, "agent-1")
    assert agent is not None
    # Assigned but not yet scope-granted -- #188's two-gate design means
    # this must still fail closed even though the join row now exists.
    assert await resolve_agent_tools(db_session, agent) == []

    await apply_remote_change(
        db_session,
        AGENT_TOOL_SCOPE_SPEC,
        encode_entity_id(AGENT_TOOL_SCOPE_SPEC, ("agent-1", "channels:manage")),
        {remote: 1},
        remote,
        {},
    )
    assert await db_session.get(AgentToolScope, ("agent-1", "channels:manage")) is not None

    resolved = await resolve_agent_tools(db_session, agent)
    assert len(resolved) == 1


async def test_apply_remote_workflow_change_syncs_published(db_session: AsyncSession) -> None:
    """#249: `published` is a synced field on WORKFLOW_SPEC now, not just
    `name`/`description` -- a peer that's already received a workflow's
    nodes/connections (WORKFLOW_NODE_SPEC/WORKFLOW_CONNECTION_SPEC) also
    needs the flag itself, or workflows/engine.py's nested-run published
    gate would keep treating an already-published workflow as a draft."""
    db_session.add(Workflow(id="wf-1", name="synced-flow", description=None, published=False))
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        WORKFLOW_SPEC,
        "wf-1",
        {"node-b": 1},
        "node-b",
        {"name": "synced-flow", "description": None, "published": True},
    )
    assert result.applied is True

    workflow = await db_session.get(Workflow, "wf-1")
    assert workflow is not None
    assert workflow.published is True


async def test_apply_remote_workflow_change_syncs_remediation_and_on_call(
    db_session: AsyncSession,
) -> None:
    """#316: `on_failure_workflow_id` and `on_call_agent_id` were added to
    the Workflow model after WORKFLOW_SPEC and never synced -- a peer had
    nothing to apply them onto, so auto-remediation (#94 layer 2) and
    on-call @mention (#94 layer 3) silently never fired there."""
    db_session.add(Workflow(id="wf-fallback", name="fallback-flow", description=None))
    db_session.add(Agent(id="agent-oncall", name="OnCall", **_AGENT_FIELDS))
    db_session.add(
        Workflow(
            id="wf-2",
            name="remediated-flow",
            description=None,
            on_failure_workflow_id=None,
            on_call_agent_id=None,
        )
    )
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        WORKFLOW_SPEC,
        "wf-2",
        {"node-b": 1},
        "node-b",
        {
            "name": "remediated-flow",
            "description": None,
            "published": False,
            "on_failure_workflow_id": "wf-fallback",
            "on_call_agent_id": "agent-oncall",
        },
    )
    assert result.applied is True

    workflow = await db_session.get(Workflow, "wf-2")
    assert workflow is not None
    assert workflow.on_failure_workflow_id == "wf-fallback"
    assert workflow.on_call_agent_id == "agent-oncall"


async def test_apply_remote_workflow_node_change_syncs_child_workflow_id(
    db_session: AsyncSession,
) -> None:
    """#316: `child_workflow_id` (#85/#201) has the same FK shape as
    `agent_id`, which is already synced, but was never added to
    WORKFLOW_NODE_SPEC -- a nested-workflow node arrived on a peer with
    child_workflow_id=None, so workflows/engine.py's
    `_execute_workflow_node` had nothing to invoke."""
    db_session.add(Workflow(id="wf-parent", name="parent-flow", description=None))
    db_session.add(Workflow(id="wf-child", name="child-flow", description=None))
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        WORKFLOW_NODE_SPEC,
        "node-1",
        {"node-b": 1},
        "node-b",
        {
            "workflow_id": "wf-parent",
            "name": "nested",
            "node_type": "workflow",
            "agent_id": None,
            "child_workflow_id": "wf-child",
            "config_json": None,
            "retry_max_attempts": 0,
            "retry_backoff_seconds": 5,
            "position_x": None,
            "position_y": None,
        },
    )
    assert result.applied is True

    node = await db_session.get(WorkflowNode, "node-1")
    assert node is not None
    assert node.child_workflow_id == "wf-child"


async def test_apply_remote_delete_removes_local_entity(db_session: AsyncSession) -> None:
    """#238's baseline case: a clean, non-conflicting tombstone actually
    deletes the local row and records the merged clock, matching
    apply_remote_change's own REMOTE_NEWER branch."""
    db_session.add(Agent(id="agent-1", name="Local", **_AGENT_FIELDS))
    await db_session.commit()

    result = await apply_remote_delete(db_session, "agent", "agent-1", {"node-b": 1}, "node-b")

    assert result.applied is True
    assert result.conflict is False
    assert await db_session.get(Agent, "agent-1") is None


async def test_apply_remote_delete_ignores_stale(db_session: AsyncSession) -> None:
    db_session.add(Agent(id="agent-1", name="Local", **_AGENT_FIELDS))
    await db_session.commit()
    await record_local_change(db_session, "agent", "agent-1", "node-a")  # local at {node-a: 1}

    result = await apply_remote_delete(db_session, "agent", "agent-1", {"node-a": 0}, "node-b")

    assert result.applied is False
    assert result.conflict is False
    assert await db_session.get(Agent, "agent-1") is not None  # untouched


async def test_apply_remote_delete_of_unknown_entity_is_a_noop(db_session: AsyncSession) -> None:
    """A tombstone for an entity this node never had (already deleted
    everywhere, or simply never synced here) has no row to remove but must
    still record the vector clock -- otherwise a later stale create for
    the same entity_id would be judged REMOTE_NEWER and resurrect it."""
    result = await apply_remote_delete(
        db_session, "agent", "never-existed", {"node-b": 1}, "node-b"
    )
    assert result.applied is True
    assert await db_session.get(Agent, "never-existed") is None


async def test_apply_remote_delete_vs_concurrent_edit_does_not_resurrect(
    db_session: AsyncSession,
) -> None:
    """The exact scenario #238 asks for a test of: delete on node A, edit
    on node B, neither having seen the other's change yet. From A's point
    of view (this test), B's edit must not resurrect the agent A already
    deleted -- it's recorded as a conflict instead, leaving A's deleted
    state alone."""
    db_session.add(Agent(id="agent-1", name="Local", **_AGENT_FIELDS))
    await db_session.commit()
    await record_local_change(db_session, "agent", "agent-1", "node-a")  # created at {node-a: 1}

    # A deletes -- bumps its own clock component to {node-a: 2} and removes
    # the row, exactly like publish_tombstone's real call to
    # record_local_change followed by apply_remote_delete's own commit
    # would on the *other* side of a real publish.
    delete_result = await apply_remote_delete(
        db_session, "agent", "agent-1", {"node-a": 2}, "node-a"
    )
    assert delete_result.applied is True
    assert await db_session.get(Agent, "agent-1") is None

    # B's edit arrives: B built it from the pre-delete state ({node-a: 1}),
    # bumped its own component -- {node-a: 1, node-b: 1}. Neither vector
    # dominates the other (A is ahead on its own component, B is ahead on
    # its own) -- a genuine concurrent modify/delete.
    edit_result = await apply_remote_change(
        db_session,
        AGENT_SPEC,
        "agent-1",
        {"node-a": 1, "node-b": 1},
        "node-b",
        {"name": "Edited on B", **_AGENT_FIELDS},
    )

    assert edit_result.applied is False
    assert edit_result.conflict is True
    assert await db_session.get(Agent, "agent-1") is None  # still deleted, not resurrected

    conflicts = list((await db_session.execute(select(SyncConflict))).scalars().all())
    assert len(conflicts) == 1
    assert conflicts[0].entity_id == "agent-1"
    assert conflicts[0].remote_node_id == "node-b"
    assert json.loads(conflicts[0].remote_snapshot)["name"] == "Edited on B"


async def test_apply_remote_delete_of_team_unassigns_its_channels(
    db_session: AsyncSession,
) -> None:
    """#250 fixed this exact IntegrityError for the local delete_team route
    (Channel.team_id has no ondelete, unlike every other synced FK) -- a
    remotely-applied team delete needs the same pre-cleanup or this would
    raise instead of deleting."""
    db_session.add(Team(id="team-1", name="Eng"))
    await db_session.commit()
    db_session.add(Channel(id="chan-1", name="eng-chan", team_id="team-1"))
    await db_session.commit()

    result = await apply_remote_delete(db_session, "team", "team-1", {"node-b": 1}, "node-b")

    assert result.applied is True
    assert await db_session.get(Team, "team-1") is None
    channel = await db_session.get(Channel, "chan-1")
    assert channel is not None
    assert channel.team_id is None


async def test_handle_incoming_state_change_applies_a_tombstone(db_session: AsyncSession) -> None:
    db_session.add(Agent(id="agent-1", name="Local", **_AGENT_FIELDS))
    await db_session.commit()

    await handle_incoming_state_change(
        "agent", "agent-1", {"node-b": 1}, "node-b", {TOMBSTONE_FIELD: True}
    )

    assert await db_session.get(Agent, "agent-1") is None


async def test_apply_remote_mcp_server_change_does_not_sync_connection_status(
    db_session: AsyncSession,
) -> None:
    """MCPServer.connected/last_connected_at are per-node status, not
    synced fields (see apply.py's module docstring) -- a freshly-applied
    remote server row must come in disconnected regardless of what the
    sender's own connection state was."""
    result = await apply_remote_change(
        db_session,
        MCP_SERVER_SPEC,
        "mcp-1",
        {"node-b": 1},
        "node-b",
        {"name": "Filesystem tools", "url": "http://127.0.0.1:9999/mcp"},
    )
    assert result.applied is True
    server = await db_session.get(MCPServer, "mcp-1")
    assert server is not None
    assert server.name == "Filesystem tools"
    assert server.connected is False
    assert server.last_connected_at is None


async def test_apply_remote_workspace_setting_change_creates_setting(
    db_session: AsyncSession,
) -> None:
    """WorkspaceSetting's primary key is `key`, not `id` like every other
    synced entity -- this is the regression test for EntitySpec.pk_fields,
    not just a routine "does sync work" check."""
    result = await apply_remote_change(
        db_session,
        WORKSPACE_SETTING_SPEC,
        "guard.turn_limit",
        {"node-b": 1},
        "node-b",
        {"value": "15"},
    )
    assert result.applied is True

    setting = await db_session.get(WorkspaceSetting, "guard.turn_limit")
    assert setting is not None
    assert setting.value == "15"


async def test_apply_remote_workspace_setting_change_updates_existing(
    db_session: AsyncSession,
) -> None:
    db_session.add(WorkspaceSetting(key="guard.turn_limit", value="10"))
    await db_session.commit()
    await record_local_change(db_session, "workspace_setting", "guard.turn_limit", "node-a")

    result = await apply_remote_change(
        db_session,
        WORKSPACE_SETTING_SPEC,
        "guard.turn_limit",
        # Must dominate local's {node-a: 1} to apply cleanly rather than
        # being judged concurrent.
        {"node-a": 1, "node-b": 1},
        "node-b",
        {"value": "20"},
    )
    assert result.applied is True
    setting = await db_session.get(WorkspaceSetting, "guard.turn_limit")
    assert setting is not None
    assert setting.value == "20"


async def test_apply_remote_agent_peer_preference_creates_row(db_session: AsyncSession) -> None:
    """Like WorkspaceSetting, AgentPeerPreference's primary key is
    agent_id, not id -- another EntitySpec.pk_fields regression case, plus
    coverage that issue #10's new entity type is actually wired into
    _DISPATCH (handle_incoming_state_change), not just apply_remote_change
    directly."""
    db_session.add(Agent(id="agent-1", name="Remote Pref Agent", **_AGENT_FIELDS))
    await db_session.commit()

    await handle_incoming_state_change(
        "agent_peer_preference", "agent-1", {"node-b": 1}, "node-b", {"capability_tag": "gpu"}
    )

    async with session_scope() as db:
        pref = await db.get(AgentPeerPreference, "agent-1")
        assert pref is not None
        assert pref.capability_tag == "gpu"


async def test_apply_remote_agent_peer_preference_updates_existing(
    db_session: AsyncSession,
) -> None:
    db_session.add(Agent(id="agent-1", name="Pref Agent", **_AGENT_FIELDS))
    db_session.add(AgentPeerPreference(agent_id="agent-1", capability_tag="cpu-heavy"))
    await db_session.commit()
    await record_local_change(db_session, "agent_peer_preference", "agent-1", "node-a")

    result = await apply_remote_change(
        db_session,
        AGENT_PEER_PREFERENCE_SPEC,
        "agent-1",
        {"node-a": 1, "node-b": 1},
        "node-b",
        {"capability_tag": "gpu"},
    )
    assert result.applied is True

    pref = await db_session.get(AgentPeerPreference, "agent-1")
    assert pref is not None
    assert pref.capability_tag == "gpu"


async def test_apply_remote_agent_peer_preference_detects_conflict(
    db_session: AsyncSession,
) -> None:
    db_session.add(Agent(id="agent-1", name="Pref Agent", **_AGENT_FIELDS))
    db_session.add(AgentPeerPreference(agent_id="agent-1", capability_tag="cpu-heavy"))
    await db_session.commit()
    await record_local_change(db_session, "agent_peer_preference", "agent-1", "node-a")

    result = await apply_remote_change(
        db_session,
        AGENT_PEER_PREFERENCE_SPEC,
        "agent-1",
        {"node-b": 1},  # concurrent with local's {node-a: 1} -- neither dominates
        "node-b",
        {"capability_tag": "gpu"},
    )
    assert result.applied is False
    assert result.conflict is True

    pref = await db_session.get(AgentPeerPreference, "agent-1")
    assert pref is not None
    assert pref.capability_tag == "cpu-heavy"  # untouched


async def test_apply_remote_delete_removes_agent_peer_preference(
    db_session: AsyncSession,
) -> None:
    """#311: the receiving side already dispatches tombstones generically
    for every type in _ALL_SPECS, including agent_peer_preference (pk_fields
    ('agent_id',), not ('id',) -- same EntitySpec.pk_fields case as the
    create/update tests above). This just confirms a tombstone for this
    type actually removes the row, the way it already does for
    'agent'/'team'."""
    db_session.add(Agent(id="agent-1", name="Pref Agent", **_AGENT_FIELDS))
    db_session.add(AgentPeerPreference(agent_id="agent-1", capability_tag="gpu"))
    await db_session.commit()

    result = await apply_remote_delete(
        db_session, "agent_peer_preference", "agent-1", {"node-b": 1}, "node-b"
    )

    assert result.applied is True
    assert result.conflict is False
    assert await db_session.get(AgentPeerPreference, "agent-1") is None


async def test_apply_remote_delete_of_agent_peer_preference_vs_concurrent_edit_does_not_resurrect(
    db_session: AsyncSession,
) -> None:
    """Mirrors test_apply_remote_delete_vs_concurrent_edit_does_not_resurrect
    for agent_peer_preference: a peer's not-yet-seen edit built from the
    pre-clear state must not recreate the row this node already cleared --
    the whole point of #311's tombstone. Without it, the peer's own next
    edit would arrive as a plain REMOTE_NEWER update and resurrect the
    withdrawn preference (the exact failure mode the issue describes)."""
    db_session.add(Agent(id="agent-1", name="Pref Agent", **_AGENT_FIELDS))
    db_session.add(AgentPeerPreference(agent_id="agent-1", capability_tag="gpu"))
    await db_session.commit()
    await record_local_change(db_session, "agent_peer_preference", "agent-1", "node-a")

    delete_result = await apply_remote_delete(
        db_session, "agent_peer_preference", "agent-1", {"node-a": 2}, "node-a"
    )
    assert delete_result.applied is True
    assert await db_session.get(AgentPeerPreference, "agent-1") is None

    edit_result = await apply_remote_change(
        db_session,
        AGENT_PEER_PREFERENCE_SPEC,
        "agent-1",
        {"node-a": 1, "node-b": 1},
        "node-b",
        {"capability_tag": "cpu-heavy"},
    )

    assert edit_result.applied is False
    assert edit_result.conflict is True
    assert await db_session.get(AgentPeerPreference, "agent-1") is None  # still cleared


async def test_handle_incoming_agent_change_resyncs_agentos_registry(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the sync_agents()-after-remote-apply fix
    (sync/apply.py's handle_incoming_state_change): before this fix, a
    node that only ever *received* an Agent row via sync had the DB row
    but no matching in-process AgentOS registration, so run_agent() would
    raise "not registered with AgentOS" -- issue #10's remote dispatch
    depends on this being fixed. Asserts sync_agents() gets called at all
    (not that the agent successfully joins AgentOS's registry, which also
    depends on a resolvable provider -- unrelated to the bug this guards
    against, and AgentOS.agents is skip-on-failure by design, per
    NFR-2.4)."""
    calls: list[AsyncSession] = []

    async def fake_sync_agents(db: AsyncSession) -> None:
        calls.append(db)

    monkeypatch.setattr("rivulets.sync.apply.sync_agents", fake_sync_agents)

    await handle_incoming_state_change(
        "agent",
        "agent-remote-1",
        {"node-b": 1},
        "node-b",
        {"name": "Remotely Synced Agent", **_AGENT_FIELDS},
    )

    assert len(calls) == 1


async def test_handle_incoming_tool_change_resyncs_agentos_registry(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#362 (P1 leftover of #317): custom tool source is loaded at agent
    *build* time (agentos/tool_resolution.py), so a synced source edit
    that doesn't trigger sync_agents() leaves every already-registered
    agent on this node executing the old function from memory. Same
    call-counting shape as the agent test above."""
    calls: list[AsyncSession] = []

    async def fake_sync_agents(db: AsyncSession) -> None:
        calls.append(db)

    monkeypatch.setattr("rivulets.sync.apply.sync_agents", fake_sync_agents)

    await handle_incoming_state_change(
        "tool",
        "tool-remote-1",
        {"node-b": 1},
        "node-b",
        {
            "name": "add_numbers",
            "description": "Adds two numbers.",
            "source_code": "def add_numbers(a, b):\n    return a + b\n",
        },
    )

    assert len(calls) == 1


async def test_handle_incoming_tool_tombstone_resyncs_agentos_registry(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#362's delete half of the same gap: a tool tombstone used to remove
    only the DB row, leaving the agent's cached in-memory function (and
    the .py on disk -- covered by the apply_remote_delete test below)."""
    calls: list[AsyncSession] = []

    async def fake_sync_agents(db: AsyncSession) -> None:
        calls.append(db)

    monkeypatch.setattr("rivulets.sync.apply.sync_agents", fake_sync_agents)

    db_session.add(
        Tool(
            id="tool-1",
            name="add_numbers",
            description="Doomed.",
            tool_type="custom",
            source_path=str(get_settings().tools_dir / "tool-1.py"),
        )
    )
    await db_session.commit()

    await handle_incoming_state_change(
        "tool", "tool-1", {"node-b": 1}, "node-b", {TOMBSTONE_FIELD: True}
    )

    assert await db_session.get(Tool, "tool-1") is None
    assert len(calls) == 1


async def test_apply_remote_delete_of_custom_tool_unlinks_source_file(
    db_session: AsyncSession,
) -> None:
    """#362: the executable source (which is exactly where operators bake
    integration secrets -- see api/tools.py's list_tool_versions) must not
    outlive the Tool row on a synced delete."""
    source_path = get_settings().tools_dir / "tool-1.py"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("def add_numbers(a, b):\n    return a + b\n")
    db_session.add(
        Tool(
            id="tool-1",
            name="add_numbers",
            description="Doomed.",
            tool_type="custom",
            source_path=str(source_path),
        )
    )
    await db_session.commit()

    result = await apply_remote_delete(db_session, "tool", "tool-1", {"node-b": 1}, "node-b")

    assert result.applied is True
    assert await db_session.get(Tool, "tool-1") is None
    assert not source_path.exists()


async def test_apply_remote_rivulet_change_creates_rivulet(db_session: AsyncSession) -> None:
    db_session.add(Channel(id="chan-1", name="general"))
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        RIVULET_SPEC,
        "rivulet-1",
        {"node-b": 1},
        "node-b",
        {"channel_id": "chan-1", "title": "Hello", "status": "active", "created_by": "human"},
    )
    assert result.applied is True

    rivulet = await db_session.get(Rivulet, "rivulet-1")
    assert rivulet is not None
    assert rivulet.channel_id == "chan-1"
    assert rivulet.title == "Hello"
    assert rivulet.agentos_session_id is None  # never synced


async def test_apply_remote_rivulet_change_skips_when_channel_missing(
    db_session: AsyncSession,
) -> None:
    """Regression test for the FK-ordering hazard apply.py's module
    docstring describes: a rivulet arriving before its channel has synced
    must be dropped cleanly (IntegrityError caught), not crash the
    sync-message handler."""
    result = await apply_remote_change(
        db_session,
        RIVULET_SPEC,
        "rivulet-1",
        {"node-b": 1},
        "node-b",
        {
            "channel_id": "does-not-exist",
            "title": "Hello",
            "status": "active",
            "created_by": "human",
        },
    )
    assert result.applied is False
    assert result.conflict is False
    assert await db_session.get(Rivulet, "rivulet-1") is None

    # The vector-clock bump must have rolled back with the failed commit --
    # otherwise a later, valid delivery of the same message would be
    # wrongly judged as already-seen (EQUAL) instead of fresh.
    retry = await apply_remote_change(
        db_session,
        RIVULET_SPEC,
        "rivulet-1",
        {"node-b": 1},
        "node-b",
        {
            "channel_id": "does-not-exist",
            "title": "Hello",
            "status": "active",
            "created_by": "human",
        },
    )
    assert retry.applied is False  # still fails (channel still missing) rather than no-op'ing


async def test_apply_remote_message_change_creates_message(db_session: AsyncSession) -> None:
    db_session.add(Channel(id="chan-1", name="general"))
    db_session.add(
        Rivulet(id="rivulet-1", channel_id="chan-1", created_by="human", status="active")
    )
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        MESSAGE_SPEC,
        "msg-1",
        {"node-b": 1},
        "node-b",
        {
            "rivulet_id": "rivulet-1",
            "sender_type": "human",
            "sender_id": None,
            "sender_name": "You",
            "content": "Hello from node B",
            "content_type": "text",
            "metadata_json": None,
        },
    )
    assert result.applied is True

    message = await db_session.get(Message, "msg-1")
    assert message is not None
    assert message.rivulet_id == "rivulet-1"
    assert message.content == "Hello from node B"


async def test_apply_remote_message_change_skips_when_rivulet_missing(
    db_session: AsyncSession,
) -> None:
    result = await apply_remote_change(
        db_session,
        MESSAGE_SPEC,
        "msg-1",
        {"node-b": 1},
        "node-b",
        {
            "rivulet_id": "does-not-exist",
            "sender_type": "human",
            "sender_id": None,
            "sender_name": "You",
            "content": "orphaned",
            "content_type": "text",
            "metadata_json": None,
        },
    )
    assert result.applied is False
    assert await db_session.get(Message, "msg-1") is None


@pytest.fixture
def not_running_sync_engine(tmp_path: Path) -> Iterator[None]:
    """apply_remote_file_change calls get_sync_engine() to (maybe) fetch
    content — it needs the singleton initialized (unlike db_session-only
    tests, which never go through create_app()'s init_sync_engine call),
    but not actually running, so fetch_file_content_from_known_sources's
    engine.running check cleanly skips without touching the network."""
    reset_sync_engine_for_testing()
    init_sync_engine(tmp_path / "sync")
    yield
    reset_sync_engine_for_testing()


async def test_apply_remote_file_change_creates_file_metadata(
    db_session: AsyncSession, not_running_sync_engine: None
) -> None:
    content_hash = "a" * 64
    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": 123,
            "message_id": None,
        },
    )
    assert result.applied is True

    file_row = await db_session.get(File, "file-1")
    assert file_row is not None
    assert file_row.content_hash == content_hash
    assert file_row.filename == "notes.txt"
    # local_path is always recomputed from this node's own files_dir, never
    # copied verbatim from the sender (mirrors Tool.source_path).
    assert file_row.local_path == str(get_settings().files_dir / content_hash[:2] / content_hash)


async def test_apply_remote_file_change_fetches_content_when_missing_locally(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """node-b is a LAN peer here, and sync.eager_files_lan defaults to
    True (unset in the DB), so this exercises the eager path end to end."""
    content = b"fetched over the wire"
    content_hash = hashlib.sha256(content).hexdigest()

    class _FakeEngine:
        running = True

        def peer_is_lan(self, peer_id: str) -> bool:
            assert peer_id == "node-b"
            return True

        async def list_peers(self) -> list[PeerInfo]:
            return []

        async def request_file(self, peer_id: str, requested_hash: str) -> bytes | None:
            assert peer_id == "node-b"
            assert requested_hash == content_hash
            return content

    reset_sync_engine_for_testing()
    monkeypatch.setattr("rivulets.sync.apply.get_sync_engine", lambda: _FakeEngine())
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)

    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": len(content),
            "message_id": None,
        },
    )
    assert result.applied is True

    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    assert local_path.read_bytes() == content

    file_row = await db_session.get(File, "file-1")
    assert file_row is not None
    assert file_row.synced_to_nodes is not None
    assert json.loads(file_row.synced_to_nodes) == ["node-b"]


async def test_apply_remote_file_change_eager_fetch_falls_back_to_connected_peers(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#391: the eager fetch previously asked only the gossip origin -- if
    that node couldn't serve the bytes (offline, or itself lazy), they
    stayed missing until someone happened to hit the download route. It
    must fall back to other connected peers the same way the on-demand
    path does."""
    content = b"held by a peer that eager-fetched earlier"
    content_hash = hashlib.sha256(content).hexdigest()

    asked: list[str] = []

    class _FakeEngine:
        running = True

        def peer_is_lan(self, peer_id: str) -> bool:
            assert peer_id == "node-b"
            return True

        async def list_peers(self) -> list[PeerInfo]:
            return [PeerInfo(peer_id="node-c", address="/ip4/10.0.0.9/tcp/4001", connected=True)]

        async def request_file(self, peer_id: str, requested_hash: str) -> bytes | None:
            asked.append(peer_id)
            assert requested_hash == content_hash
            if peer_id == "node-b":  # the origin can't serve the bytes
                return None
            return content

    reset_sync_engine_for_testing()
    monkeypatch.setattr("rivulets.sync.apply.get_sync_engine", lambda: _FakeEngine())
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)

    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": len(content),
            "message_id": None,
        },
    )
    assert result.applied is True

    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    assert local_path.read_bytes() == content
    # The just-recorded origin is asked first, then the connected peer.
    assert asked == ["node-b", "node-c"]


async def test_apply_remote_file_change_discards_content_that_does_not_match_hash(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#288: a mesh peer can lie -- it can ack a request_file() call for
    content_hash with any bytes it likes. Those bytes must be re-hashed
    and compared before ever landing on disk under a name (content_hash)
    that later readers (download_file, ingest_document) will trust
    without re-checking."""
    content_hash = hashlib.sha256(b"the real content").hexdigest()

    class _FakeEngine:
        running = True

        def peer_is_lan(self, peer_id: str) -> bool:
            return True

        async def list_peers(self) -> list[PeerInfo]:
            return []

        async def request_file(self, peer_id: str, requested_hash: str) -> bytes | None:
            return b"not the bytes that hash to content_hash"

    reset_sync_engine_for_testing()
    monkeypatch.setattr("rivulets.sync.apply.get_sync_engine", lambda: _FakeEngine())
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)

    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": 16,
            "message_id": None,
        },
    )
    assert result.applied is True

    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    assert not local_path.exists()


async def test_apply_remote_file_change_defers_fetch_for_wan_peer_by_default(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """issue #123: sync.eager_files_wan defaults to False, so a file whose
    metadata arrived from a non-LAN peer should NOT be fetched immediately
    -- only remembered as a known source for a later on-demand fetch."""
    content_hash = "d" * 64

    class _FakeEngine:
        running = True

        def peer_is_lan(self, peer_id: str) -> bool:
            return False

        async def request_file(self, peer_id: str, requested_hash: str) -> bytes | None:
            raise AssertionError("should not eager-fetch from a WAN peer by default")

    reset_sync_engine_for_testing()
    monkeypatch.setattr("rivulets.sync.apply.get_sync_engine", lambda: _FakeEngine())
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)

    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": 5,
            "message_id": None,
        },
    )
    assert result.applied is True

    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    assert not local_path.exists()

    file_row = await db_session.get(File, "file-1")
    assert file_row is not None
    assert file_row.synced_to_nodes is not None
    assert json.loads(file_row.synced_to_nodes) == ["node-b"]


async def test_apply_remote_file_change_fetches_wan_content_when_eager_wan_enabled(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = b"pushed over WAN"
    content_hash = hashlib.sha256(content).hexdigest()
    db_session.add(WorkspaceSetting(key="sync.eager_files_wan", value="true"))
    await db_session.commit()

    class _FakeEngine:
        running = True

        def peer_is_lan(self, peer_id: str) -> bool:
            return False

        async def list_peers(self) -> list[PeerInfo]:
            return []

        async def request_file(self, peer_id: str, requested_hash: str) -> bytes | None:
            return content

    reset_sync_engine_for_testing()
    monkeypatch.setattr("rivulets.sync.apply.get_sync_engine", lambda: _FakeEngine())
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)

    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": len(content),
            "message_id": None,
        },
    )
    assert result.applied is True
    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    assert local_path.read_bytes() == content


async def test_apply_remote_file_change_does_not_fetch_when_engine_not_running(
    db_session: AsyncSession,
    not_running_sync_engine: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)
    content_hash = "c" * 64

    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": 5,
            "message_id": None,
        },
    )
    assert result.applied is True
    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    assert not local_path.exists()  # engine wasn't running, so no fetch was attempted


async def test_apply_remote_file_change_rejects_non_hex_content_hash(
    db_session: AsyncSession, not_running_sync_engine: None
) -> None:
    """#239: a peer-supplied content_hash that isn't a valid SHA-256 hex
    digest (e.g. a path-traversal payload) must be rejected outright,
    never joined onto files_dir -- not applied, and no row written."""
    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": "../../etc/passwd",
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": 5,
            "message_id": None,
        },
    )
    assert result.applied is False
    assert result.conflict is False
    assert await db_session.get(File, "file-1") is None


async def test_apply_remote_file_change_rejects_absolute_content_hash(
    db_session: AsyncSession, not_running_sync_engine: None
) -> None:
    """Pathlib's `/` operator discards everything before an absolute
    right-hand segment (Path("/data/files") / "/etc/passwd" ->
    "/etc/passwd") -- a hash that merely looks path-like must be rejected
    the same way a "../"-style value is."""
    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": "/etc/passwd",
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": 5,
            "message_id": None,
        },
    )
    assert result.applied is False
    assert await db_session.get(File, "file-1") is None


async def test_apply_remote_tool_change_writes_source_code_to_disk(
    db_session: AsyncSession,
) -> None:
    # conftest.py points RIVULETS_WORKSPACE_DIR at an isolated temp dir
    # for the whole test session, so get_settings().tools_dir is already
    # safe to write into here without redirecting it further per-test.
    # #289: keyed off the tool's own id, not its name (two different tool
    # ids can no longer fight over the same `{name}.py` file).
    expected_path = get_settings().tools_dir / "tool-1.py"

    result = await apply_remote_tool_change(
        db_session,
        "tool-1",
        {"node-b": 1},
        "node-b",
        {
            "name": "add_numbers",
            "description": "Adds two numbers.",
            "source_code": "def add_numbers(a, b):\n    return a + b\n",
        },
    )
    assert result.applied is True

    tool = await db_session.get(Tool, "tool-1")
    assert tool is not None
    assert tool.tool_type == "custom"
    assert tool.source_path == str(expected_path)
    assert expected_path.read_text() == "def add_numbers(a, b):\n    return a + b\n"

    versions = list(
        (await db_session.execute(select(ToolVersion).where(ToolVersion.tool_id == "tool-1")))
        .scalars()
        .all()
    )
    assert len(versions) == 1
    assert versions[0].version == 1


async def test_apply_remote_tool_change_detects_conflict(db_session: AsyncSession) -> None:
    source_path = get_settings().tools_dir / "add_numbers.py"
    db_session.add(
        Tool(
            id="tool-1",
            name="add_numbers",
            description="Local version.",
            tool_type="custom",
            source_path=str(source_path),
        )
    )
    await db_session.commit()
    await record_local_change(db_session, "tool", "tool-1", "node-a")

    result = await apply_remote_tool_change(
        db_session,
        "tool-1",
        {"node-b": 1},
        "node-b",
        {"name": "add_numbers", "description": "Remote version.", "source_code": "..."},
    )
    assert result.applied is False
    assert result.conflict is True

    tool = await db_session.get(Tool, "tool-1")
    assert tool is not None
    assert tool.description == "Local version."  # untouched


async def test_apply_remote_tool_change_rejects_invalid_name(db_session: AsyncSession) -> None:
    """#239: payload['name'] is exec'd against as a Python identifier by
    _load_custom_tool's getattr(module, tool_row.name) -- a peer-supplied
    name that isn't a valid identifier (e.g. a path-traversal payload)
    must be rejected outright, never written to disk."""
    result = await apply_remote_tool_change(
        db_session,
        "tool-1",
        {"node-b": 1},
        "node-b",
        {
            "name": "../../etc/cron.d/evil",
            "description": "Malicious.",
            "source_code": "import os\nos.system('echo pwned')\n",
        },
    )
    assert result.applied is False
    assert result.conflict is False
    assert await db_session.get(Tool, "tool-1") is None
    assert not (get_settings().tools_dir.parent / "etc" / "cron.d" / "evil.py").exists()


async def test_apply_remote_tool_change_rejects_name_collision_with_different_id(
    db_session: AsyncSession,
) -> None:
    """#289: a peer publishing a *different* tool id under the same name as
    a tool already assigned locally must not be allowed to overwrite that
    tool's file -- otherwise agent_tool (never synced, see module
    docstring) keeps pointing at the same filename while its contents
    silently become the attacker's code."""
    local_path = get_settings().tools_dir / "tool-victim.py"
    db_session.add(
        Tool(
            id="tool-victim",
            name="send_report",
            description="Local, already assigned to an agent.",
            tool_type="custom",
            source_path=str(local_path),
        )
    )
    await db_session.commit()
    local_path.write_text("def send_report():\n    return 'legit'\n")

    result = await apply_remote_tool_change(
        db_session,
        "tool-attacker",
        {"node-b": 1},
        "node-b",
        {
            "name": "send_report",
            "description": "Malicious replacement.",
            "source_code": "import os\ndef send_report():\n    os.system('echo pwned')\n",
        },
    )
    assert result.applied is False
    assert result.conflict is False

    # Neither the victim's DB row nor its on-disk source were touched.
    assert await db_session.get(Tool, "tool-attacker") is None
    victim = await db_session.get(Tool, "tool-victim")
    assert victim is not None
    assert victim.source_path == str(local_path)
    assert local_path.read_text() == "def send_report():\n    return 'legit'\n"


async def _get_first_addr(engine: SyncEngine) -> str:
    host = engine._host  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert host is not None
    return str(host.get_addrs()[0])


async def test_two_engines_sync_agent_state_change(tmp_path: Path) -> None:
    # Same PSK (required to connect at all -- PNet gates the connection on
    # it), but deliberately *different* mDNS workspace fingerprints. This
    # test wants to exercise manual connect (FR-9.3) in isolation: giving
    # both engines the same fingerprint let real mDNS auto-connect
    # (engine.py's _on_peer_discovered) race the test's own explicit
    # connect() call, and the two concurrent connection attempts between
    # the same pair of hosts reliably broke gossipsub mesh formation
    # (discovered by this test actually failing after auto-connect was
    # added -- not a hypothetical).
    psk_hex = hashlib.sha256(b"rivulets-test-workspace").digest().hex()

    engine_a = SyncEngine(tmp_path / "a")
    engine_b = SyncEngine(tmp_path / "b")

    received = asyncio.Event()
    received_call: dict[str, object] = {}

    async def on_change(
        entity_type: str,
        entity_id: str,
        vector_clock: dict[str, int],
        origin_node_id: str,
        payload: dict[str, object],
    ) -> None:
        received_call["args"] = (entity_type, entity_id, vector_clock, origin_node_id, payload)
        received.set()

    engine_b.set_state_change_handler(on_change)

    await engine_a.start("rivulets-test-workspace-fingerprint-a", psk_hex)
    await engine_b.start("rivulets-test-workspace-fingerprint-b", psk_hex)
    try:
        addr = await engine_a._call_trio(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            _get_first_addr, engine_a
        )
        peer = await engine_b.connect(addr)
        assert peer.peer_id == engine_a.node_id

        await asyncio.sleep(2.0)  # let gossipsub GRAFT the mesh (heartbeat-driven)

        await engine_a.publish_state_change(
            "agent", "agent-xyz", {"name": "Synced Agent"}, {engine_a.node_id: 1}
        )

        await asyncio.wait_for(received.wait(), timeout=10)
    finally:
        await engine_a.stop()
        await engine_b.stop()

    entity_type, entity_id, vector_clock, origin_node_id, payload = received_call["args"]  # type: ignore[misc]
    assert entity_type == "agent"
    assert entity_id == "agent-xyz"
    assert vector_clock == {engine_a.node_id: 1}
    assert origin_node_id == engine_a.node_id
    assert payload == {"name": "Synced Agent"}


async def test_engine_tracks_inbound_connections_and_detects_disconnect(tmp_path: Path) -> None:
    """Two real bugs, both from _connected_peers only ever being written
    by this engine's own outbound connect() calls: (1) a peer connecting
    TO this host (inbound — B dials A) was never recorded at all, so
    list_peers() silently missed a real, active connection; (2) once
    recorded, an entry never left _connected_peers except via this
    engine's own explicit disconnect()/stop() — a network drop or the
    peer's process exiting left a phantom "connected: true" forever.
    _PeerConnectionNotifee fixes both."""
    psk_hex = hashlib.sha256(b"disconnect-test-workspace").digest().hex()

    engine_a = SyncEngine(tmp_path / "a")
    engine_b = SyncEngine(tmp_path / "b")

    await engine_a.start("disconnect-test-fingerprint-a", psk_hex)
    await engine_b.start("disconnect-test-fingerprint-b", psk_hex)
    try:
        addr = await engine_a._call_trio(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            _get_first_addr, engine_a
        )
        await engine_b.connect(addr)  # B dials A -- A never calls connect() itself
        await asyncio.sleep(1.0)

        peers_a = await engine_a.list_peers()
        assert len(peers_a) == 1, "inbound connection was never recorded"
        assert peers_a[0].peer_id == engine_b.node_id

        await engine_b.disconnect(engine_a.node_id)  # B's doing, not A's
        await asyncio.sleep(1.0)

        peers_a_after = await engine_a.list_peers()
        assert peers_a_after == [], "phantom peer: A never noticed B disconnecting"
    finally:
        await engine_a.stop()
        await engine_b.stop()


async def test_two_engines_sync_capability_broadcast(tmp_path: Path) -> None:
    """Issue #10: capability announcements ride the same _STATE_TOPIC as
    entity sync, but via the "node_capabilities" sentinel branch in
    _receive_loop -- not apply.py. This is the real end-to-end regression
    test for that branch, mirroring test_two_engines_sync_agent_state_change."""
    psk_hex = hashlib.sha256(b"capability-test-workspace").digest().hex()

    engine_a = SyncEngine(tmp_path / "a")
    engine_b = SyncEngine(tmp_path / "b")

    await engine_a.start("capability-test-fingerprint-a", psk_hex)
    await engine_b.start("capability-test-fingerprint-b", psk_hex)
    try:
        addr = await engine_a._call_trio(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            _get_first_addr, engine_a
        )
        await engine_b.connect(addr)
        await asyncio.sleep(2.0)  # let gossipsub GRAFT the mesh (heartbeat-driven)

        await engine_a.publish_capabilities(["gpu", "cpu-heavy"])

        for _ in range(50):
            capabilities = await engine_b.list_peer_capabilities()
            if engine_a.node_id in capabilities:
                break
            await asyncio.sleep(0.2)
        else:
            pytest.fail("engine_b never received engine_a's capability broadcast")

        assert capabilities[engine_a.node_id] == ["gpu", "cpu-heavy"]

        # Disconnecting drops the cached entry -- a peer going offline must
        # not still count toward remote-dispatch routing.
        await engine_b.disconnect(engine_a.node_id)
        await asyncio.sleep(1.0)
        assert engine_a.node_id not in await engine_b.list_peer_capabilities()
    finally:
        await engine_a.stop()
        await engine_b.stop()


# ---------------------------------------------------------------------------
# #101: bully-style coordinator election. Short heartbeat_interval/timeout
# overrides (vs. engine.py's 5s/15s production defaults) keep these real
# two/three-engine tests fast without changing the algorithm under test.
# ---------------------------------------------------------------------------

_TEST_HEARTBEAT_INTERVAL = 0.2
_TEST_TIMEOUT = 0.6


async def _poll_until(predicate: Any, *, attempts: int = 50, interval: float = 0.2) -> None:
    for _ in range(attempts):
        if await predicate():
            return
        await asyncio.sleep(interval)
    pytest.fail("condition never became true")


async def test_single_engine_elects_self_as_coordinator(tmp_path: Path) -> None:
    """No quorum threshold: a lone peer with no visible peers becomes its
    own coordinator on the very first tick, term 1."""
    engine = SyncEngine(
        tmp_path,
        coordinator_heartbeat_interval=_TEST_HEARTBEAT_INTERVAL,
        coordinator_timeout=_TEST_TIMEOUT,
    )
    await engine.start("solo-coordinator-fingerprint", hashlib.sha256(b"solo").digest().hex())
    try:
        await asyncio.sleep(_TEST_HEARTBEAT_INTERVAL * 3)
        status = await engine.get_coordinator_status()
        assert status.is_self is True
        assert status.coordinator_id == engine.node_id
        assert status.term == 1
    finally:
        await engine.stop()


async def test_own_addresses_populated_after_start(tmp_path: Path) -> None:
    """Issue #132: own_addresses gives the user something copyable for
    manual cross-network pairing instead of requiring them to hand-build
    a multiaddr. Each entry must be this node's own listen interface plus
    its own peer id, dialable as-is by another node's connect()."""
    engine = SyncEngine(tmp_path)
    assert engine.own_addresses == []  # nothing bound yet
    await engine.start("own-addresses-fingerprint", hashlib.sha256(b"addrs").digest().hex())
    try:
        addrs = engine.own_addresses
        assert len(addrs) > 0
        suffix = f"/p2p/{engine.node_id}"
        for addr in addrs:
            assert addr.endswith(suffix)
            # Issue #420: host.get_addrs() already includes /p2p/<id>;
            # appending it again produced /p2p/id/p2p/id.
            assert addr[: -len(suffix)].count("/p2p/") == 0
    finally:
        await engine.stop()
    assert engine.own_addresses == []  # reset on stop, same as other ephemeral state


async def test_two_engines_converge_on_same_coordinator(tmp_path: Path) -> None:
    """Both engines run on the same test machine (near-identical capability
    scores), so this deliberately doesn't assert *which* peer wins -- only
    that gossiped self-claims converge both sides onto one agreed
    coordinator, per outranks()'s deterministic tie-break."""
    psk_hex = hashlib.sha256(b"coordinator-election-workspace").digest().hex()
    engine_a = SyncEngine(
        tmp_path / "a",
        coordinator_heartbeat_interval=_TEST_HEARTBEAT_INTERVAL,
        coordinator_timeout=_TEST_TIMEOUT,
    )
    engine_b = SyncEngine(
        tmp_path / "b",
        coordinator_heartbeat_interval=_TEST_HEARTBEAT_INTERVAL,
        coordinator_timeout=_TEST_TIMEOUT,
    )
    await engine_a.start("coordinator-election-fingerprint-a", psk_hex)
    await engine_b.start("coordinator-election-fingerprint-b", psk_hex)
    try:
        addr = await engine_a._call_trio(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            _get_first_addr, engine_a
        )
        await engine_b.connect(addr)
        await asyncio.sleep(2.0)  # let gossipsub GRAFT the mesh (heartbeat-driven)

        async def _converged() -> bool:
            status_a = await engine_a.get_coordinator_status()
            status_b = await engine_b.get_coordinator_status()
            return (
                status_a.coordinator_id is not None
                and status_a.coordinator_id == status_b.coordinator_id
                and status_a.term == status_b.term
            )

        await _poll_until(_converged, attempts=30, interval=0.3)

        status_a = await engine_a.get_coordinator_status()
        status_b = await engine_b.get_coordinator_status()
        # Exactly one side believes it's the coordinator, never both, never
        # neither -- split-brain resolved.
        assert status_a.is_self != status_b.is_self
    finally:
        await engine_a.stop()
        await engine_b.stop()


async def test_coordinator_failover_on_disconnect(tmp_path: Path) -> None:
    """The surviving peer re-elects itself once the coordinator drops off
    the mesh -- TCP disconnect is treated as an immediate re-election
    trigger (engine.py's _on_peer_disconnected), not just the heartbeat
    timeout, so this converges within roughly one heartbeat interval."""
    psk_hex = hashlib.sha256(b"coordinator-failover-workspace").digest().hex()
    engine_a = SyncEngine(
        tmp_path / "a",
        coordinator_heartbeat_interval=_TEST_HEARTBEAT_INTERVAL,
        coordinator_timeout=_TEST_TIMEOUT,
    )
    engine_b = SyncEngine(
        tmp_path / "b",
        coordinator_heartbeat_interval=_TEST_HEARTBEAT_INTERVAL,
        coordinator_timeout=_TEST_TIMEOUT,
    )
    await engine_a.start("coordinator-failover-fingerprint-a", psk_hex)
    await engine_b.start("coordinator-failover-fingerprint-b", psk_hex)
    try:
        addr = await engine_a._call_trio(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            _get_first_addr, engine_a
        )
        await engine_b.connect(addr)
        await asyncio.sleep(2.0)

        async def _converged() -> bool:
            status_a = await engine_a.get_coordinator_status()
            status_b = await engine_b.get_coordinator_status()
            return (
                status_a.coordinator_id is not None
                and status_a.coordinator_id == status_b.coordinator_id
            )

        await _poll_until(_converged, attempts=30, interval=0.3)

        status_a = await engine_a.get_coordinator_status()
        coordinator, follower = (engine_a, engine_b) if status_a.is_self else (engine_b, engine_a)

        await follower.disconnect(coordinator.node_id)

        async def _follower_took_over() -> bool:
            status = await follower.get_coordinator_status()
            return status.is_self is True

        await _poll_until(_follower_took_over, attempts=30, interval=0.3)
    finally:
        await engine_a.stop()
        await engine_b.stop()


async def test_reclaim_coordinator_forces_takeover(tmp_path: Path) -> None:
    """The human-triggered override: a peer that is NOT currently
    coordinator (and, on this same-spec test setup, wouldn't necessarily
    win a natural re-election) forces itself into the role via a higher
    term, which every peer adopts unconditionally regardless of score."""
    psk_hex = hashlib.sha256(b"coordinator-reclaim-workspace").digest().hex()
    engine_a = SyncEngine(
        tmp_path / "a",
        coordinator_heartbeat_interval=_TEST_HEARTBEAT_INTERVAL,
        coordinator_timeout=_TEST_TIMEOUT,
    )
    engine_b = SyncEngine(
        tmp_path / "b",
        coordinator_heartbeat_interval=_TEST_HEARTBEAT_INTERVAL,
        coordinator_timeout=_TEST_TIMEOUT,
    )
    await engine_a.start("coordinator-reclaim-fingerprint-a", psk_hex)
    await engine_b.start("coordinator-reclaim-fingerprint-b", psk_hex)
    try:
        addr = await engine_a._call_trio(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            _get_first_addr, engine_a
        )
        await engine_b.connect(addr)
        await asyncio.sleep(2.0)

        async def _converged() -> bool:
            status_a = await engine_a.get_coordinator_status()
            status_b = await engine_b.get_coordinator_status()
            return (
                status_a.coordinator_id is not None
                and status_a.coordinator_id == status_b.coordinator_id
            )

        await _poll_until(_converged, attempts=30, interval=0.3)

        status_a = await engine_a.get_coordinator_status()
        _coordinator, follower = (engine_a, engine_b) if status_a.is_self else (engine_b, engine_a)
        term_before = status_a.term

        await follower.reclaim_coordinator()

        async def _both_agree_follower_won() -> bool:
            status_a2 = await engine_a.get_coordinator_status()
            status_b2 = await engine_b.get_coordinator_status()
            return (
                status_a2.coordinator_id == follower.node_id
                and status_b2.coordinator_id == follower.node_id
                and status_a2.term == status_b2.term
                and status_a2.term > term_before
            )

        await _poll_until(_both_agree_follower_won, attempts=30, interval=0.3)
    finally:
        await engine_a.stop()
        await engine_b.stop()


def test_sync_status_when_not_running(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/sync/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["running"] is False
    assert body["node_id"] is None
    assert body["peers"] == []
    assert body["own_addresses"] == []
    # #347: pending_changes is the real SyncPendingOutbound/-Inbound
    # backlog now, not a hardcoded 0 — and under this fixture the engine
    # never runs, so workspace bootstrap's own publishes are legitimately
    # queued. Exact count is bootstrap's business, not this test's;
    # test_sync_catchup.py asserts the counting itself.
    assert body["pending_changes"] >= 0


def test_sync_connect_when_not_running(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/sync/connect", json={"address": "/ip4/127.0.0.1/tcp/1/p2p/x"}, headers=auth_headers
    )
    assert response.status_code == 409


def test_sync_disconnect_when_not_running(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/sync/disconnect", json={"peer_id": "some-peer"}, headers=auth_headers
    )
    assert response.status_code == 409


def test_get_coordinator_when_not_running(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/sync/coordinator", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {
        "running": False,
        "node_id": None,
        "coordinator_id": None,
        "term": 0,
        "is_self": False,
        "self_score": 0.0,
        "peer_scores": {},
    }


def test_reclaim_coordinator_when_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post("/api/v1/sync/coordinator/reclaim", headers=auth_headers)
    assert response.status_code == 409


def test_get_capabilities_defaults_to_empty(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Redirect to an isolated tmp_path -- capabilities.json lives under the
    # shared session workspace dir otherwise, and this test must not
    # depend on (or be broken by) another test in this file having already
    # written one there.
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)
    response = client.get("/api/v1/sync/capabilities", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"capabilities": []}


def test_set_capabilities_persists_and_round_trips(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Engine isn't running through the client fixture (see its docstring),
    so this exercises save/load_capabilities' local file round-trip
    without needing publish_capabilities to actually broadcast anything."""
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)
    set_response = client.patch(
        "/api/v1/sync/capabilities",
        json={"capabilities": ["gpu", "cpu-heavy"]},
        headers=auth_headers,
    )
    assert set_response.status_code == 200, set_response.text
    assert set_response.json() == {"capabilities": ["gpu", "cpu-heavy"]}

    read_back = client.get("/api/v1/sync/capabilities", headers=auth_headers)
    assert read_back.json() == {"capabilities": ["gpu", "cpu-heavy"]}


def test_list_conflicts_empty(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/sync/conflicts", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_resolve_conflict_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/sync/conflicts/nonexistent/resolve", json={"keep": "local"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_resolve_conflict_invalid_keep(client: TestClient, auth_headers: dict[str, str]) -> None:
    # No real conflict to resolve, but validation should fire before the 404 lookup context matters.
    response = client.post(
        "/api/v1/sync/conflicts/nonexistent/resolve", json={"keep": "bogus"}, headers=auth_headers
    )
    assert response.status_code in (400, 404)


async def test_resolve_conflict_applies_remote_for_non_agent_entity(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Regression test: resolve_conflict used to only apply the remote
    snapshot for entity_type == 'agent' — every other synced entity type
    (channel, team, rivulet, ...) silently did nothing when a user picked
    "keep remote". Covered here with 'channel' since it goes through the
    plain generic apply path (get_entity_spec)."""
    create = client.post("/api/v1/channels", json={"name": "local-name-chan"}, headers=auth_headers)
    channel_id = create.json()["id"]

    async with session_scope() as db:
        await record_local_change(db, "channel", channel_id, "node-a")
        result = await apply_remote_change(
            db,
            CHANNEL_SPEC,
            channel_id,
            {"node-b": 1},
            "node-b",
            {
                "name": "remote-name-chan",
                "description": "from remote",
                "position": 0,
                "archived": False,
            },
        )
        assert result.conflict is True

    conflicts = client.get("/api/v1/sync/conflicts", headers=auth_headers).json()
    matching = [c for c in conflicts if c["entity_id"] == channel_id]
    assert len(matching) == 1
    conflict_id = matching[0]["id"]

    resolved = client.post(
        f"/api/v1/sync/conflicts/{conflict_id}/resolve",
        json={"keep": "remote"},
        headers=auth_headers,
    )
    assert resolved.status_code == 200, resolved.text

    updated = client.get(f"/api/v1/channels/{channel_id}", headers=auth_headers).json()
    assert updated["name"] == "remote-name-chan"
    assert updated["description"] == "from remote"


async def test_resolve_conflict_keep_remote_deletes_for_a_delete_conflict(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#238: a modify/delete conflict's remote_snapshot is {"deleted":
    True}, not a set of spec.synced_fields -- before this, "keep remote"
    on one of these silently did nothing (none of spec.synced_fields is
    ever present in {"deleted": True}), which looked like it worked but
    left the entity right where it was."""
    create = client.post(
        "/api/v1/agents",
        json={
            "name": "Conflict Delete Target",
            "description": "Exists only to be deleted concurrently.",
            "instructions": "N/A",
            "model": "openai:gpt-4",
        },
        headers=auth_headers,
    )
    agent_id = create.json()["id"]

    async with session_scope() as db:
        await record_local_change(db, "agent", agent_id, "node-a")
        result = await apply_remote_delete(db, "agent", agent_id, {"node-b": 1}, "node-b")
        assert result.conflict is True

    conflicts = client.get("/api/v1/sync/conflicts", headers=auth_headers).json()
    matching = [c for c in conflicts if c["entity_id"] == agent_id]
    assert len(matching) == 1
    assert matching[0]["remote_snapshot"] == {"deleted": True}
    conflict_id = matching[0]["id"]

    resolved = client.post(
        f"/api/v1/sync/conflicts/{conflict_id}/resolve",
        json={"keep": "remote"},
        headers=auth_headers,
    )
    assert resolved.status_code == 200, resolved.text

    assert client.get(f"/api/v1/agents/{agent_id}", headers=auth_headers).status_code == 404


async def test_resolve_conflict_keep_local_bumps_clock_and_republishes(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#325: "keep local" used to only flip `resolved` on this node -- both
    sides already merged to the identical vector clock the moment the
    conflict was detected, so a peer holding node-b's edit had no way to
    ever judge this node's copy as newer, and the mesh stayed split forever
    even though the UI reported the conflict resolved. Fixed by
    republishing: capture what actually gets handed to
    SyncEngine.publish_state_change and confirm it carries node-a's own
    unchanged field values under a clock that strictly dominates the clock
    a second node independently converged to for the same conflict."""
    create = client.post("/api/v1/channels", json={"name": "local-name-chan"}, headers=auth_headers)
    channel_id = create.json()["id"]

    async with session_scope() as db:
        await record_local_change(db, "channel", channel_id, "node-a")
        result = await apply_remote_change(
            db,
            CHANNEL_SPEC,
            channel_id,
            {"node-b": 1},
            "node-b",
            {
                "name": "remote-name-chan",
                "description": "from remote",
                "position": 0,
                "archived": False,
            },
        )
        assert result.conflict is True

        # What an independent second node converges to on detecting the
        # identical conflict itself -- both sides merge to the same clock.
        rows = (
            (
                await db.execute(
                    select(VectorClockTracker).where(
                        VectorClockTracker.entity_type == "channel",
                        VectorClockTracker.entity_id == channel_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        second_node_clock_before = {row.node_id: row.clock for row in rows}
    assert second_node_clock_before == {"node-a": 1, "node-b": 1}

    conflicts = client.get("/api/v1/sync/conflicts", headers=auth_headers).json()
    conflict_id = next(c["id"] for c in conflicts if c["entity_id"] == channel_id)

    published: list[tuple[str, str, dict[str, Any], dict[str, int]]] = []

    class _FakePublishEngine:
        running = True
        node_id = "node-a"

        async def publish_state_change(
            self,
            entity_type: str,
            entity_id: str,
            payload: dict[str, Any],
            vector_clock: dict[str, int],
        ) -> None:
            published.append((entity_type, entity_id, payload, vector_clock))

    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: _FakePublishEngine())

    resolved = client.post(
        f"/api/v1/sync/conflicts/{conflict_id}/resolve",
        json={"keep": "local"},
        headers=auth_headers,
    )
    assert resolved.status_code == 200, resolved.text

    assert len(published) == 1
    entity_type, entity_id, payload, vector_clock = published[0]
    assert entity_type == "channel"
    assert entity_id == channel_id
    assert payload["name"] == "local-name-chan"  # node-a's own value, untouched
    comparison = compare_vector_clocks(second_node_clock_before, vector_clock)
    assert comparison is ClockComparison.REMOTE_NEWER


async def test_resolve_conflict_keep_remote_recreates_entity_after_local_delete(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#325: "keep remote" used to require the local row to still exist --
    `if instance is not None:` guarded the whole apply block, so the other
    modify/delete race (this node deletes locally, a peer concurrently
    edits) recorded the conflict as resolved without ever recreating the
    row, silently dropping the surviving edit. Fixed by recreating from
    remote_snapshot the same way apply_remote_change does for an entity_id
    this node has never seen."""
    create = client.post(
        "/api/v1/agents",
        json={"name": "Doomed Locally", **_AGENT_FIELDS},
        headers=auth_headers,
    )
    agent_id = create.json()["id"]

    async with session_scope() as db:
        await record_local_change(db, "agent", agent_id, "node-a")
        instance = await db.get(Agent, agent_id)
        assert instance is not None
        await db.delete(instance)  # this node's own local delete
        await db.commit()

        result = await apply_remote_change(
            db,
            AGENT_SPEC,
            agent_id,
            {"node-b": 1},
            "node-b",
            {"name": "Renamed By Peer", **_AGENT_FIELDS},
        )
        assert result.conflict is True

    conflicts = client.get("/api/v1/sync/conflicts", headers=auth_headers).json()
    matching = [c for c in conflicts if c["entity_id"] == agent_id]
    assert len(matching) == 1
    assert matching[0]["remote_snapshot"]["name"] == "Renamed By Peer"
    conflict_id = matching[0]["id"]

    resolved = client.post(
        f"/api/v1/sync/conflicts/{conflict_id}/resolve",
        json={"keep": "remote"},
        headers=auth_headers,
    )
    assert resolved.status_code == 200, resolved.text

    recreated = client.get(f"/api/v1/agents/{agent_id}", headers=auth_headers)
    assert recreated.status_code == 200, recreated.text
    assert recreated.json()["name"] == "Renamed By Peer"


class _CapturingPublishEngine:
    """Stand-in for SyncEngine on the publish side -- collects what
    resolve_conflict hands to publish_state_change so tests can assert on
    the republished payload/clock (#325/#348)."""

    running = True
    node_id = "node-a"

    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict[str, Any], dict[str, int]]] = []

    async def publish_state_change(
        self,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        vector_clock: dict[str, int],
    ) -> None:
        self.published.append((entity_type, entity_id, payload, vector_clock))


async def _channel_conflict(client: TestClient, auth_headers: dict[str, str]) -> str:
    """Creates a channel locally as node-a, then records a concurrent
    node-b rename of it -- both sides merged to {node-a: 1, node-b: 1} the
    moment the conflict was detected, the exact starting state of #348's
    reproduction. Returns the channel id."""
    create = client.post("/api/v1/channels", json={"name": "local-name-chan"}, headers=auth_headers)
    channel_id: str = create.json()["id"]
    async with session_scope() as db:
        await record_local_change(db, "channel", channel_id, "node-a")
        result = await apply_remote_change(
            db,
            CHANNEL_SPEC,
            channel_id,
            {"node-b": 1},
            "node-b",
            {
                "name": "remote-name-chan",
                "description": "from remote",
                "position": 0,
                "archived": False,
            },
        )
        assert result.conflict is True
    return channel_id


async def test_resolve_conflict_keep_remote_bumps_clock_and_republishes(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#348 (the keep-remote half of #325): "keep remote" used to apply the
    snapshot locally and stop -- no clock bump, no publish -- so with both
    humans clicking "keep remote" the two nodes simply swapped states and
    stayed split (clocks already equal, later gossip a no-op). The chosen
    snapshot must be republished under a strictly-dominating clock exactly
    like "keep local" already does, carrying the resolution stamp."""
    channel_id = await _channel_conflict(client, auth_headers)
    async with session_scope() as db:
        merged_clock = await current_vector_clock(db, "channel", channel_id)
    assert merged_clock == {"node-a": 1, "node-b": 1}

    conflicts = client.get("/api/v1/sync/conflicts", headers=auth_headers).json()
    conflict_id = next(c["id"] for c in conflicts if c["entity_id"] == channel_id)

    engine = _CapturingPublishEngine()
    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: engine)

    resolved = client.post(
        f"/api/v1/sync/conflicts/{conflict_id}/resolve",
        json={"keep": "remote"},
        headers=auth_headers,
    )
    assert resolved.status_code == 200, resolved.text

    assert len(engine.published) == 1
    entity_type, entity_id, payload, vector_clock = engine.published[0]
    assert entity_type == "channel"
    assert entity_id == channel_id
    assert payload["name"] == "remote-name-chan"  # the *chosen* snapshot
    assert RESOLUTION_FIELD in payload
    # A peer still sitting at the merged clock judges this REMOTE_NEWER --
    # the mesh converges on the chosen snapshot instead of staying split.
    assert compare_vector_clocks(merged_clock, vector_clock) is ClockComparison.REMOTE_NEWER


async def test_resolve_conflict_keep_remote_delete_publishes_tombstone(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#348: keeping a remote *delete* must converge the mesh too -- the
    republished envelope is a tombstone (the row is gone locally, so
    there's no live state to publish) carrying the resolution stamp."""
    create = client.post(
        "/api/v1/agents",
        json={"name": "Doomed By Peer", **_AGENT_FIELDS},
        headers=auth_headers,
    )
    agent_id = create.json()["id"]

    async with session_scope() as db:
        await record_local_change(db, "agent", agent_id, "node-a")
        result = await apply_remote_delete(db, "agent", agent_id, {"node-b": 1}, "node-b")
        assert result.conflict is True
        merged_clock = await current_vector_clock(db, "agent", agent_id)

    conflicts = client.get("/api/v1/sync/conflicts", headers=auth_headers).json()
    conflict_id = next(c["id"] for c in conflicts if c["entity_id"] == agent_id)

    engine = _CapturingPublishEngine()
    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: engine)

    resolved = client.post(
        f"/api/v1/sync/conflicts/{conflict_id}/resolve",
        json={"keep": "remote"},
        headers=auth_headers,
    )
    assert resolved.status_code == 200, resolved.text

    assert client.get(f"/api/v1/agents/{agent_id}", headers=auth_headers).status_code == 404

    assert len(engine.published) == 1
    _, entity_id, payload, vector_clock = engine.published[0]
    assert entity_id == agent_id
    assert payload[TOMBSTONE_FIELD] is True
    assert RESOLUTION_FIELD in payload
    assert compare_vector_clocks(merged_clock, vector_clock) is ClockComparison.REMOTE_NEWER


async def test_concurrent_resolutions_converge_on_last_stamp(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#348: two nodes resolving the same conflict independently publish
    clocks that are concurrent *again* (each bumped only its own component
    past the merged clock) -- previously that recorded a fresh conflict
    pair on both sides ("keep local" twice) or silently swapped states
    ("keep remote" twice). The RESOLUTION_FIELD stamp settles it: the
    later (resolved_at, node_id) resolution wins deterministically on
    every node, and an older one is ignored without re-conflicting."""
    channel_id = await _channel_conflict(client, auth_headers)
    conflicts = client.get("/api/v1/sync/conflicts", headers=auth_headers).json()
    conflict_id = next(c["id"] for c in conflicts if c["entity_id"] == channel_id)

    engine = _CapturingPublishEngine()
    monkeypatch.setattr("rivulets.sync.publish.get_sync_engine", lambda: engine)

    resolved = client.post(
        f"/api/v1/sync/conflicts/{conflict_id}/resolve",
        json={"keep": "local"},
        headers=auth_headers,
    )
    assert resolved.status_code == 200, resolved.text
    # This node now sits at {node-a: 2, node-b: 1} -- exactly concurrent
    # with what node-b's own independent resolution publishes below.

    remote_fields = {
        "name": "node-b-final",
        "description": "node-b's pick",
        "position": 0,
        "archived": False,
    }
    async with session_scope() as db:
        # node-b resolved its copy of the conflict *later* (stamp sorts
        # after anything resolution_stamp() produced above) -- its choice
        # must win here even though the clocks can't order the two.
        later = await apply_remote_change(
            db,
            CHANNEL_SPEC,
            channel_id,
            {"node-a": 1, "node-b": 2},
            "node-b",
            {**remote_fields, RESOLUTION_FIELD: "2999-01-01T00:00:00.000000Z"},
        )
        assert later.applied is True
        assert later.conflict is False

        channel = await db.get(Channel, channel_id)
        assert channel is not None
        assert channel.name == "node-b-final"

        # A third, *older* resolution (e.g. a long-offline node's) must
        # lose to the one this node already reflects -- ignored, clocks
        # merged, and no new conflict recorded.
        older = await apply_remote_change(
            db,
            CHANNEL_SPEC,
            channel_id,
            {"node-a": 1, "node-b": 1, "node-c": 1},
            "node-c",
            {
                **remote_fields,
                "name": "stale-pick",
                RESOLUTION_FIELD: "2000-01-01T00:00:00.000000Z",
            },
        )
        assert older.applied is False
        assert older.conflict is False

        await db.refresh(channel)
        assert channel.name == "node-b-final"

        open_conflicts = (
            (
                await db.execute(
                    select(SyncConflict).where(
                        SyncConflict.entity_id == channel_id, SyncConflict.resolved.is_(False)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert open_conflicts == []
        register = await db.get(SyncResolution, ("channel", channel_id))
        assert register is not None
        assert (register.resolved_at, register.node_id) == (
            "2999-01-01T00:00:00.000000Z",
            "node-b",
        )


async def test_incoming_resolution_auto_resolves_open_conflict(
    db_session: AsyncSession,
) -> None:
    """#348: when a peer resolves first, its resolution envelope supersedes
    this node's still-open conflict for the same entity -- the local
    conflict's snapshots predate the mesh-level decision, and resolving it
    later could republish a stale snapshot right back over the settled
    state. Applying the resolution flips the local conflict to resolved."""
    db_session.add(Team(id="team-1", name="Local Name", description=""))
    await db_session.commit()
    await record_local_change(db_session, "team", "team-1", "node-a")

    conflicted = await apply_remote_change(
        db_session,
        TEAM_SPEC,
        "team-1",
        {"node-b": 1},
        "node-b",
        {"name": "Remote Name", "description": ""},
    )
    assert conflicted.conflict is True

    # node-b resolved on its side (keep local there) and republished under
    # a bumped clock -- REMOTE_NEWER here, carrying the resolution stamp.
    result = await apply_remote_change(
        db_session,
        TEAM_SPEC,
        "team-1",
        {"node-a": 1, "node-b": 2},
        "node-b",
        {"name": "Remote Name", "description": "", RESOLUTION_FIELD: "2999-01-01T00:00:00.000000Z"},
    )
    assert result.applied is True

    open_conflicts = (
        (
            await db_session.execute(
                select(SyncConflict).where(
                    SyncConflict.entity_id == "team-1", SyncConflict.resolved.is_(False)
                )
            )
        )
        .scalars()
        .all()
    )
    assert open_conflicts == []


async def test_concurrent_tombstone_resolution_applies_by_stamp(
    db_session: AsyncSession,
) -> None:
    """#348: apply_remote_delete's resolution path -- a concurrent
    tombstone carrying a superseding resolution stamp deletes the row
    instead of recording another conflict."""
    db_session.add(Team(id="team-2", name="Kept Locally", description=""))
    await db_session.commit()
    await record_local_change(db_session, "team", "team-2", "node-a")

    result = await apply_remote_delete(
        db_session,
        "team",
        "team-2",
        {"node-b": 1},
        "node-b",
        resolution="2999-01-01T00:00:00.000000Z",
    )
    assert result.applied is True
    assert result.conflict is False
    assert await db_session.get(Team, "team-2") is None


async def test_catchup_resolution_stamp_settles_late_joiner_and_keeps_resolver_node(
    db_session: AsyncSession,
) -> None:
    """#392: a catch-up snapshot envelope re-carries a settled conflict's
    resolution stamp with RESOLUTION_NODE_FIELD naming the original
    resolver -- the relaying sender's own node id must not leak into the
    register's tie-break pair, and a node still holding a concurrent
    pre-resolution clock must adopt the settled state instead of recording
    a fresh SyncConflict."""
    db_session.add(Team(id="team-3", name="Pre-Resolution Pick", description=""))
    await db_session.commit()
    await record_local_change(db_session, "team", "team-3", "node-a")

    result = await apply_remote_change(
        db_session,
        TEAM_SPEC,
        "team-3",
        {"node-b": 2},  # concurrent with local {node-a: 1}
        "node-sender",  # snapshot relayer, NOT the resolver
        {
            "name": "Settled Name",
            "description": "",
            RESOLUTION_FIELD: "2999-01-01T00:00:00.000000Z",
            RESOLUTION_NODE_FIELD: "node-resolver",
        },
    )

    assert result.applied is True
    assert result.conflict is False
    team = await db_session.get(Team, "team-3")
    assert team is not None
    assert team.name == "Settled Name"
    register = await db_session.get(SyncResolution, ("team", "team-3"))
    assert register is not None
    assert (register.resolved_at, register.node_id) == (
        "2999-01-01T00:00:00.000000Z",
        "node-resolver",
    )


async def test_already_reflected_resolution_stamp_records_fresh_conflict(
    db_session: AsyncSession,
) -> None:
    """#392: catch-up stamps every push for a once-resolved entity forever,
    so a CONCURRENT envelope carrying the exact (resolved_at, node_id) pair
    this node already reflects is *new* divergence created after settling,
    not the settled conflict re-arriving. Swallowing it as
    already-resolved (the pre-#392 behavior for any non-superseding stamp)
    would leave both sides silently diverged with no conflict recorded
    anywhere."""
    db_session.add(Team(id="team-4", name="Edited After Settling", description=""))
    await db_session.commit()
    await record_local_change(db_session, "team", "team-4", "node-a")
    await record_resolution(
        db_session, "team", "team-4", "2020-01-01T00:00:00.000000Z", "node-resolver"
    )
    await db_session.commit()

    result = await apply_remote_change(
        db_session,
        TEAM_SPEC,
        "team-4",
        {"node-b": 2},
        "node-b",
        {
            "name": "Their Post-Settle Edit",
            "description": "",
            RESOLUTION_FIELD: "2020-01-01T00:00:00.000000Z",
            RESOLUTION_NODE_FIELD: "node-resolver",
        },
    )

    assert result.applied is False
    assert result.conflict is True
    team = await db_session.get(Team, "team-4")
    assert team is not None
    assert team.name == "Edited After Settling"
    conflict = await db_session.scalar(
        select(SyncConflict).where(
            SyncConflict.entity_id == "team-4", SyncConflict.resolved.is_(False)
        )
    )
    assert conflict is not None
    # The stamp markers describe the envelope, not the entity -- they must
    # not leak into the snapshot a human sees / "keep remote" applies.
    assert RESOLUTION_FIELD not in json.loads(conflict.remote_snapshot)


async def test_record_resolution_never_regresses_the_register(
    db_session: AsyncSession,
) -> None:
    """#392: catch-up replays stamps forever, including from senders whose
    register lags the mesh -- an older (resolved_at, node_id) pair arriving
    on an otherwise-applicable envelope must not overwrite a later one."""
    await record_resolution(db_session, "team", "team-5", "2030-01-01T00:00:00.000000Z", "node-b")
    await db_session.commit()

    await record_resolution(db_session, "team", "team-5", "2020-01-01T00:00:00.000000Z", "node-a")
    await db_session.commit()

    register = await db_session.get(SyncResolution, ("team", "team-5"))
    assert register is not None
    assert (register.resolved_at, register.node_id) == ("2030-01-01T00:00:00.000000Z", "node-b")


async def test_tool_conflict_keep_remote_writes_source_code(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#348: tool conflict snapshots used to be metadata-only, so "keep
    remote" renamed the tool while this node kept executing its own
    pre-conflict source under that name. The snapshot now carries the
    remote source_code, and resolving toward it writes the chosen bytes to
    this node's {id}.py plus a ToolVersion for rollback history. #390
    (#362 leftover): writing the chosen bytes isn't enough on its own --
    custom tool source is loaded at agent *build* time, so the resolve
    must also rebuild AgentOS (sync_agents) or already-registered agents
    keep executing the pre-conflict function from memory."""
    sync_agents_calls: list[object] = []

    async def fake_sync_agents(db: object) -> None:
        sync_agents_calls.append(db)

    monkeypatch.setattr("rivulets.api.sync.sync_agents", fake_sync_agents)
    local_source = "def add_numbers(a, b):\n    return a + b\n"
    remote_source = "def add_numbers(a, b):\n    return a * b\n"
    source_path = get_settings().tools_dir / "tool-res-1.py"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(local_source)

    async with session_scope() as db:
        db.add(
            Tool(
                id="tool-res-1",
                name="add_numbers",
                description="Local version.",
                tool_type="custom",
                source_path=str(source_path),
            )
        )
        await db.commit()
        await record_local_change(db, "tool", "tool-res-1", "node-a")
        result = await apply_remote_tool_change(
            db,
            "tool-res-1",
            {"node-b": 1},
            "node-b",
            {
                "name": "add_numbers",
                "description": "Remote version.",
                "source_code": remote_source,
            },
        )
        assert result.conflict is True

    conflicts = client.get("/api/v1/sync/conflicts", headers=auth_headers).json()
    matching = [c for c in conflicts if c["entity_id"] == "tool-res-1"]
    assert len(matching) == 1
    assert matching[0]["remote_snapshot"]["source_code"] == remote_source

    resolved = client.post(
        f"/api/v1/sync/conflicts/{matching[0]['id']}/resolve",
        json={"keep": "remote"},
        headers=auth_headers,
    )
    assert resolved.status_code == 200, resolved.text

    assert source_path.read_text() == remote_source
    assert len(sync_agents_calls) == 1
    async with session_scope() as db:
        tool = await db.get(Tool, "tool-res-1")
        assert tool is not None
        assert tool.description == "Remote version."
        versions = (
            (await db.execute(select(ToolVersion).where(ToolVersion.tool_id == "tool-res-1")))
            .scalars()
            .all()
        )
        assert [v.source_code for v in versions] == [remote_source]


async def test_tool_conflict_keep_remote_delete_resyncs_agentos_registry(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#390's delete half (#362 leftover): "keep remote" on a tool
    modify/delete conflict deletes the row and unlinks the .py, but before
    the fix never rebuilt AgentOS, leaving the agent's cached in-memory
    function callable after the tool was gone. Keep-local, by contrast,
    changes nothing AgentOS can see and must NOT trigger a rebuild -- both
    halves asserted here via two conflicts on two tools."""
    sync_agents_calls: list[object] = []

    async def fake_sync_agents(db: object) -> None:
        sync_agents_calls.append(db)

    monkeypatch.setattr("rivulets.api.sync.sync_agents", fake_sync_agents)

    source = "def doomed_tool():\n    return 1\n"
    paths: dict[str, Path] = {}
    async with session_scope() as db:
        for tool_id in ("tool-del-res-1", "tool-del-res-2"):
            source_path = get_settings().tools_dir / f"{tool_id}.py"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(source)
            paths[tool_id] = source_path
            db.add(
                Tool(
                    id=tool_id,
                    name=tool_id.replace("-", "_"),
                    description="Deleted remotely, edited locally.",
                    tool_type="custom",
                    source_path=str(source_path),
                )
            )
        await db.commit()
        for tool_id in ("tool-del-res-1", "tool-del-res-2"):
            await record_local_change(db, "tool", tool_id, "node-a")
            result = await apply_remote_delete(db, "tool", tool_id, {"node-b": 1}, "node-b")
            assert result.conflict is True

    conflicts = client.get("/api/v1/sync/conflicts", headers=auth_headers).json()
    by_entity = {c["entity_id"]: c for c in conflicts if c["entity_id"] in paths}
    assert len(by_entity) == 2

    resolved = client.post(
        f"/api/v1/sync/conflicts/{by_entity['tool-del-res-1']['id']}/resolve",
        json={"keep": "remote"},
        headers=auth_headers,
    )
    assert resolved.status_code == 200, resolved.text
    assert len(sync_agents_calls) == 1
    assert not paths["tool-del-res-1"].exists()
    async with session_scope() as db:
        assert await db.get(Tool, "tool-del-res-1") is None

    resolved = client.post(
        f"/api/v1/sync/conflicts/{by_entity['tool-del-res-2']['id']}/resolve",
        json={"keep": "local"},
        headers=auth_headers,
    )
    assert resolved.status_code == 200, resolved.text
    assert len(sync_agents_calls) == 1  # keep-local did not rebuild


async def test_file_conflict_keep_remote_recomputes_path_and_records_source(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """#348: "keep remote" on a file conflict used to point content_hash at
    bytes this node never fetched, while local_path kept naming the *old*
    content's location. The path must follow the chosen hash, and the
    conflicting peer must be recorded as a known source so the bytes are
    actually obtainable later (api/files.py's on-demand fetch)."""
    local_hash = hashlib.sha256(b"local-bytes").hexdigest()
    remote_hash = hashlib.sha256(b"remote-bytes").hexdigest()

    async with session_scope() as db:
        db.add(
            File(
                id="file-res-1",
                content_hash=local_hash,
                filename="local.txt",
                mime_type="text/plain",
                size_bytes=11,
                local_path=str(local_path_for_content_hash(local_hash)),
            )
        )
        await db.commit()
        await record_local_change(db, "file", "file-res-1", "node-a")
        result = await apply_remote_file_change(
            db,
            "file-res-1",
            {"node-b": 1},
            "node-b",
            {
                "content_hash": remote_hash,
                "filename": "remote.txt",
                "mime_type": "text/plain",
                "size_bytes": 12,
                "message_id": None,
            },
        )
        assert result.conflict is True

    conflicts = client.get("/api/v1/sync/conflicts", headers=auth_headers).json()
    conflict_id = next(c["id"] for c in conflicts if c["entity_id"] == "file-res-1")

    resolved = client.post(
        f"/api/v1/sync/conflicts/{conflict_id}/resolve",
        json={"keep": "remote"},
        headers=auth_headers,
    )
    assert resolved.status_code == 200, resolved.text

    async with session_scope() as db:
        file_row = await db.get(File, "file-res-1")
        assert file_row is not None
        assert file_row.content_hash == remote_hash
        assert file_row.local_path == str(local_path_for_content_hash(remote_hash))
        # The engine isn't running here, so no eager fetch -- but node-b is
        # recorded as a known source for the deferred one.
        assert file_row.synced_to_nodes is not None
        assert json.loads(file_row.synced_to_nodes) == ["node-b"]


def test_agent_create_does_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """FR-9.5: creating an agent must succeed even though the (no-op'd,
    per conftest.py) sync engine never actually starts in tests — this is
    exactly the "offline" case _publish_agent_change guards against."""
    response = client.post(
        "/api/v1/agents",
        json={
            "name": "offline-agent",
            "description": "Created while sync is not running.",
            "instructions": "Be helpful.",
            "model": "openai:gpt-4",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text


def test_channel_create_does_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/channels", json={"name": "offline-channel"}, headers=auth_headers
    )
    assert response.status_code == 201, response.text


def test_team_create_does_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post("/api/v1/teams", json={"name": "Offline Team"}, headers=auth_headers)
    assert response.status_code == 201, response.text


def test_mcp_server_register_does_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_discover_tools(
        url: str,  # noqa: ARG001
        timeout_seconds: int = 10,  # noqa: ARG001
        headers: dict[str, str] | None = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> list[object]:
        return []

    monkeypatch.setattr("rivulets.api.mcp_servers.discover_tools", _fake_discover_tools)

    response = client.post(
        "/api/v1/mcp-servers",
        json={"name": "offline-server", "url": "http://127.0.0.1:9999/mcp"},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text


def test_tool_create_does_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/tools",
        json={"name": "offline_tool", "description": "A tool created while sync is not running."},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text


def test_rivulet_and_message_create_do_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    channel = client.post(
        "/api/v1/channels", json={"name": "offline-sync-channel"}, headers=auth_headers
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "hello while sync is offline"},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text
    rivulet_id = rivulet.json()["id"]

    message = client.post(
        f"/api/v1/rivulets/{rivulet_id}/messages",
        json={"content": "another message while sync is offline"},
        headers=auth_headers,
    )
    assert message.status_code == 201, message.text

    resumed = client.post(f"/api/v1/rivulets/{rivulet_id}/resume", headers=auth_headers)
    assert resumed.status_code == 200

    closed = client.delete(f"/api/v1/rivulets/{rivulet_id}", headers=auth_headers)
    assert closed.status_code == 204


def test_file_upload_and_attach_do_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    upload = client.post(
        "/api/v1/files/upload",
        files={"upload": ("notes.txt", b"hello file sync", "text/plain")},
        headers=auth_headers,
    )
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["file_id"]

    channel = client.post(
        "/api/v1/channels", json={"name": "offline-file-channel"}, headers=auth_headers
    )
    channel_id = channel.json()["id"]

    rivulet = client.post(
        f"/api/v1/channels/{channel_id}/rivulets",
        json={"content": "see attached", "files": [file_id]},
        headers=auth_headers,
    )
    assert rivulet.status_code == 201, rivulet.text


def test_settings_patch_does_not_fail_when_sync_engine_not_running(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.patch("/api/v1/settings", json={"guard.turn_limit": 15}, headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["guard.turn_limit"] == 15


# ---------------------------------------------------------------------------
# api/sync.py: sync_status/connect/disconnect's "engine actually running"
# branches. The `client` fixture no-ops SyncEngine.start()/stop() (see
# conftest.py), so engine.running is always False through it -- these branches
# are only reachable by swapping in a fake engine that reports running=True,
# the same pattern test_mcp_servers.py uses for MCPTools.
# ---------------------------------------------------------------------------


class _FakeRunningEngine:
    running = True
    node_id = "fake-node-id"
    own_addresses = ["/ip4/192.168.1.5/tcp/4001/p2p/fake-node-id"]

    def __init__(
        self,
        peers: list[PeerInfo] | None = None,
        connect_result: PeerInfo | Exception | None = None,
        disconnect_error: Exception | None = None,
        coordinator_status: CoordinatorStatus | None = None,
    ) -> None:
        self._peers = peers or []
        self._connect_result = connect_result
        self._disconnect_error = disconnect_error
        self._coordinator_status = coordinator_status or CoordinatorStatus(
            coordinator_id="fake-node-id", term=1, is_self=True, self_score=42.0, peer_scores={}
        )

    async def list_peers(self) -> list[PeerInfo]:
        return self._peers

    async def list_peer_capabilities(self) -> dict[str, list[str]]:
        return {}

    async def connect(self, address: str) -> PeerInfo:
        if isinstance(self._connect_result, Exception):
            raise self._connect_result
        assert self._connect_result is not None
        return self._connect_result

    async def disconnect(self, peer_id: str) -> None:  # noqa: ARG002
        if self._disconnect_error is not None:
            raise self._disconnect_error

    async def get_coordinator_status(self) -> CoordinatorStatus:
        return self._coordinator_status

    async def reclaim_coordinator(self) -> None:
        self._coordinator_status = CoordinatorStatus(
            coordinator_id=self.node_id,
            term=self._coordinator_status.term + 1,
            is_self=True,
            self_score=self._coordinator_status.self_score,
            peer_scores=self._coordinator_status.peer_scores,
        )


def test_sync_status_when_running_reports_peers(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRunningEngine(
        peers=[PeerInfo(peer_id="peer-1", address="/ip4/1.2.3.4/tcp/1", connected=True)]
    )
    monkeypatch.setattr("rivulets.api.sync.get_sync_engine", lambda: fake)

    response = client.get("/api/v1/sync/status", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["running"] is True
    assert body["node_id"] == "fake-node-id"
    assert body["own_addresses"] == [
        {"address": "/ip4/192.168.1.5/tcp/4001/p2p/fake-node-id", "scope": "network"}
    ]
    assert body["peers"] == [
        {
            "peer_id": "peer-1",
            "address": "/ip4/1.2.3.4/tcp/1",
            "connected": True,
            "capabilities": [],
        }
    ]


def test_sync_connect_when_running_returns_peer(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRunningEngine(
        connect_result=PeerInfo(peer_id="peer-2", address="/ip4/5.6.7.8/tcp/2", connected=True)
    )
    monkeypatch.setattr("rivulets.api.sync.get_sync_engine", lambda: fake)

    response = client.post(
        "/api/v1/sync/connect",
        json={"address": "/ip4/5.6.7.8/tcp/2/p2p/peer-2"},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "peer_id": "peer-2",
        "address": "/ip4/5.6.7.8/tcp/2",
        "connected": True,
        "capabilities": [],
    }


def test_sync_connect_when_running_wraps_failure_as_502(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRunningEngine(connect_result=RuntimeError("no route to host"))
    monkeypatch.setattr("rivulets.api.sync.get_sync_engine", lambda: fake)

    response = client.post(
        "/api/v1/sync/connect", json={"address": "/ip4/9.9.9.9/tcp/1/p2p/x"}, headers=auth_headers
    )

    assert response.status_code == 502
    assert "no route to host" in response.json()["detail"]


def test_sync_disconnect_when_running_succeeds(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRunningEngine()
    monkeypatch.setattr("rivulets.api.sync.get_sync_engine", lambda: fake)

    response = client.post(
        "/api/v1/sync/disconnect", json={"peer_id": "peer-2"}, headers=auth_headers
    )

    assert response.status_code == 204


def test_get_coordinator_when_running_reports_status(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRunningEngine(
        coordinator_status=CoordinatorStatus(
            coordinator_id="peer-9",
            term=3,
            is_self=False,
            self_score=12.5,
            peer_scores={"peer-9": 99.0},
        )
    )
    monkeypatch.setattr("rivulets.api.sync.get_sync_engine", lambda: fake)

    response = client.get("/api/v1/sync/coordinator", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "running": True,
        "node_id": "fake-node-id",
        "coordinator_id": "peer-9",
        "term": 3,
        "is_self": False,
        "self_score": 12.5,
        "peer_scores": {"peer-9": 99.0},
    }


def test_reclaim_coordinator_when_running_forces_self(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRunningEngine(
        coordinator_status=CoordinatorStatus(
            coordinator_id="peer-9", term=3, is_self=False, self_score=12.5, peer_scores={}
        )
    )
    monkeypatch.setattr("rivulets.api.sync.get_sync_engine", lambda: fake)

    response = client.post("/api/v1/sync/coordinator/reclaim", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["coordinator_id"] == "fake-node-id"
    assert body["is_self"] is True
    assert body["term"] == 4


async def test_resolve_conflict_rejects_invalid_keep_for_a_real_conflict(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Unlike test_resolve_conflict_invalid_keep (which hits a nonexistent
    conflict id, so it can't tell 400-before-404 apart from a plain 404),
    this creates a real conflict first so an invalid `keep` value is
    unambiguously exercising the 400 validation branch, not the 404 lookup."""
    create = client.post("/api/v1/channels", json={"name": "conflict-chan"}, headers=auth_headers)
    channel_id = create.json()["id"]

    async with session_scope() as db:
        await record_local_change(db, "channel", channel_id, "node-a")
        result = await apply_remote_change(
            db,
            CHANNEL_SPEC,
            channel_id,
            {"node-b": 1},
            "node-b",
            {"name": "remote", "description": None, "position": 0, "archived": False},
        )
        assert result.conflict is True

    conflicts = client.get("/api/v1/sync/conflicts", headers=auth_headers).json()
    conflict_id = next(c["id"] for c in conflicts if c["entity_id"] == channel_id)

    response = client.post(
        f"/api/v1/sync/conflicts/{conflict_id}/resolve",
        json={"keep": "bogus"},
        headers=auth_headers,
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# sync/apply.py: branches the HTTP-layer/db_session tests above don't reach --
# stale tool/file deliveries, file-level conflicts, the "peer doesn't have it
# either" fetch outcome, pending-inbound retry with an unexpected entity type,
# and handle_incoming_state_change (SyncEngine's real callback) end to end.
# ---------------------------------------------------------------------------


async def test_apply_remote_tool_change_ignores_stale(db_session: AsyncSession) -> None:
    await record_local_change(db_session, "tool", "tool-stale", "node-a")  # local at {node-a: 1}

    result = await apply_remote_tool_change(
        db_session,
        "tool-stale",
        {"node-a": 0},
        "node-b",
        {"name": "whatever", "description": "x", "source_code": "pass"},
    )

    assert result.applied is False
    assert result.conflict is False
    assert await db_session.get(Tool, "tool-stale") is None


async def test_apply_remote_file_change_ignores_stale(db_session: AsyncSession) -> None:
    await record_local_change(db_session, "file", "file-stale", "node-a")

    result = await apply_remote_file_change(
        db_session,
        "file-stale",
        {"node-a": 0},
        "node-b",
        {
            "content_hash": "d" * 64,
            "filename": "x.txt",
            "mime_type": "text/plain",
            "size_bytes": 1,
            "message_id": None,
        },
    )

    assert result.applied is False
    assert result.conflict is False
    assert await db_session.get(File, "file-stale") is None


async def test_apply_remote_file_change_detects_conflict(
    db_session: AsyncSession, not_running_sync_engine: None
) -> None:
    db_session.add(
        File(
            id="file-1",
            content_hash="e" * 64,
            filename="local.txt",
            mime_type="text/plain",
            size_bytes=1,
            local_path="not-a-real-path/local.txt",
        )
    )
    await db_session.commit()
    await record_local_change(db_session, "file", "file-1", "node-a")

    result = await apply_remote_file_change(
        db_session,
        "file-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": "f" * 64,
            "filename": "remote.txt",
            "mime_type": "text/plain",
            "size_bytes": 2,
            "message_id": None,
        },
    )

    assert result.applied is False
    assert result.conflict is True
    file_row = await db_session.get(File, "file-1")
    assert file_row is not None
    assert file_row.filename == "local.txt"  # untouched

    conflicts = list((await db_session.execute(select(SyncConflict))).scalars().all())
    assert len(conflicts) == 1
    assert conflicts[0].entity_type == "file"


async def test_apply_remote_file_change_logs_when_peer_also_lacks_content(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content_hash = "1" * 64

    class _FakeEngineNoContent:
        running = True

        def peer_is_lan(self, peer_id: str) -> bool:
            return True

        async def list_peers(self) -> list[PeerInfo]:
            return []

        async def request_file(self, peer_id: str, requested_hash: str) -> bytes | None:
            assert peer_id == "node-b"
            assert requested_hash == content_hash
            return None

    reset_sync_engine_for_testing()
    monkeypatch.setattr("rivulets.sync.apply.get_sync_engine", lambda: _FakeEngineNoContent())
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)

    result = await apply_remote_file_change(
        db_session,
        "file-2",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size_bytes": 5,
            "message_id": None,
        },
    )

    assert result.applied is True
    local_path = get_settings().files_dir / content_hash[:2] / content_hash
    assert not local_path.exists()


async def test_retry_pending_inbound_skips_unrecognized_entity_type(
    db_session: AsyncSession,
) -> None:
    """Tool/file never end up in SyncPendingInbound (module docstring: they
    have no FK-ordering hazard), but retry_pending_inbound must still cope
    defensively with an entity_type get_entity_spec doesn't dispatch on
    without raising -- it just clears the row and moves on."""
    db_session.add(
        SyncPendingInbound(
            entity_type="not-a-real-entity-type",
            entity_id="whatever",
            vector_clock_json="{}",
            origin_node_id="node-b",
            payload_json="{}",
        )
    )
    await db_session.commit()

    await retry_pending_inbound(db_session)

    remaining = list((await db_session.execute(select(SyncPendingInbound))).scalars().all())
    assert remaining == []


async def test_handle_incoming_state_change_applies_a_dispatch_entity(
    db_session: AsyncSession,
) -> None:
    await handle_incoming_state_change(
        "agent", "agent-remote-1", {"node-b": 1}, "node-b", {"name": "Remote", **_AGENT_FIELDS}
    )
    agent = await db_session.get(Agent, "agent-remote-1")
    assert agent is not None
    assert agent.name == "Remote"


async def test_handle_incoming_state_change_applies_a_tool(db_session: AsyncSession) -> None:
    await handle_incoming_state_change(
        "tool",
        "tool-remote-1",
        {"node-b": 1},
        "node-b",
        {"name": "remote_tool", "description": "d", "source_code": "def f():\n    pass\n"},
    )
    tool = await db_session.get(Tool, "tool-remote-1")
    assert tool is not None
    assert tool.name == "remote_tool"


async def test_handle_incoming_state_change_applies_a_file(
    db_session: AsyncSession, not_running_sync_engine: None
) -> None:
    content_hash = "2" * 64
    await handle_incoming_state_change(
        "file",
        "file-remote-1",
        {"node-b": 1},
        "node-b",
        {
            "content_hash": content_hash,
            "filename": "remote.txt",
            "mime_type": "text/plain",
            "size_bytes": 3,
            "message_id": None,
        },
    )
    file_row = await db_session.get(File, "file-remote-1")
    assert file_row is not None
    assert file_row.content_hash == content_hash


async def test_handle_incoming_state_change_drops_unsupported_entity_type(
    db_session: AsyncSession,
) -> None:
    # Must not raise, and must not create anything -- just logged and dropped.
    await handle_incoming_state_change(
        "not-a-real-entity-type", "whatever", {"node-b": 1}, "node-b", {}
    )


async def test_handle_incoming_state_change_records_a_conflict(db_session: AsyncSession) -> None:
    db_session.add(Agent(id="agent-conf", name="Local", **_AGENT_FIELDS))
    await db_session.commit()
    await record_local_change(db_session, "agent", "agent-conf", "node-a")

    await handle_incoming_state_change(
        "agent", "agent-conf", {"node-b": 1}, "node-b", {"name": "Remote", **_AGENT_FIELDS}
    )

    conflicts = list(
        (
            await db_session.execute(
                select(SyncConflict).where(SyncConflict.entity_id == "agent-conf")
            )
        )
        .scalars()
        .all()
    )
    assert len(conflicts) == 1
    agent = await db_session.get(Agent, "agent-conf")
    assert agent is not None
    assert agent.name == "Local"  # untouched -- the conflict wasn't auto-applied


async def test_handle_incoming_state_change_retries_pending_inbound_on_success(
    db_session: AsyncSession,
) -> None:
    """End-to-end regression for the FK-ordering hazard (module docstring):
    a rivulet arrives before its channel and gets queued; once the channel
    itself arrives via handle_incoming_state_change, the queued rivulet
    must be retried automatically, not left stranded."""
    queue_result = await apply_remote_change(
        db_session,
        RIVULET_SPEC,
        "rivulet-pending",
        {"node-b": 1},
        "node-b",
        {
            "channel_id": "chan-not-yet-synced",
            "title": "Queued",
            "status": "active",
            "created_by": "human",
        },
    )
    assert queue_result.applied is False
    from rivulets.sync.apply import _record_pending_inbound  # pyright: ignore[reportPrivateUsage]

    await _record_pending_inbound(
        db_session,
        "rivulet",
        "rivulet-pending",
        {"node-b": 1},
        "node-b",
        {
            "channel_id": "chan-not-yet-synced",
            "title": "Queued",
            "status": "active",
            "created_by": "human",
        },
    )

    await handle_incoming_state_change(
        "channel",
        "chan-not-yet-synced",
        {"node-b": 1},
        "node-b",
        {"name": "general", "description": None, "position": 0, "archived": False},
    )

    rivulet = await db_session.get(Rivulet, "rivulet-pending")
    assert rivulet is not None
    assert rivulet.title == "Queued"
    remaining_pending = list((await db_session.execute(select(SyncPendingInbound))).scalars().all())
    assert remaining_pending == []


# ---------------------------------------------------------------------------
# sync/engine.py: unit-level coverage of SyncEngine internals that the real
# two-host tests above (test_two_engines_sync_agent_state_change,
# test_engine_tracks_inbound_connections_and_detects_disconnect) don't
# exercise -- error/edge branches around startup, shutdown, auto-connect
# races, malformed gossipsub messages, and the file-transfer wire protocol,
# using fakes rather than real libp2p hosts (matching this file's own
# module docstring on _FakeEngine-style tests for apply.py above).
# ---------------------------------------------------------------------------


def test_bound_port_raises_when_host_has_no_tcp_port() -> None:
    class _BadAddr:
        def value_for_protocol(self, proto: str) -> int | None:
            raise ValueError(f"no {proto} protocol on this addr")

    class _FakeHost:
        def get_addrs(self) -> list[_BadAddr]:
            return [_BadAddr()]

    with pytest.raises(RuntimeError, match="no bound TCP port"):
        _bound_port(_FakeHost())  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    "address,expected",
    [
        ("/ip4/192.168.1.5/tcp/4001", True),
        ("/ip4/10.0.0.7/tcp/4001", True),
        ("/ip4/172.16.0.1/tcp/4001", True),
        ("/ip4/127.0.0.1/tcp/4001", True),
        ("/ip4/169.254.10.1/tcp/4001", True),  # IPv4 link-local
        ("/ip6/fc00::1/tcp/4001", True),
        ("/ip6/fe80::1/tcp/4001", True),  # IPv6 link-local
        ("/ip6/::1/tcp/4001", True),
        ("/ip4/8.8.8.8/tcp/4001", False),
        ("/ip4/1.1.1.1/tcp/4001", False),
        # issue #361: CGNAT / Tailscale overlay space is WAN, not LAN --
        # ipaddress.is_private called it private on Python < 3.12.4
        ("/ip4/100.64.0.1/tcp/4001", False),
        ("/ip4/100.100.83.98/tcp/4001", False),  # typical Tailscale address
        ("/ip4/100.127.255.254/tcp/4001", False),
        # is_private=True ranges that were never a real local network
        ("/ip4/192.0.2.10/tcp/4001", False),  # TEST-NET-1
        ("/ip4/198.18.0.1/tcp/4001", False),  # benchmarking
        ("/ip6/2001:db8::1/tcp/4001", False),  # IPv6 documentation range
        ("/ip6/::ffff:192.168.1.5/tcp/4001", True),  # IPv4-mapped RFC1918
        ("", False),  # no address recorded (best-effort gap, see notifee docstring)
        ("not-a-multiaddr", False),
    ],
)
def test_is_lan_address_classifies_private_ranges_as_lan(address: str, expected: bool) -> None:
    assert _is_lan_address(address) is expected  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    "raw,peer_id,expected",
    [
        ("/ip4/127.0.0.1/tcp/1", "12D3KooWpeer", "/ip4/127.0.0.1/tcp/1/p2p/12D3KooWpeer"),
        (
            "/ip4/127.0.0.1/tcp/1/p2p/12D3KooWpeer",
            "12D3KooWpeer",
            "/ip4/127.0.0.1/tcp/1/p2p/12D3KooWpeer",
        ),
        (
            "/ip4/127.0.0.1/tcp/1/p2p/12D3KooWpeer/p2p/12D3KooWpeer",
            "12D3KooWpeer",
            "/ip4/127.0.0.1/tcp/1/p2p/12D3KooWpeer",
        ),
    ],
)
def test_dialable_own_address_keeps_a_single_peer_id_suffix(
    raw: str, peer_id: str, expected: str
) -> None:
    assert _dialable_own_address(raw, peer_id) == expected  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    "address,in_container,expected",
    [
        ("/ip4/127.0.0.1/tcp/1", False, "loopback"),
        ("/ip6/::1/tcp/1", False, "loopback"),
        ("/ip4/192.168.1.5/tcp/1", False, "network"),
        ("/ip4/192.168.1.5/tcp/1", True, "network"),
        ("/ip4/10.0.0.7/tcp/1", True, "network"),
        ("/ip4/172.22.0.2/tcp/1", False, "network"),
        ("/ip4/172.22.0.2/tcp/1", True, "container"),
        ("/ip4/172.17.0.2/tcp/1", True, "container"),
        ("not-a-multiaddr", True, "network"),
        # Invalid /p2p/ suffix must not hide a well-formed host IP.
        ("/ip4/127.0.0.1/tcp/4001/p2p/fake-node-id", False, "loopback"),
        ("/ip4/172.22.0.2/tcp/4001/p2p/fake-node-id", True, "container"),
    ],
)
def test_own_address_scope_labels_loopback_and_docker_bridge(
    address: str, in_container: bool, expected: str
) -> None:
    assert own_address_scope(address, in_container=in_container) == expected


def test_running_in_container_detects_dockerenv(monkeypatch: pytest.MonkeyPatch) -> None:
    original = Path.exists

    def exists(self: Path) -> bool:
        if str(self) == "/.dockerenv":
            return True
        return original(self)

    monkeypatch.setattr(Path, "exists", exists)
    assert running_in_container() is True


def test_sync_status_scopes_and_orders_own_addresses(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRunningEngine()
    fake.own_addresses = [
        "/ip4/172.22.0.2/tcp/4001/p2p/fake-node-id",
        "/ip4/127.0.0.1/tcp/4001/p2p/fake-node-id",
        "/ip4/192.168.1.5/tcp/4001/p2p/fake-node-id",
    ]
    monkeypatch.setattr("rivulets.api.sync.get_sync_engine", lambda: fake)
    monkeypatch.setattr("rivulets.api.sync.running_in_container", lambda: True)

    response = client.get("/api/v1/sync/status", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["own_addresses"] == [
        {"address": "/ip4/192.168.1.5/tcp/4001/p2p/fake-node-id", "scope": "network"},
        {"address": "/ip4/127.0.0.1/tcp/4001/p2p/fake-node-id", "scope": "loopback"},
        {"address": "/ip4/172.22.0.2/tcp/4001/p2p/fake-node-id", "scope": "container"},
    ]


def test_sync_engine_peer_is_lan_reads_connected_peers_address(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path / "sync")
    engine._connected_peers["lan-peer"] = "/ip4/192.168.1.9/tcp/4001"  # pyright: ignore[reportPrivateUsage]
    engine._connected_peers["wan-peer"] = "/ip4/8.8.8.8/tcp/4001"  # pyright: ignore[reportPrivateUsage]

    assert engine.peer_is_lan("lan-peer") is True
    assert engine.peer_is_lan("wan-peer") is False
    assert engine.peer_is_lan("never-connected") is False


async def test_notifee_connected_handles_transport_address_lookup_failure() -> None:
    connected: list[tuple[str, str]] = []
    notifee = _PeerConnectionNotifee(
        on_connected=lambda pid, addr: connected.append((pid, addr)), on_disconnected=lambda _: None
    )

    class _BoomConn:
        muxed_conn = SimpleNamespace(peer_id="peer-x")

        def get_transport_addresses(self) -> list[str]:
            raise RuntimeError("peerstore race")

    await notifee.connected(None, _BoomConn())  # pyright: ignore[reportArgumentType]

    assert connected == [("peer-x", "")]


def test_node_id_raises_when_engine_not_running(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    with pytest.raises(RuntimeError, match="not running"):
        _ = engine.node_id


async def test_start_is_a_noop_when_already_running(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    sentinel = object()
    engine._thread = sentinel  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]

    await engine.start("fingerprint", "psk-hex")

    assert engine._thread is sentinel  # pyright: ignore[reportPrivateUsage]  # start() returned early


async def test_start_surfaces_the_underlying_trio_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = SyncEngine(tmp_path)

    async def _boom() -> None:
        raise RuntimeError("psk rejected by new_host")

    monkeypatch.setattr(engine, "_trio_main", _boom)

    with pytest.raises(RuntimeError, match="Sync engine failed to start"):
        await engine.start("fingerprint", "bad-psk")

    assert engine._thread is None  # pyright: ignore[reportPrivateUsage]


async def test_stop_is_a_noop_when_not_running(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    await engine.stop()  # must not raise


async def test_stop_swallows_run_finished_error_from_the_trio_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trio.run() that's already tearing down on its own races stop()'s
    own signal -- trio.from_thread.run_sync raises RunFinishedError in that
    case, which must be swallowed rather than propagated."""
    engine = SyncEngine(tmp_path)
    already_finished_thread = threading_dummy_thread()
    engine._thread = already_finished_thread  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    engine._trio_token = object()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    engine._stop_event = trio.Event()  # pyright: ignore[reportPrivateUsage]

    def _raise_run_finished(*_args: object, **_kwargs: object) -> None:
        raise trio.RunFinishedError("trio run already exited")

    monkeypatch.setattr(trio.from_thread, "run_sync", _raise_run_finished)

    await engine.stop()  # must not raise

    assert engine._thread is None  # pyright: ignore[reportPrivateUsage]


def threading_dummy_thread() -> Any:
    import threading

    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()
    return t


async def test_call_trio_raises_when_engine_not_running(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    with pytest.raises(RuntimeError, match="not running"):
        await engine.connect("/ip4/127.0.0.1/tcp/1/p2p/x")


async def test_publish_state_change_is_a_noop_when_engine_not_running(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    await engine.publish_state_change("agent", "agent-1", {"name": "x"}, {"node-a": 1})
    # No exception, and nothing to assert on the wire -- the log message
    # itself (FR-9.5 offline operation) is the documented behavior here.


def _peer_info(peer_id: str, addrs: list[str] | None = None) -> Any:
    """A duck-typed stand-in for libp2p's PeerInfo (peer_id + addrs is all
    engine.py's auto-connect path ever reads off of it) -- typed as Any so
    it satisfies engine.py's real (stub-less, per pyproject.toml's
    per-directory pyright config) PeerInfo parameter type without a
    reportArgumentType ignore-comment at every call site below."""
    return SimpleNamespace(peer_id=peer_id, addrs=addrs or [])


def test_on_peer_discovered_returns_early_with_no_trio_token(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)  # never started -- _trio_token is None
    engine._on_peer_discovered(  # pyright: ignore[reportPrivateUsage]
        _peer_info("peer-x")
    )  # must not raise


def test_on_peer_discovered_logs_and_swallows_auto_connect_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = SyncEngine(tmp_path)
    engine._trio_token = object()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("not actually in a trio thread")

    monkeypatch.setattr(trio.from_thread, "run", _raise)

    engine._on_peer_discovered(  # pyright: ignore[reportPrivateUsage]
        _peer_info("peer-x")
    )  # must not raise


def test_trio_auto_connect_branches(tmp_path: Path) -> None:
    """_trio_auto_connect uses trio.Lock/trio.fail_after, so it needs a real
    trio run loop -- exercised directly here (rather than through a real
    libp2p host) for the self/already-connected/failure/success branches."""

    async def main() -> None:
        engine = SyncEngine(tmp_path)
        engine._node_id = "self-node"  # pyright: ignore[reportPrivateUsage]
        engine._host = object()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]

        # 1. Skips connecting to itself.
        await engine._trio_auto_connect(  # pyright: ignore[reportPrivateUsage]
            _peer_info("self-node")
        )
        assert engine._connected_peers == {}  # pyright: ignore[reportPrivateUsage]

        # 2. Skips a peer already recorded as connected.
        engine._connected_peers["peer-already"] = "/some/addr"  # pyright: ignore[reportPrivateUsage]
        await engine._trio_auto_connect(  # pyright: ignore[reportPrivateUsage]
            _peer_info("peer-already")
        )
        assert engine._connected_peers["peer-already"] == "/some/addr"  # pyright: ignore[reportPrivateUsage]

        # 3. Logs and swallows a connect failure -- never recorded as connected.
        class _FailingHost:
            async def connect(self, _info: object) -> None:
                raise RuntimeError("connection refused")

        engine._host = _FailingHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        await engine._trio_auto_connect(  # pyright: ignore[reportPrivateUsage]
            _peer_info("peer-fails")
        )
        assert "peer-fails" not in engine._connected_peers  # pyright: ignore[reportPrivateUsage]

        # 4. A clean connect records the peer and its first address.
        class _WorkingHost:
            async def connect(self, _info: object) -> None:
                return None

        engine._host = _WorkingHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        await engine._trio_auto_connect(  # pyright: ignore[reportPrivateUsage]
            _peer_info("peer-ok", ["/ip4/1.2.3.4/tcp/1"])
        )
        assert engine._connected_peers["peer-ok"] == "/ip4/1.2.3.4/tcp/1"  # pyright: ignore[reportPrivateUsage]

    trio.run(main)


def test_on_peer_connected_ignores_self(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    engine._node_id = "self-node"  # pyright: ignore[reportPrivateUsage]
    engine._on_peer_connected("self-node", "/some/addr")  # pyright: ignore[reportPrivateUsage]
    assert engine._connected_peers == {}  # pyright: ignore[reportPrivateUsage]


async def test_trigger_peer_connected_handler_is_a_noop_with_no_handler(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    engine._loop = asyncio.get_running_loop()  # pyright: ignore[reportPrivateUsage]
    # Sleeps _MESH_FORM_DELAY_SECONDS internally -- monkeypatch trio.sleep so
    # this doesn't add ~2s of real wall-clock time to the suite.
    import rivulets.sync.engine as engine_module

    async def _no_sleep(_seconds: float) -> None:
        return None

    original_sleep = engine_module.trio.sleep
    engine_module.trio.sleep = _no_sleep  # type: ignore[assignment]
    try:
        await engine._trigger_peer_connected_handler("peer-1")  # pyright: ignore[reportPrivateUsage]
    finally:
        engine_module.trio.sleep = original_sleep  # type: ignore[assignment]


async def test_trigger_peer_connected_handler_invokes_the_registered_handler(
    tmp_path: Path,
) -> None:
    engine = SyncEngine(tmp_path)
    engine._loop = asyncio.get_running_loop()  # pyright: ignore[reportPrivateUsage]
    called = asyncio.Event()
    seen: list[str] = []

    async def handler(peer_id: str) -> None:
        seen.append(peer_id)
        called.set()

    engine.set_peer_connected_handler(handler)

    import rivulets.sync.engine as engine_module

    async def _no_sleep(_seconds: float) -> None:
        return None

    original_sleep = engine_module.trio.sleep
    engine_module.trio.sleep = _no_sleep  # type: ignore[assignment]
    try:
        await engine._trigger_peer_connected_handler("peer-1")  # pyright: ignore[reportPrivateUsage]
        await asyncio.wait_for(called.wait(), timeout=5)
        assert seen == ["peer-1"]
    finally:
        engine_module.trio.sleep = original_sleep  # type: ignore[assignment]


def _noop_on_connected(_peer_id: str, _address: str) -> None:
    return None


def _noop_on_disconnected(_peer_id: str) -> None:
    return None


async def test_notifee_listen_callbacks_are_no_ops() -> None:
    """opened_stream/closed_stream/listen/listen_close are protocol-required
    INotifee overrides this engine doesn't act on -- confirms they're
    genuinely inert rather than accidentally raising."""
    notifee = _PeerConnectionNotifee(
        on_connected=_noop_on_connected, on_disconnected=_noop_on_disconnected
    )
    assert await notifee.opened_stream(None, None) is None  # pyright: ignore[reportArgumentType]
    assert await notifee.closed_stream(None, None) is None  # pyright: ignore[reportArgumentType]
    assert await notifee.listen(None, None) is None  # pyright: ignore[reportArgumentType]
    assert await notifee.listen_close(None, None) is None  # pyright: ignore[reportArgumentType]


async def test_receive_loop_discards_a_malformed_message(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    engine._loop = asyncio.get_running_loop()  # pyright: ignore[reportPrivateUsage]
    received: list[object] = []

    async def on_change(*args: object) -> None:
        received.append(args)

    engine.set_state_change_handler(on_change)  # type: ignore[arg-type]

    class _BadMessage:
        from_id = b"someone-else"
        data = b"not valid json"

    async def _one_message() -> Any:
        yield _BadMessage()

    await engine._receive_loop(_one_message())  # pyright: ignore[reportPrivateUsage]

    assert received == []  # malformed message was discarded, not handed to the handler


class _FakeFileStream:
    def __init__(self, to_read: bytes = b"") -> None:
        self._buf = to_read
        self.written = b""
        self.closed = False
        self.write_closed = False

    async def read(self, n: int) -> bytes:
        chunk = self._buf[:n]
        self._buf = self._buf[n:]
        return chunk

    async def write(self, data: bytes) -> None:
        self.written += data

    async def close(self) -> None:
        self.closed = True

    async def close_write(self) -> None:
        self.write_closed = True


async def test_handle_file_transfer_stream_reports_miss(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)
    try:
        stream = _FakeFileStream(to_read=("0" * HASH_LEN).encode())
        await engine._handle_file_transfer_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert stream.written == MISS_MARKER
        assert stream.closed is True
    finally:
        monkeypatch.undo()


async def test_handle_file_transfer_stream_reports_hit(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)
    try:
        content_hash = hashlib.sha256(b"hello from disk").hexdigest()
        local_path = get_settings().files_dir / content_hash[:2] / content_hash
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"hello from disk")

        stream = _FakeFileStream(to_read=content_hash.encode())
        await engine._handle_file_transfer_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

        assert (
            stream.written
            == HIT_PREFIX + struct.pack(">Q", len(b"hello from disk")) + b"hello from disk"
        )
        assert stream.closed is True
    finally:
        monkeypatch.undo()


async def test_handle_file_transfer_stream_refuses_to_serve_mismatched_content(
    tmp_path: Path,
) -> None:
    """#288: the file on disk under files_dir/<hash>/ must actually hash
    to <hash> -- e.g. disk corruption, or a row written before this check
    existed -- or it's served as a MISS rather than handed out as if it
    were authentic content for the requested hash."""
    engine = SyncEngine(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)
    try:
        content_hash = "a" * HASH_LEN
        local_path = get_settings().files_dir / content_hash[:2] / content_hash
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"this does not hash to content_hash")

        stream = _FakeFileStream(to_read=content_hash.encode())
        await engine._handle_file_transfer_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

        assert stream.written == MISS_MARKER
        assert stream.closed is True
    finally:
        monkeypatch.undo()


async def test_handle_file_transfer_stream_reports_miss_for_invalid_hash(tmp_path: Path) -> None:
    """#239: content_hash comes straight off the wire from a peer -- a
    non-hex value (e.g. an attempted path-traversal payload) must be
    treated as a miss, never joined onto files_dir and read from disk."""
    engine = SyncEngine(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(get_settings(), "workspace_dir", tmp_path)
    try:
        secret = tmp_path / "secret.txt"
        secret.write_text("should never be readable via file transfer")
        # Exactly HASH_LEN bytes (the protocol reads a fixed-length hash),
        # but not a valid hex digest -- a "../"-style traversal attempt.
        traversal_hash = ("../" * 20 + "secret.txt").ljust(HASH_LEN, "0")[:HASH_LEN]

        stream = _FakeFileStream(to_read=traversal_hash.encode())
        await engine._handle_file_transfer_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

        assert stream.written == MISS_MARKER
        assert stream.closed is True
    finally:
        monkeypatch.undo()


async def test_handle_file_transfer_stream_logs_and_closes_on_malformed_request(
    tmp_path: Path,
) -> None:
    engine = SyncEngine(tmp_path)
    stream = _FakeFileStream(to_read=b"")  # too short -- read_exactly raises EOFError

    await engine._handle_file_transfer_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    assert stream.written == b""
    assert stream.closed is True  # finally still runs


def _sample_dispatch_request() -> AgentDispatchRequest:
    return AgentDispatchRequest(
        rivulet_id="riv-1",
        channel_id="chan-1",
        agent_id="agent-1",
        message_content="hello",
        from_agent_id=None,
        from_agent_name=None,
        triggering_message_id="msg-1",
    )


def _framed(data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + data


def _unframe_response(written: bytes) -> dict[str, Any]:
    length = int.from_bytes(written[:4], "big")
    return json.loads(written[4 : 4 + length].decode())


async def test_handle_agent_dispatch_stream_acks_accepted_when_handler_registered(
    tmp_path: Path,
) -> None:
    engine = SyncEngine(tmp_path)
    engine._loop = asyncio.get_running_loop()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

    received: list[AgentDispatchRequest] = []

    async def fake_handler(request: AgentDispatchRequest) -> None:
        received.append(request)

    engine.set_agent_dispatch_handler(fake_handler)
    request = _sample_dispatch_request()
    stream = _FakeFileStream(to_read=_framed(request.to_bytes()))

    await engine._handle_agent_dispatch_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    assert _unframe_response(stream.written) == {"accepted": True, "reason": None}
    assert stream.closed is True

    # The handler runs via asyncio.run_coroutine_threadsafe -- fire-and-
    # forget from the stream handler's own perspective, so poll briefly
    # for it to actually run before asserting.
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.01)
    assert received == [request]


async def test_handle_agent_dispatch_stream_acks_rejected_when_no_handler_registered(
    tmp_path: Path,
) -> None:
    engine = SyncEngine(tmp_path)
    engine._loop = asyncio.get_running_loop()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    stream = _FakeFileStream(to_read=_framed(_sample_dispatch_request().to_bytes()))

    await engine._handle_agent_dispatch_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    response = _unframe_response(stream.written)
    assert response["accepted"] is False
    assert response["reason"]
    assert stream.closed is True


async def test_handle_agent_dispatch_stream_logs_and_closes_on_malformed_request(
    tmp_path: Path,
) -> None:
    engine = SyncEngine(tmp_path)
    stream = _FakeFileStream(to_read=b"")  # too short -- read_exactly raises EOFError

    await engine._handle_agent_dispatch_stream(stream)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    assert stream.written == b""
    assert stream.closed is True  # finally still runs


def test_trio_dispatch_agent_returns_true_on_accept(tmp_path: Path) -> None:
    request = _sample_dispatch_request()
    response = _framed(json.dumps({"accepted": True, "reason": None}).encode())

    class _FakeHost:
        async def new_stream(self, _peer_id: object, _protocols: object) -> _FakeFileStream:
            return _FakeFileStream(to_read=response)

    async def main() -> None:
        engine = SyncEngine(tmp_path)
        engine._host = _FakeHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        result = await engine._trio_dispatch_agent(  # pyright: ignore[reportPrivateUsage]
            _valid_base58_peer_id(), request
        )
        assert result is True

    trio.run(main)


def test_trio_dispatch_agent_returns_false_on_reject(tmp_path: Path) -> None:
    response = _framed(json.dumps({"accepted": False, "reason": "no dispatch handler"}).encode())

    class _FakeHost:
        async def new_stream(self, _peer_id: object, _protocols: object) -> _FakeFileStream:
            return _FakeFileStream(to_read=response)

    async def main() -> None:
        engine = SyncEngine(tmp_path)
        engine._host = _FakeHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        result = await engine._trio_dispatch_agent(  # pyright: ignore[reportPrivateUsage]
            _valid_base58_peer_id(), _sample_dispatch_request()
        )
        assert result is False

    trio.run(main)


async def test_dispatch_agent_remotely_returns_false_on_transport_failure(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)  # never started -- _call_trio raises immediately
    result = await engine.dispatch_agent_remotely("peer-x", _sample_dispatch_request())
    assert result is False


def _valid_base58_peer_id() -> str:
    """A syntactically valid libp2p peer id string -- _trio_request_file
    round-trips it through ID.from_base58 before ever touching the (faked)
    host, so an arbitrary string like "peer-x" fails there with a base58
    decode error unrelated to what this test actually exercises."""
    return str(_ID(os.urandom(32)))


def test_trio_request_file_reads_a_hit_response(tmp_path: Path) -> None:
    data = b"remote file bytes"
    response = HIT_PREFIX + struct.pack(">Q", len(data)) + data

    class _FakeHost:
        async def new_stream(self, _peer_id: object, _protocols: object) -> _FakeFileStream:
            return _FakeFileStream(to_read=response)

    async def main() -> None:
        engine = SyncEngine(tmp_path)
        engine._host = _FakeHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        result = await engine._trio_request_file(  # pyright: ignore[reportPrivateUsage]
            _valid_base58_peer_id(), "b" * HASH_LEN
        )
        assert result == data

    trio.run(main)


def test_trio_request_file_returns_none_on_miss(tmp_path: Path) -> None:
    class _FakeHost:
        async def new_stream(self, _peer_id: object, _protocols: object) -> _FakeFileStream:
            return _FakeFileStream(to_read=MISS_MARKER)

    async def main() -> None:
        engine = SyncEngine(tmp_path)
        engine._host = _FakeHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        result = await engine._trio_request_file(  # pyright: ignore[reportPrivateUsage]
            _valid_base58_peer_id(), "c" * HASH_LEN
        )
        assert result is None

    trio.run(main)


def test_trio_request_file_rejects_oversized_declared_length(tmp_path: Path) -> None:
    """#288: a peer can send HIT + an enormous length prefix (up to
    2**64-1) before supplying a single byte of body. That must be
    rejected before read_exactly(stream, length) ever tries to buffer it
    -- not after, which would already have OOM'd the process."""
    oversized_length = MAX_FILE_BYTES + 1
    # No body bytes follow -- if the length check didn't reject this
    # first, read_exactly would hang/EOF trying to read a body that was
    # never sent, rather than this test hanging trying to construct one.
    response = HIT_PREFIX + struct.pack(">Q", oversized_length)

    class _FakeHost:
        async def new_stream(self, _peer_id: object, _protocols: object) -> _FakeFileStream:
            return _FakeFileStream(to_read=response)

    async def main() -> None:
        engine = SyncEngine(tmp_path)
        engine._host = _FakeHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        with pytest.raises(ValueError, match="exceeds"):
            await engine._trio_request_file(  # pyright: ignore[reportPrivateUsage]
                _valid_base58_peer_id(), "f" * HASH_LEN
            )

    trio.run(main)


async def test_request_file_returns_none_on_oversized_declared_length(tmp_path: Path) -> None:
    """The public request_file() wraps _trio_request_file's rejection the
    same way it wraps any other transport failure -- None, not a raised
    exception, since callers (apply.py) already treat None as "not
    available from this peer right now"."""
    oversized_length = MAX_FILE_BYTES + 1
    response = HIT_PREFIX + struct.pack(">Q", oversized_length)

    class _FakeHost:
        async def new_stream(self, _peer_id: object, _protocols: object) -> _FakeFileStream:
            return _FakeFileStream(to_read=response)

    engine = SyncEngine(tmp_path)
    engine._host = _FakeHost()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]

    async def _fake_call_trio(fn: Any, *args: Any) -> Any:
        return await fn(*args)

    engine._call_trio = _fake_call_trio  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]

    result = await engine.request_file(_valid_base58_peer_id(), "f" * HASH_LEN)
    assert result is None


async def test_request_file_returns_none_and_logs_on_transport_failure(tmp_path: Path) -> None:
    engine = SyncEngine(tmp_path)  # never started -- _call_trio raises immediately
    result = await engine.request_file("peer-x", "d" * HASH_LEN)
    assert result is None


def test_get_sync_engine_raises_when_not_initialized() -> None:
    reset_sync_engine_for_testing()
    with pytest.raises(RuntimeError, match="not initialized"):
        get_sync_engine()
