"""Game state — immutable with functional updates via attrs.evolve.

This is the single state representation. Both the runtime and the evaluator
operate on GameState. No separate "flow/state" vs "v3/state" layers.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import attrs


class Visibility(Enum):
    PUBLIC = "public"  # everyone sees real value
    PRIVATE = "private"  # only owner sees their own
    HIDDEN = "hidden"  # runtime-only, never exposed to any view


# Reveal key helpers — semantic constructors for the (observer, entity, attr) tuple.
# Using plain tuples (not NamedTuple) for zero-overhead serialization with cattrs.
PUBLIC_OBSERVER = "public"


def reveal_key(observer: str, entity: str, attr: str) -> tuple[str, str, str]:
    """Create a reveal key for observer-specific visibility."""
    return (observer, entity, attr)


def public_reveal_key(entity: str, attr: str) -> tuple[str, str, str]:
    """Create a public reveal key visible to all observers."""
    return (PUBLIC_OBSERVER, entity, attr)


# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------


@attrs.frozen
class Entity:
    """A player or NPC in the game."""

    id: str
    active: bool = True
    resources: dict[str, int | float] = attrs.Factory(dict)
    attrs_: dict[str, Any] = attrs.Factory(dict)
    groups: frozenset[str] = frozenset()

    def get_resource(self, name: str) -> int | float:
        return self.resources.get(name, 0)

    def get_attr(self, name: str) -> Any:
        return self.attrs_.get(name)


@attrs.frozen
class Group:
    """A group of entities (e.g., wolf_pack, lovers)."""

    id: str
    type: str
    members: frozenset[str] = frozenset()
    name: str = ""


@attrs.frozen
class PendingDeal:
    """A deal awaiting response."""

    instance_id: str
    deal_id: str
    proposer: str
    responders: dict[str, str | None] = attrs.Factory(dict)  # eid → response
    params: dict[str, Any] = attrs.Factory(dict)
    stakes: dict[str, list[tuple[str, int | float]]] = attrs.Factory(dict)
    target: str | None = None  # target entity for deals that use 3-party bindings


@attrs.frozen
class PendingVote:
    """A vote in progress."""

    instance_id: str
    vote_id: str
    proposer: str | None = None
    subject: str | None = None
    params: dict[str, Any] = attrs.Factory(dict)
    eligible: tuple[str, ...] = ()
    options: tuple[str, ...] = ()
    votes: dict[str, str] = attrs.Factory(dict)  # voter → option
    weights: dict[str, float] = attrs.Factory(dict)  # voter → weight


@attrs.frozen
class Message:
    """A message sent through a channel — first-class data in GameState.

    Messages are immutable, stored in state for replay/archive/verification.
    Each message records who sent it, through which channel, and when.
    """

    id: str  # deterministic: msg-0, msg-1, ...
    channel: str  # channel id
    sender: str  # entity id
    content: str  # text content
    round: int = 0  # game round when sent
    phase: str = ""  # phase when sent
    metadata: dict[str, Any] = attrs.Factory(dict)  # optional structured data


@attrs.frozen
class HistoryEntry:
    """Log entry for game replay."""

    type: str
    data: dict[str, Any] = attrs.Factory(dict)


# ---------------------------------------------------------------------------
# Compiled game definition (produced by compiler, read-only at runtime)
# ---------------------------------------------------------------------------


@attrs.frozen
class ResourceDef:
    id: str
    initial: int | float = 0
    visibility: Visibility = Visibility.PUBLIC
    bounds: tuple[int | float | None, int | float | None] = (None, None)
    transferable: bool = True


@attrs.frozen
class AttrDef:
    id: str
    initial: Any = None
    visibility: Visibility = Visibility.PUBLIC
    values: tuple[str, ...] | None = None
    distribute: bool = False


@attrs.frozen
class RoleDef:
    id: str
    team: str
    count: int = 1
    unique: bool = True
    filler: bool = False
    appears_as: str | None = None
    doc: str = ""
    min_players: int | None = None


@attrs.frozen
class PartyDef:
    filter: Any = None  # Expr or None
    excludes: tuple[str, ...] = ()
    not_in_group: str | None = None
    count: tuple[int, int] | None = None  # (min, max) for multilateral


@attrs.frozen
class ParamDef:
    type: str  # "number", "string", "keyword", "player", "resource"
    min: Any = None
    max: Any = None
    default: Any = None
    filter: Any = None  # Expr
    options: tuple[str, ...] | None = None
    label: str = ""
    placeholder: str = ""


@attrs.frozen
class GameParamDef:
    """Tunable game-level parameter for training variation.

    Game params are injected into state.vars_ at start_game() time.
    Accessible in Expr via game.param_name (existing path resolution).
    """

    id: str
    default: int | float | str = 0
    type: str = "number"  # "number" | "keyword"
    min: int | float | None = None
    max: int | float | None = None
    options: tuple[str, ...] = ()
    label: str = ""


@attrs.frozen
class OutcomeDef:
    effects: tuple = ()  # tuple of Effect
    doc: str = ""
    guard: Any = None  # Expr — condition for auto-selection
    priority: int = 0  # higher priority checked first in guard evaluation


@attrs.frozen
class DealDef:
    id: str
    parties: dict[str, PartyDef] = attrs.Factory(dict)
    params: dict[str, ParamDef] = attrs.Factory(dict)
    stakes: dict[str, tuple] = attrs.Factory(dict)
    guard: Any = None  # Expr
    response_options: tuple[str, ...] = ()
    outcomes: dict[str, OutcomeDef] = attrs.Factory(dict)
    completion_rule: str = "all"
    per_round: int | None = None
    per_phase: int | None = None
    per_game: int | None = None
    doc: str = ""


@attrs.frozen
class VoteDef:
    id: str
    proposer_filter: Any = None
    voters_filter: Any = None
    subject: dict[str, Any] | None = None
    options: tuple[str, ...] = ()
    threshold: str = "majority"
    visibility: str = "public"
    outcomes: dict[str, OutcomeDef] = attrs.Factory(dict)
    doc: str = ""


@attrs.frozen
class TransitionDef:
    """Conditional phase transition rule.

    Evaluated in order during phase advancement.
    First guard that evaluates truthy selects the target phase.
    """

    guard: Any  # Expr
    target: str  # phase_id to jump to


@attrs.frozen
class PhaseDef:
    id: str
    name: str = ""
    category: str = "action"  # setup, action, resolution, transition
    when: Any = None  # Expr guard
    automatic: bool = False
    once: bool = False
    parallel: bool = False
    allows: tuple[str, ...] = ()
    effects: tuple = ()  # entry effects
    channels: tuple[str, ...] = ()
    resolution: str = "first_wins"
    priorities: dict[str, int] = attrs.Factory(dict)
    duration: int | None = None  # seconds
    # Non-linear phase transitions
    next: str | None = None  # unconditional jump (None = linear fallback)
    transitions: tuple = ()  # tuple[TransitionDef, ...] — conditional, first match wins
    starts_round: bool = False  # entering this phase increments round counter
    # Training: per-phase reward signal (Expr, evaluated as delta for PBRS)
    reward_expr: Any = None


@attrs.frozen
class VictoryDef:
    id: str
    type: str = "single"  # single, distribution
    when: Any = None  # Expr
    team: str | None = None
    score: Any = None  # Expr
    priority: int = 100
    shared: bool = False
    individual: bool = False
    message: str = ""


@attrs.frozen
class GroupTypeDef:
    id: str
    visible: bool = True
    exclusive: bool = False
    knows_members: bool = True
    linked_fate: bool = False
    max_size: int | None = None


@attrs.frozen
class ChannelDef:
    """Communication channel with DSL-driven access rules.

    Channels are not just decorative — they control who can send/read messages.
    Access is expressed via Expr filters, same as deal party filters.
    """

    id: str
    type: str = "public"  # public, group, private, broadcast
    group: str | None = None  # for group channels: which group
    write_filter: Any = None  # Expr — who can send (None = all active)
    read_filter: Any = None  # Expr — who can read (None = derived from type)
    phase_filter: tuple[str, ...] = ()  # empty = all phases; non-empty = only these
    max_participants: int | None = None
    effects: tuple = ()  # effects triggered on each message (e.g., +suspicion for whisper)
    description: str = ""


@attrs.frozen
class CommitmentDef:
    """Declarative hook triggered by game events.

    Commitments are static rules (not player actions).
    Example: lover heartbreak — when one lover is eliminated, the other dies too.
    """

    id: str
    trigger: str = "eliminate"  # "eliminate" | "phase_change" | "round_start"
    guard: Any = None  # Expr — condition to fire (actor = triggered entity)
    effects: tuple = ()  # effects to execute
    once: bool = False  # fire only once per game
    doc: str = ""


@attrs.frozen
class SpeechActDef:
    """Performative speech act definition — first-class game primitive.

    Six act types: claim, accuse, promise, predict, endorse, inquire.
    Each has resource cost (credible signal), deferred verification,
    and optional endorsement chains.
    """

    id: str
    act_type: str  # "claim" | "accuse" | "promise" | "predict" | "endorse" | "inquire"
    actor_filter: Any = None  # Expr — who can perform this act
    target_filter: Any = None  # Expr — valid targets (accuse/inquire)
    cost: dict[str, int | float] = attrs.Factory(dict)  # resource → amount
    endorsement_cost: dict[str, int | float] | None = (
        None  # separate cost for endorsing (None = same as cost)
    )
    params: dict[str, ParamDef] = attrs.Factory(dict)

    # Verification
    verify_condition: Any = None  # Expr — what makes claim true (evaluated on trigger)
    verify_triggers: tuple[str, ...] = ("eliminate", "game_end")
    verify_true_effects: tuple = ()  # effects when verified true
    verify_false_effects: tuple = ()  # effects when verified false

    # Inquire-specific
    inquire_response_options: tuple[str, ...] = ()
    inquire_deadline: int = 1  # phases until silence penalty
    inquire_silence_effects: tuple = ()

    # Promise-specific
    promise_action: str | None = None  # deal_id to track
    promise_deadline: int | None = None  # rounds until broken

    # Usage limits
    per_round: int | None = None
    per_phase: int | None = None
    per_game: int | None = None

    # Meta
    endorsable: bool = True
    visibility: str = "public"  # "public" | "group" | "private"
    phase_filter: tuple[str, ...] = ()  # empty = all phases
    doc: str = ""


@attrs.frozen
class PendingSpeechAct:
    """A speech act awaiting verification."""

    instance_id: str
    speech_act_id: str
    act_type: str
    actor: str
    target: str | None = None
    params: dict[str, Any] = attrs.Factory(dict)
    round_created: int = 0
    phase_created: str = ""
    phase_index_created: int = 0  # for inquire deadline (phase-granularity)
    transition_count_created: int = 0  # non-linear aware deadline counter

    # State tracking
    status: str = (
        "pending"  # "pending" | "verified_true" | "verified_false" | "expired"
    )

    # Inquire tracking
    inquire_response: str | None = None

    # Promise tracking
    promise_fulfilled: bool | None = None  # None = not yet checked
    decision_index: int = (
        0  # index into state.decisions when created (for promise scanning)
    )

    # Endorsement chain
    endorsers: tuple[str, ...] = ()  # players who endorsed THIS act


# ---------------------------------------------------------------------------
# Context annotations (metadata for AI agents — not mechanics)
# ---------------------------------------------------------------------------


@attrs.frozen
class VarHint:
    """How to display a game variable to agents."""

    id: str  # var name (must match state.vars_ key)
    label: str = ""  # human label ("Current Lot", "Auction Format")
    format: str = "default"  # "progress" (X/Y), "currency", "player", "default"
    max_var: str | None = None  # for format="progress": var name for denominator
    phases: tuple[str, ...] = ()  # show only in these phases (empty = all)
    priority: int = 50  # higher = shown first (0-100)


@attrs.frozen
class PhaseHint:
    """Strategic context for a phase."""

    id: str  # phase id
    summary: str = ""  # "Choose auction format — affects bidding strategy"
    tips: tuple[str, ...] = ()  # ["Vickrey rewards truthful bidding", ...]
    urgency: str = "normal"  # "critical" | "normal" | "low"


@attrs.frozen
class RoleHint:
    """Per-role strategic guidance."""

    id: str  # role id
    strategy: str = ""  # "As Seer, investigate suspicious players at night"
    allies: tuple[str, ...] = ()  # ["bodyguard", "seer"]
    threats: tuple[str, ...] = ()  # ["werewolf", "alpha_wolf"]
    key_actions: tuple[str, ...] = ()  # ["seer_vision", "declare_role"]
    phase_tips: dict[str, str] = attrs.Factory(dict)  # {"night": "Use vision"}


@attrs.frozen
class ChannelHint:
    """Usage guidance for a communication channel."""

    id: str  # channel id
    when_to_use: str = ""  # "Use to coordinate wolf kills"
    risk: str = ""  # "Others see you whispered — +2 suspicion"
    strategy: str = ""  # "Signal intentions without revealing too much"


@attrs.frozen
class ContextConfig:
    """Universal context configuration — stored in CompiledGame."""

    game_summary: str = ""  # "Auction game with 5 formats, 6 lots..."
    score_explanation: str = ""  # "Victory = gold + collection_value + taste_bonus"
    var_hints: dict[str, VarHint] = attrs.Factory(dict)
    phase_hints: dict[str, PhaseHint] = attrs.Factory(dict)
    role_hints: dict[str, RoleHint] = attrs.Factory(dict)
    channel_hints: dict[str, ChannelHint] = attrs.Factory(dict)
    deal_priorities: dict[str, int] = attrs.Factory(dict)  # deal_id → display order


@attrs.frozen
class CompiledGame:
    """Validated, frozen game definition — read-only at runtime."""

    id: str
    name: str
    min_players: int = 2
    max_players: int = 12
    resources: dict[str, ResourceDef] = attrs.Factory(dict)
    attrs_defs: dict[str, AttrDef] = attrs.Factory(dict)
    roles: dict[str, RoleDef] = attrs.Factory(dict)
    group_types: dict[str, GroupTypeDef] = attrs.Factory(dict)
    deals: dict[str, DealDef] = attrs.Factory(dict)
    votes: dict[str, VoteDef] = attrs.Factory(dict)
    phases: tuple[PhaseDef, ...] = ()
    victories: tuple[VictoryDef, ...] = ()
    channels: dict[str, ChannelDef] = attrs.Factory(dict)
    commitments: dict[str, CommitmentDef] = attrs.Factory(dict)
    speech_acts: dict[str, SpeechActDef] = attrs.Factory(dict)
    source_hash: str = ""
    context: ContextConfig = attrs.Factory(ContextConfig)
    game_params: dict[str, GameParamDef] = attrs.Factory(dict)


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------


@attrs.frozen
class GameState:
    """Complete game state — immutable, functional updates via attrs.evolve."""

    # Phase tracking
    phase_index: int = 0
    phase: str = ""
    round: int = 1

    # Entities, groups, relations
    entities: dict[str, Entity] = attrs.Factory(dict)
    groups: dict[str, Group] = attrs.Factory(dict)
    relations: dict[tuple[str, str], frozenset[str]] = attrs.Factory(dict)

    # Reveals (persistent visibility grants)
    reveals: dict[tuple[str, str, str], Any] = attrs.Factory(dict)

    # Flow variables
    vars_: dict[str, Any] = attrs.Factory(dict)

    # Pending actions
    pending_deals: dict[str, PendingDeal] = attrs.Factory(dict)
    pending_votes: dict[str, PendingVote] = attrs.Factory(dict)

    # Messages (first-class data — for replay, archive, UI, MCP)
    messages: tuple[Message, ...] = ()

    # Speech acts (performative communication with deferred verification)
    pending_speech_acts: dict[str, PendingSpeechAct] = attrs.Factory(dict)
    resolved_speech_acts: tuple[PendingSpeechAct, ...] = ()

    # History and decisions
    history: tuple[HistoryEntry, ...] = ()
    decisions: tuple[dict[str, Any], ...] = ()

    # Once-phase tracking
    executed_once: frozenset[str] = frozenset()

    # Phase transition counter (monotonic, for speech act deadlines)
    phase_transition_count: int = 0

    # Counters for deterministic ID generation
    counters: dict[str, int] = attrs.Factory(dict)

    # RNG seed
    seed: int = 0
    rng_state: int = 0

    # Usage tracking
    usage: dict[str, dict[str, int]] = attrs.Factory(dict)

    # Commitment tracking
    commitments_fired: frozenset[str] = frozenset()

    # Status
    status: str = "active"  # active, ended, cancelled
    victory_result: dict[str, Any] | None = None

    # -----------------------------------------------------------------------
    # Query methods (used by evaluator)
    # -----------------------------------------------------------------------

    def is_active(self, entity_id: str) -> bool:
        entity = self.entities.get(entity_id)
        return entity.active if entity else False

    def get_resource(self, entity_id: str, resource: str) -> int | float:
        entity = self.entities.get(entity_id)
        return entity.get_resource(resource) if entity else 0

    def get_attr(self, entity_id: str, attr: str) -> Any:
        entity = self.entities.get(entity_id)
        return entity.get_attr(attr) if entity else None

    def get_entity_property(
        self, entity_id: str, path: tuple[str, ...], compiled: CompiledGame
    ) -> Any:
        """Resolve entity property by path (resource, attr, or built-in)."""
        if not path:
            return entity_id
        entity = self.entities.get(entity_id)
        if not entity:
            return None
        prop = path[0]
        # Built-ins
        if prop in ("active", "alive"):
            return entity.active
        if prop == "id":
            return entity.id
        # Check defined resources first, then attrs
        if prop in compiled.resources:
            return entity.get_resource(prop)
        if prop in compiled.attrs_defs:
            return entity.get_attr(prop)
        # Fallback: try attr, then resource
        val = entity.get_attr(prop)
        if val is not None:
            return val
        return entity.get_resource(prop)

    def get_game_var(self, name: str) -> Any:
        # Built-in game vars
        if name == "round":
            return self.round
        if name == "phase":
            return self.phase
        return self.vars_.get(name)

    def get_active_entity_ids(self) -> list[str]:
        return [eid for eid, e in self.entities.items() if e.active]

    def entity_in_group(self, entity_id: str, group_id: str) -> bool:
        entity = self.entities.get(entity_id)
        if not entity:
            return False
        # Check by direct group ID
        if group_id in entity.groups:
            return True
        # Check by group type (e.g., "wolf_pack" matches "wolf_pack-0")
        for gid in entity.groups:
            g = self.groups.get(gid)
            if g and g.type == group_id:
                return True
        return False

    def entity_in_group_type(self, entity_id: str, group_type: str) -> bool:
        return self.entity_in_group(entity_id, group_type)

    def in_same_group(self, a: str, b: str) -> bool:
        ea = self.entities.get(a)
        eb = self.entities.get(b)
        if not ea or not eb:
            return False
        return bool(ea.groups & eb.groups)

    def has_relation(self, a: str, b: str, relation: str) -> bool:
        rels = self.relations.get((a, b), frozenset())
        return relation in rels

    def get_group_members(self, group_id: str) -> frozenset[str]:
        g = self.groups.get(group_id)
        if g:
            return g.members
        # Fallback: search by type
        for g in self.groups.values():
            if g.type == group_id:
                return g.members
        return frozenset()

    # -----------------------------------------------------------------------
    # Functional update methods
    # -----------------------------------------------------------------------

    def update_entity(self, entity_id: str, **changes: Any) -> GameState:
        entity = self.entities[entity_id]
        new_entity = attrs.evolve(entity, **changes)
        new_entities = {**self.entities, entity_id: new_entity}
        return attrs.evolve(self, entities=new_entities)

    def set_resource(
        self,
        entity_id: str,
        resource: str,
        value: int | float,
        compiled: CompiledGame | None = None,
    ) -> GameState:
        entity = self.entities[entity_id]
        # Clamp to bounds if compiled game provided
        if compiled and resource in compiled.resources:
            bounds = compiled.resources[resource].bounds
            if bounds[0] is not None:
                value = max(bounds[0], value)
            if bounds[1] is not None:
                value = min(bounds[1], value)
        new_resources = {**entity.resources, resource: value}
        return self.update_entity(entity_id, resources=new_resources)

    def adjust_resource(
        self,
        entity_id: str,
        resource: str,
        delta: int | float,
        compiled: CompiledGame | None = None,
    ) -> GameState:
        current = self.get_resource(entity_id, resource)
        return self.set_resource(entity_id, resource, current + delta, compiled)

    def set_attr(self, entity_id: str, attr: str, value: Any) -> GameState:
        entity = self.entities[entity_id]
        new_attrs = {**entity.attrs_, attr: value}
        return self.update_entity(entity_id, attrs_=new_attrs)

    def set_game_var(self, name: str, value: Any) -> GameState:
        new_vars = {**self.vars_, name: value}
        return attrs.evolve(self, vars_=new_vars)

    def deactivate(self, entity_id: str) -> GameState:
        return self.update_entity(entity_id, active=False)

    def reactivate(self, entity_id: str) -> GameState:
        return self.update_entity(entity_id, active=True)

    def add_group(self, group: Group) -> GameState:
        new_groups = {**self.groups, group.id: group}
        # Update entity memberships
        state = attrs.evolve(self, groups=new_groups)
        for member_id in group.members:
            entity = state.entities.get(member_id)
            if entity:
                new_entity_groups = entity.groups | {group.id}
                state = state.update_entity(member_id, groups=new_entity_groups)
        return state

    def add_relation(self, a: str, b: str, relation: str) -> GameState:
        key = (a, b)
        existing = self.relations.get(key, frozenset())
        new_relations = {**self.relations, key: existing | {relation}}
        return attrs.evolve(self, relations=new_relations)

    def add_reveal(
        self, observer: str, entity: str, attr: str, value: Any = True
    ) -> GameState:
        key = (observer, entity, attr)
        new_reveals = {**self.reveals, key: value}
        return attrs.evolve(self, reveals=new_reveals)

    def add_history(self, type: str, **data: Any) -> GameState:
        entry = HistoryEntry(type=type, data=data)
        return attrs.evolve(self, history=(*self.history, entry))

    def record_decision(self, decision: dict[str, Any]) -> GameState:
        return attrs.evolve(self, decisions=(*self.decisions, decision))

    def add_message(self, msg: Message) -> GameState:
        return attrs.evolve(self, messages=(*self.messages, msg))

    def next_id(self, prefix: str) -> tuple[GameState, str]:
        """Generate deterministic ID: deal-0, deal-1, vote-0, etc."""
        count = self.counters.get(prefix, 0)
        new_counters = {**self.counters, prefix: count + 1}
        return attrs.evolve(self, counters=new_counters), f"{prefix}-{count}"


# ---------------------------------------------------------------------------
# Standalone view generation (presentation logic, not data model)
# ---------------------------------------------------------------------------


def can_read_channel(
    state: GameState, observer_id: str, channel_id: str, compiled: CompiledGame
) -> bool:
    """Check if observer can read messages from a channel."""
    cdef = compiled.channels.get(channel_id)
    if cdef is None:
        if channel_id.startswith("private:"):
            parts = channel_id.split(":")
            return observer_id in parts[1:]
        return True

    match cdef.type:
        case "public" | "broadcast":
            return True
        case "group":
            if cdef.group:
                return state.entity_in_group(observer_id, cdef.group)
            return True
        case "private":
            if channel_id.startswith("private:"):
                parts = channel_id.split(":")
                return observer_id in parts[1:]
            # FIX-7: Named private channels (e.g. "backroom") —
            # readable by all active players (write_filter controls access)
            return state.is_active(observer_id)
        case _:
            return True


def can_write_channel(
    state: GameState, sender_id: str, channel_id: str, compiled: CompiledGame
) -> bool:
    """Check if sender can write to a channel in current phase.

    Mirrors the validation logic in GameRuntime.send_message() but as a pure
    predicate (no Result/error messages). Used by MCP tools to show permissions.
    """
    if not state.is_active(sender_id):
        return False

    cdef = compiled.channels.get(channel_id)
    if not cdef:
        # Ad-hoc private channels
        if channel_id.startswith("private:"):
            return sender_id in channel_id.split(":")[1:]
        return False

    # Phase filter
    if cdef.phase_filter and state.phase not in cdef.phase_filter:
        return False

    # Write filter (Expr)
    if cdef.write_filter is not None:
        from engine.expr.evaluator import Context, evaluate

        ctx = Context(state=state, compiled=compiled, bindings={"actor": sender_id})
        if not evaluate(cdef.write_filter, ctx):
            return False

    # Group channel membership
    if cdef.type == "group" and cdef.group:
        if not state.entity_in_group(sender_id, cdef.group):
            return False

    return True


SPECTATOR_ID = "__spectator__"


def _speech_act_view(sa: PendingSpeechAct) -> dict[str, Any]:
    """Convert a PendingSpeechAct to a view dict."""
    view: dict[str, Any] = {
        "instance_id": sa.instance_id,
        "speech_act_id": sa.speech_act_id,
        "act_type": sa.act_type,
        "actor": sa.actor,
        "status": sa.status,
        "round_created": sa.round_created,
        "phase_created": sa.phase_created,
    }
    if sa.target:
        view["target"] = sa.target
    if sa.params:
        view["params"] = sa.params
    if sa.endorsers:
        view["endorsers"] = list(sa.endorsers)
        view["endorsement_count"] = len(sa.endorsers)
    if sa.inquire_response is not None:
        view["inquire_response"] = sa.inquire_response
    return view


def view_for(
    state: GameState, observer_id: str, compiled: CompiledGame
) -> dict[str, Any]:
    """Generate filtered state view for an observer.

    Pure function — no dependency on GameState internals beyond public API.

    Rules:
    - PUBLIC: always visible
    - PRIVATE: visible only to owner (observer == entity)
    - HIDDEN: never visible
    - Reveals override visibility (True = real value, other = fake value)
    - Spectator (observer_id == SPECTATOR_ID): PUBLIC only, no reveals, public channels only
    """
    is_spectator = observer_id == SPECTATOR_ID
    entities = {}
    for eid, entity in state.entities.items():
        is_self = False if is_spectator else (observer_id == eid)

        visible_resources = {}
        for rid, val in entity.resources.items():
            rdef = compiled.resources.get(rid)
            vis = rdef.visibility if rdef else Visibility.PUBLIC
            if not is_spectator:
                rk = reveal_key(observer_id, eid, rid)
                if rk in state.reveals:
                    rv = state.reveals[rk]
                    visible_resources[rid] = val if rv is True else rv
                    continue
            pk = public_reveal_key(eid, rid)
            if pk in state.reveals:
                rv = state.reveals[pk]
                visible_resources[rid] = val if rv is True else rv
                continue
            if vis == Visibility.PUBLIC or (vis == Visibility.PRIVATE and is_self):
                visible_resources[rid] = val

        visible_attrs = {}
        for aid, val in entity.attrs_.items():
            adef = compiled.attrs_defs.get(aid)
            vis = adef.visibility if adef else Visibility.HIDDEN
            if not is_spectator:
                rk = reveal_key(observer_id, eid, aid)
                if rk in state.reveals:
                    rv = state.reveals[rk]
                    visible_attrs[aid] = val if rv is True else rv
                    continue
            pk = public_reveal_key(eid, aid)
            if pk in state.reveals:
                rv = state.reveals[pk]
                visible_attrs[aid] = val if rv is True else rv
                continue
            if vis == Visibility.PUBLIC or (vis == Visibility.PRIVATE and is_self):
                visible_attrs[aid] = val

        # Filter groups by visibility:
        # - visible=True groups: shown to everyone
        # - visible=False groups: shown only to self or fellow members
        visible_groups = []
        if not is_spectator:
            observer_entity = state.entities.get(observer_id)
            observer_groups = observer_entity.groups if observer_entity else frozenset()
            for gid in entity.groups:
                g = state.groups.get(gid)
                if g:
                    gtdef = compiled.group_types.get(g.type)
                    if is_self or (gtdef and gtdef.visible) or gid in observer_groups:
                        visible_groups.append(gid)
                else:
                    visible_groups.append(gid)
        else:
            for gid in entity.groups:
                g = state.groups.get(gid)
                if g:
                    gtdef = compiled.group_types.get(g.type)
                    if gtdef and gtdef.visible:
                        visible_groups.append(gid)

        # Collect PRIVATE fields hidden from this observer (not HIDDEN — those stay invisible)
        hidden_fields: list[str] = []
        if not is_self and not is_spectator:
            for rid in entity.resources:
                if rid not in visible_resources:
                    rdef = compiled.resources.get(rid)
                    if rdef and rdef.visibility == Visibility.PRIVATE:
                        hidden_fields.append(rid)
            for aid in entity.attrs_:
                if aid not in visible_attrs:
                    adef = compiled.attrs_defs.get(aid)
                    if adef and adef.visibility == Visibility.PRIVATE:
                        hidden_fields.append(aid)

        entry: dict[str, Any] = {
            "id": eid,
            "active": entity.active,
            "resources": visible_resources,
            "attrs": visible_attrs,
            "groups": sorted(visible_groups),
        }
        if hidden_fields:
            entry["hidden_fields"] = hidden_fields
        entities[eid] = entry

    visible_messages = []
    for msg in state.messages:
        if can_read_channel(state, observer_id, msg.channel, compiled):
            visible_messages.append(
                {
                    "id": msg.id,
                    "channel": msg.channel,
                    "sender": msg.sender,
                    "content": msg.content,
                    "round": msg.round,
                    "phase": msg.phase,
                }
            )

    # Speech acts — filter by visibility (both pending and resolved)
    visible_speech_acts = []
    all_acts = list(state.pending_speech_acts.values()) + list(
        state.resolved_speech_acts
    )
    for sa in all_acts:
        sa_def = compiled.speech_acts.get(sa.speech_act_id)
        vis = sa_def.visibility if sa_def else "public"
        if vis == "public":
            visible_speech_acts.append(_speech_act_view(sa))
        elif not is_spectator and vis == "private" and observer_id in (sa.actor, sa.target):
            visible_speech_acts.append(_speech_act_view(sa))

    return {
        "round": state.round,
        "phase": state.phase,
        "status": state.status,
        "entities": entities,
        "messages": visible_messages,
        "speech_acts": visible_speech_acts,
        "vars": {k: v for k, v in state.vars_.items() if not k.startswith("_")},
    }
