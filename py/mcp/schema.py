"""Dynamic MCP tool schema generation from CompiledGame definitions.

Converts compiled game definitions into MCP-compatible tool schemas.
Tools are generated for deals, votes, channels, and universal actions.
Runtime filtering shows only tools available in the current phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.runtime.state import (
    CompiledGame,
    DealDef,
    GameState,
    PartyDef,
    SpeechActDef,
    VoteDef,
    can_write_channel,
)


@dataclass(frozen=True)
class Tool:
    """MCP tool definition."""

    name: str
    description: str
    inputSchema: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)
    _meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PartyClassification:
    """Result of classifying deal parties into canonical MCP params."""

    type: str  # "immediate" | "bilateral" | "multilateral"
    initiator: str | None = None
    respondent: str | None = None
    target: str | None = None
    mapping: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Party classification
# ---------------------------------------------------------------------------


def classify_parties(parties: dict[str, PartyDef]) -> PartyClassification:
    """Classify deal parties into canonical MCP structure.

    Maps custom party names (leader, partners, etc.) to canonical
    MCP parameter names (actor, responder, responders, target).
    """
    if len(parties) == 0:
        return PartyClassification(type="immediate")

    if len(parties) == 1:
        key = next(iter(parties))
        return PartyClassification(
            type="immediate",
            initiator=key,
            mapping={key: "actor"},
        )

    initiator = None
    respondent = None
    target_key = None

    for key, party in parties.items():
        if key == "target":
            target_key = key
        elif party.count is not None:
            respondent = key  # multilateral (has count constraint)
        elif initiator is None:
            initiator = key
        else:
            respondent = key

    is_multi = (
        respondent is not None
        and parties.get(respondent) is not None
        and parties[respondent].count is not None
    )

    mapping: dict[str, str] = {}
    if initiator:
        mapping[initiator] = "actor"
    if respondent:
        mapping[respondent] = "responders" if is_multi else "responder"
    if target_key:
        mapping[target_key] = "target"

    return PartyClassification(
        type="multilateral" if is_multi else "bilateral",
        initiator=initiator,
        respondent=respondent,
        target=target_key,
        mapping=mapping,
    )


# ---------------------------------------------------------------------------
# Schema builders
# ---------------------------------------------------------------------------


def _param_schema(pdef) -> dict[str, Any]:
    """Convert ParamDef to JSON Schema property."""
    from engine.expr.core import Expr

    match pdef.type:
        case "number":
            schema: dict[str, Any] = {"type": "number"}
            if pdef.min is not None:
                if isinstance(pdef.min, Expr):
                    schema.setdefault("description", "")
                    schema["description"] += f" (min: dynamic)"
                else:
                    schema["minimum"] = pdef.min
            if pdef.max is not None:
                if isinstance(pdef.max, Expr):
                    schema.setdefault("description", "")
                    schema["description"] += f" (max: dynamic)"
                else:
                    schema["maximum"] = pdef.max
        case "string":
            schema = {"type": "string"}
        case "keyword" | "player" | "resource":
            schema = {"type": "string"}
            if pdef.options:
                schema["enum"] = list(pdef.options)
        case _:
            schema = {"type": "string"}
    if pdef.label:
        schema["description"] = pdef.label
    return schema


def deal_to_tool(game_id: str, deal_id: str, deal: DealDef) -> Tool:
    """Generate MCP tool from a deal definition."""
    classification = classify_parties(deal.parties)
    properties: dict[str, Any] = {}
    required: list[str] = []

    # Add party parameters based on classification
    if classification.target:
        properties["target"] = {"type": "string", "description": "Target player"}
        required.append("target")

    if classification.type == "bilateral" and classification.respondent:
        properties["responder"] = {"type": "string", "description": "Responder player"}
        required.append("responder")
    elif classification.type == "multilateral" and classification.respondent:
        party = deal.parties.get(classification.respondent)
        arr_schema: dict[str, Any] = {
            "type": "array",
            "items": {"type": "string"},
            "description": "Responding players",
        }
        if party and party.count:
            arr_schema["minItems"] = party.count[0]
            arr_schema["maxItems"] = party.count[1]
        properties["responders"] = arr_schema
        required.append("responders")

    # Add deal parameters
    for param_id, pdef in deal.params.items():
        properties[param_id] = _param_schema(pdef)
        if pdef.default is None:
            required.append(param_id)

    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required

    # Build description with outcome summary
    desc = deal.doc or f"Execute {deal_id} deal"

    # Add actor filter info so agents know who can use this deal
    if classification.initiator:
        initiator_party = deal.parties.get(classification.initiator)
        if initiator_party and initiator_party.filter is not None:
            from engine.expr.core import Expr

            if isinstance(initiator_party.filter, Expr):
                from mcp.mechanics import describe_expr

                filter_text = describe_expr(initiator_party.filter)
                # Skip trivial "alive" filter — almost every deal has it
                if filter_text != "alive":
                    desc += f" Requires: {filter_text}."

    # Add deal guard info
    if deal.guard is not None:
        from engine.expr.core import Expr

        if isinstance(deal.guard, Expr):
            from mcp.mechanics import describe_expr as _de

            desc += f" Guard: {_de(deal.guard)}."

    if deal.outcomes:
        from mcp.mechanics import outcome_summary

        summary = outcome_summary(deal.outcomes)
        if summary:
            sep = " " if desc.endswith(".") else ". "
            desc += f"{sep}Outcomes: {summary}"

    return Tool(
        name=f"{game_id}/{deal_id}",
        description=desc,
        inputSchema=schema,
        _meta={
            "type": "deal",
            "deal_id": deal_id,
            "party_mapping": classification.mapping,
            "classification": classification.type,
        },
    )


def vote_to_tool(game_id: str, vote_id: str, vote: VoteDef) -> Tool:
    """Generate MCP tool from a vote definition."""
    properties: dict[str, Any] = {
        "option": {"type": "string", "description": "Vote option"},
    }
    required = ["option"]

    if vote.options:
        properties["option"]["enum"] = list(vote.options)

    if vote.subject:
        properties["subject"] = {"type": "string", "description": "Vote subject"}
        required.append("subject")

    schema = {"type": "object", "properties": properties, "required": required}

    base_desc = vote.doc or f"Vote in {vote_id}"
    desc = base_desc + ". Casts on pending vote if one exists, otherwise starts new."

    return Tool(
        name=f"{game_id}/vote_{vote_id}",
        description=desc,
        inputSchema=schema,
        _meta={"type": "vote", "vote_id": vote_id},
    )


def channel_to_tool(game_id: str, ch_id: str, ch, context=None) -> Tool:
    """Generate MCP tool from a channel definition."""
    properties: dict[str, Any] = {
        "content": {
            "type": "string",
            "description": "Message content",
            "maxLength": 500,
        },
    }
    schema = {"type": "object", "properties": properties, "required": ["content"]}

    desc = ch.description or f"Send message to {ch_id}"
    if context and context.channel_hints:
        hint = context.channel_hints.get(ch_id)
        if hint:
            if hint.when_to_use:
                desc += f". When: {hint.when_to_use}"
            if hint.risk:
                desc += f". Risk: {hint.risk}"

    return Tool(
        name=f"{game_id}/send_{ch_id}",
        description=desc,
        inputSchema=schema,
        _meta={"type": "channel", "channel_id": ch_id},
    )


def speech_act_to_tool(game_id: str, sa_id: str, sa: SpeechActDef) -> Tool:
    """Generate MCP tool from a speech act definition."""
    properties: dict[str, Any] = {}
    required: list[str] = []

    if sa.target_filter is not None:
        properties["target"] = {"type": "string", "description": "Target player"}
        required.append("target")

    for param_id, pdef in sa.params.items():
        properties[param_id] = _param_schema(pdef)
        if pdef.default is None:
            required.append(param_id)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required

    # Build cost description
    cost_parts = [f"{amt} {res}" for res, amt in sa.cost.items()]
    cost_str = f" (cost: {', '.join(cost_parts)})" if cost_parts else ""

    # Add verification summary
    desc = (sa.doc or f"{sa.act_type.title()}: {sa_id}") + cost_str
    if sa.verify_triggers:
        from mcp.mechanics import speech_act_verification_summary

        verify_str = speech_act_verification_summary(sa)
        if verify_str:
            sep = " " if desc.endswith(".") else ". "
            desc += f"{sep}{verify_str}"

    return Tool(
        name=f"{game_id}/{sa_id}",
        description=desc,
        inputSchema=schema,
        _meta={"type": "speech_act", "speech_act_id": sa_id, "act_type": sa.act_type},
    )


# ---------------------------------------------------------------------------
# Universal tools (always available in-game)
# ---------------------------------------------------------------------------


_READONLY = {"readOnlyHint": True}
_MUTATING = {"readOnlyHint": False}


def _universal_tools() -> list[Tool]:
    from mcp.handlers.channels import TOOLS as channel_tools
    from mcp.handlers.helpers import TOOLS as helper_tools

    return (
        channel_tools
        + helper_tools
        + [
            Tool(
                name="get_status",
                description="Get current game state (your view, filtered by visibility)",
                annotations=_READONLY,
                _meta={"type": "query"},
            ),
            Tool(
                name="get_history",
                description="Get recent game events",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "default": 10,
                            "description": "Number of events",
                        }
                    },
                },
                annotations=_READONLY,
                _meta={"type": "query"},
            ),
            Tool(
                name="available_actions",
                description="List actions you can take right now",
                annotations=_READONLY,
                _meta={"type": "query"},
            ),
            Tool(
                name="advance_phase",
                description="Advance to next game phase",
                annotations=_MUTATING,
                _meta={"type": "action"},
            ),
            Tool(
                name="respond",
                description="Respond to a pending deal",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "instance_id": {"type": "string"},
                        "response": {"type": "string"},
                    },
                    "required": ["instance_id", "response"],
                },
                annotations=_MUTATING,
                _meta={"type": "action"},
            ),
            Tool(
                name="leave_game",
                description="Leave the current game",
                annotations=_MUTATING,
                _meta={"type": "action"},
            ),
            Tool(
                name="endorse",
                description="Endorse another player's speech act (claim, accusation, etc.)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "instance_id": {
                            "type": "string",
                            "description": "Instance ID of the speech act to endorse",
                        },
                    },
                    "required": ["instance_id"],
                },
                annotations=_MUTATING,
                _meta={"type": "action"},
            ),
            Tool(
                name="respond_to_inquire",
                description="Respond to an inquiry from another player",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "instance_id": {
                            "type": "string",
                            "description": "Instance ID of the inquire to respond to",
                        },
                        "response": {
                            "type": "string",
                            "description": "Your response",
                        },
                    },
                    "required": ["instance_id", "response"],
                },
                annotations=_MUTATING,
                _meta={"type": "action"},
            ),
            Tool(
                name="act",
                description=(
                    "Combined observe+execute. No args: returns status + available "
                    "actions. With action: executes it, then returns result + "
                    "updated status + available actions. Reduces 3 calls to 1."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": (
                                "Action to execute (deal id, vote_X, send_X, "
                                "advance_phase, respond). Omit to just observe."
                            ),
                        },
                        "args": {
                            "type": "object",
                            "description": "Arguments for the action",
                        },
                    },
                },
                annotations=_MUTATING,
                _meta={"type": "action"},
            ),
            Tool(
                name="wait_for_turn",
                description=(
                    "Block until game state changes (new phase, deal, vote). "
                    "Returns immediately if you have pending actions. "
                    "Use instead of polling get_status. Max 60s timeout."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "timeout": {
                            "type": "number",
                            "description": "Max seconds to wait (default 60, max 60)",
                        },
                    },
                },
                annotations=_READONLY,
                _meta={"type": "action"},
            ),
            Tool(
                name="help",
                description="What to do next — contextual guidance",
                annotations=_READONLY,
                _meta={"type": "query"},
            ),
            Tool(
                name="simulate",
                description=(
                    "Preview an action without committing. Shows what would "
                    "happen (resource changes, deal outcome, phase transition) "
                    "without modifying game state. Same args as act."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": (
                                "Action to simulate (deal id, vote_X, "
                                "advance_phase)"
                            ),
                        },
                        "args": {
                            "type": "object",
                            "description": "Arguments for the action",
                        },
                    },
                    "required": ["action"],
                },
                annotations=_READONLY,
                _meta={"type": "query"},
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Full schema generation
# ---------------------------------------------------------------------------


def generate_game_tools(compiled: CompiledGame) -> list[Tool]:
    """Generate all possible MCP tools from a compiled game."""
    tools: list[Tool] = []

    for deal_id, deal in compiled.deals.items():
        tools.append(deal_to_tool(compiled.id, deal_id, deal))

    for vote_id, vote in compiled.votes.items():
        tools.append(vote_to_tool(compiled.id, vote_id, vote))

    for ch_id, ch in compiled.channels.items():
        tools.append(channel_to_tool(compiled.id, ch_id, ch, compiled.context))

    for sa_id, sa in compiled.speech_acts.items():
        tools.append(speech_act_to_tool(compiled.id, sa_id, sa))

    tools.extend(_universal_tools())
    return tools


def _is_deal_exhausted(
    state: GameState, deal_id: str, player_id: str, deal: DealDef
) -> bool:
    """Check if usage limits are exhausted for a deal."""
    key = f"{player_id}:{deal_id}"
    usage = state.usage.get(key, {})

    if (
        deal.per_round is not None
        and usage.get(f"round:{state.round}", 0) >= deal.per_round
    ):
        return True
    if (
        deal.per_phase is not None
        and usage.get(f"phase:{state.phase}", 0) >= deal.per_phase
    ):
        return True
    if deal.per_game is not None and usage.get("game", 0) >= deal.per_game:
        return True
    return False


def _player_can_use_deal(
    deal: DealDef,
    player_id: str,
    state: GameState,
    compiled: CompiledGame,
) -> bool:
    """Check if a player can initiate a deal based on usage limits, party filters, and guard."""
    from engine.expr.evaluator import Context, evaluate

    # Check usage limits first (cheap)
    if _is_deal_exhausted(state, deal.id, player_id, deal):
        return False

    classification = classify_parties(deal.parties)
    initiator_key = classification.initiator

    # Check actor/initiator party filter
    if initiator_key and initiator_key in deal.parties:
        party_def = deal.parties[initiator_key]
        if party_def.filter is not None:
            ctx = Context(
                state=state,
                compiled=compiled,
                bindings={"actor": player_id, initiator_key: player_id},
            )
            if not evaluate(party_def.filter, ctx):
                return False

    # Check deal guard
    if deal.guard is not None:
        ctx = Context(
            state=state,
            compiled=compiled,
            bindings={"actor": player_id},
        )
        if not evaluate(deal.guard, ctx):
            return False

    return True


def _player_can_use_speech_act(
    sa: SpeechActDef,
    player_id: str,
    state: GameState,
    compiled: CompiledGame,
) -> bool:
    """Check if a player can perform a speech act."""
    from engine.expr.evaluator import Context, evaluate

    # Check usage limits
    if _is_speech_act_exhausted(state, sa.id, player_id, sa):
        return False

    if sa.actor_filter is not None:
        ctx = Context(
            state=state,
            compiled=compiled,
            bindings={"actor": player_id},
        )
        if not evaluate(sa.actor_filter, ctx):
            return False

    # Check cost affordability
    for resource, amount in sa.cost.items():
        if state.get_resource(player_id, resource) < amount:
            return False

    return True


def _is_speech_act_exhausted(
    state: GameState, sa_id: str, player_id: str, sa: SpeechActDef
) -> bool:
    """Check if usage limits are exhausted for a speech act."""
    key = f"{player_id}:{sa_id}"
    usage = state.usage.get(key, {})

    if (
        sa.per_round is not None
        and usage.get(f"round:{state.round}", 0) >= sa.per_round
    ):
        return True
    if (
        sa.per_phase is not None
        and usage.get(f"phase:{state.phase}", 0) >= sa.per_phase
    ):
        return True
    if sa.per_game is not None and usage.get("game", 0) >= sa.per_game:
        return True
    return False


def filter_tools_for_phase(
    tools: list[Tool],
    state: GameState,
    compiled: CompiledGame,
    player_id: str,
) -> list[Tool]:
    """Filter tools to only those available in the current phase and player."""
    # Find current phase
    phase_def = None
    for p in compiled.phases:
        if p.id == state.phase:
            phase_def = p
            break

    allowed_actions = set(phase_def.allows) if phase_def else set()

    filtered = []
    for tool in tools:
        meta = tool._meta
        tool_type = meta.get("type", "")

        if tool_type == "query":
            filtered.append(tool)
        elif tool_type == "action":
            filtered.append(tool)
        elif tool_type == "deal":
            deal_id = meta["deal_id"]
            if deal_id in allowed_actions:
                deal = compiled.deals.get(deal_id)
                if deal and _player_can_use_deal(deal, player_id, state, compiled):
                    filtered.append(tool)
        elif tool_type == "vote":
            if meta["vote_id"] in allowed_actions:
                filtered.append(tool)
        elif tool_type == "channel":
            ch_id = meta["channel_id"]
            ch = compiled.channels.get(ch_id)
            if ch and (not ch.phase_filter or state.phase in ch.phase_filter):
                # Only show channel send tool if player can write to it
                if can_write_channel(state, player_id, ch_id, compiled):
                    filtered.append(tool)
        elif tool_type == "speech_act":
            sa_id = meta["speech_act_id"]
            sa = compiled.speech_acts.get(sa_id)
            if sa and (not sa.phase_filter or state.phase in sa.phase_filter):
                if sa_id in allowed_actions:
                    if _player_can_use_speech_act(sa, player_id, state, compiled):
                        filtered.append(tool)

    # Add respond tool only if there are pending deals for this player
    has_pending = any(
        player_id in pd.responders and pd.responders[player_id] is None
        for pd in state.pending_deals.values()
    )
    if not has_pending:
        filtered = [t for t in filtered if t.name != "respond"]

    # Show endorse only if there are endorsable pending speech acts (not own)
    has_endorsable = any(
        sa.status == "pending"
        and sa.actor != player_id
        and sa.endorsers is not None
        and player_id not in sa.endorsers
        for sa in state.pending_speech_acts.values()
        if compiled.speech_acts.get(sa.speech_act_id, None)
        and compiled.speech_acts[sa.speech_act_id].endorsable
    )
    if not has_endorsable:
        filtered = [t for t in filtered if t.name != "endorse"]

    # Show respond_to_inquire only if there's a pending inquire targeting this player
    has_inquire = any(
        sa.act_type == "inquire"
        and sa.target == player_id
        and sa.inquire_response is None
        for sa in state.pending_speech_acts.values()
    )
    if not has_inquire:
        filtered = [t for t in filtered if t.name != "respond_to_inquire"]

    return filtered
