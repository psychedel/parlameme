"""Effect system — tagged union with open registry dispatch.

Effects are frozen dataclasses. Handlers are registered via @register_effect.
Games can add custom effects without editing this file.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import attrs

from engine.expr.core import Expr
from engine.expr.evaluator import Context, evaluate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Effect registry
# ---------------------------------------------------------------------------

_effect_handlers: dict[type, Callable] = {}


def register_effect(effect_type: type):
    """Decorator to register an effect handler."""

    def decorator(fn: Callable) -> Callable:
        _effect_handlers[effect_type] = fn
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Effect types (tagged union)
# ---------------------------------------------------------------------------

# -- Resource effects --


@dataclass(frozen=True, slots=True)
class Transfer:
    source: str | Expr
    target: str | Expr
    resource: str | Expr
    amount: int | float | Expr


@dataclass(frozen=True, slots=True)
class Boost:
    entity: str | Expr
    resource: str | Expr
    amount: int | float | Expr


@dataclass(frozen=True, slots=True)
class Damage:
    entity: str | Expr
    resource: str | Expr
    amount: int | float | Expr


@dataclass(frozen=True, slots=True)
class SetResource:
    entity: str | Expr
    resource: str | Expr
    value: int | float | Expr


# -- Entity effects --


@dataclass(frozen=True, slots=True)
class Eliminate:
    entity: str | Expr


@dataclass(frozen=True, slots=True)
class Reactivate:
    entity: str | Expr


@dataclass(frozen=True, slots=True)
class SetAttr:
    entity: str | Expr
    attr: str
    value: Any


@dataclass(frozen=True, slots=True)
class SetAdd:
    """Add value to a set-typed attribute."""

    entity: str | Expr
    attr: str
    value: Any


@dataclass(frozen=True, slots=True)
class SetRemove:
    """Remove value from a set-typed attribute."""

    entity: str | Expr
    attr: str
    value: Any


# -- Relation effects --


@dataclass(frozen=True, slots=True)
class Relate:
    source: str | Expr
    target: str | Expr
    relation: str


@dataclass(frozen=True, slots=True)
class Unrelate:
    source: str | Expr
    target: str | Expr
    relation: str


# -- Group effects --


@dataclass(frozen=True, slots=True)
class CreateGroup:
    type: str
    members: Expr | list[str] | None = None
    name: str = ""


@dataclass(frozen=True, slots=True)
class JoinGroup:
    entity: str | Expr
    group: str


@dataclass(frozen=True, slots=True)
class LeaveGroup:
    entity: str | Expr
    group: str


@dataclass(frozen=True, slots=True)
class DissolveGroup:
    group: str


# -- Variable effects --


@dataclass(frozen=True, slots=True)
class SetVar:
    name: str
    value: Any


@dataclass(frozen=True, slots=True)
class UpdateVar:
    """Nested var mutation — navigate path, apply operation on leaf."""

    path: tuple[str, ...]  # nested key path into vars_
    operation: str  # "set"|"append"|"remove"|"remove_where"|"increment"|"decrement"|"append_max"|"sort_by"|"clear"|"prepend"
    value: Any = None  # operand (can be Expr)
    key: str | None = None  # for remove_where/sort_by: field name


# -- Communication effects --


@dataclass(frozen=True, slots=True)
class Broadcast:
    template: str


@dataclass(frozen=True, slots=True)
class Notify:
    entity: str | Expr
    template: str


@dataclass(frozen=True, slots=True)
class Emit:
    event: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendMessage:
    """Send a message to a channel — recorded in GameState for replay."""

    channel: str  # channel id
    sender: str  # entity ref (e.g., "actor")
    content: str  # text or template with {actor}, {target}, etc.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Reveal:
    entity: str | Expr
    attr: str
    to: str | Expr
    fake: Any = None  # None means real value; non-None means deception


# -- Stake effects --


@dataclass(frozen=True, slots=True)
class ReturnStakes:
    pass


@dataclass(frozen=True, slots=True)
class TransferStakes:
    to: str


@dataclass(frozen=True, slots=True)
class TransferStakesSplit:
    to_key: str


@dataclass(frozen=True, slots=True)
class BurnStakes:
    pass


# -- Control flow (meta-effects) --


@dataclass(frozen=True, slots=True)
class When:
    condition: Expr
    effects: tuple


@dataclass(frozen=True, slots=True)
class Each:
    binding: str
    filter: Expr
    effects: tuple


@dataclass(frozen=True, slots=True)
class Let:
    bindings: dict[str, Expr]
    effects: tuple


@dataclass(frozen=True, slots=True)
class Cond:
    """Multi-branch conditional: [(guard, effects), ..., (None, default)]."""

    branches: tuple  # tuple of (Expr | None, tuple[Effect, ...])


@dataclass(frozen=True, slots=True)
class Maybe:
    """Probabilistic effect — uses deterministic RNG."""

    probability: float
    effects: tuple


@dataclass(frozen=True, slots=True)
class Repeat:
    """Execute effects N times."""

    times: int | Expr
    effects: tuple


# -- Speech act effects --


@dataclass(frozen=True, slots=True)
class VerifySpeechAct:
    """Force-verify a specific pending speech act."""

    instance_id: str
    result: str  # "true" | "false"


@dataclass(frozen=True, slots=True)
class ResolveSpeechActs:
    """Resolve all pending speech acts matching a trigger."""

    trigger: str  # "eliminate" | "phase_change" | "game_end"
    entity: str = ""  # for eliminate trigger


# -- Setup effects --


@dataclass(frozen=True, slots=True)
class AssignRoles:
    pass


@dataclass(frozen=True, slots=True)
class SetupVisibility:
    pass


@dataclass(frozen=True, slots=True)
class ResolveMarked:
    pass


# Union type for documentation
Effect = (
    Transfer
    | Boost
    | Damage
    | SetResource
    | Eliminate
    | Reactivate
    | SetAttr
    | SetAdd
    | SetRemove
    | Relate
    | Unrelate
    | CreateGroup
    | JoinGroup
    | LeaveGroup
    | DissolveGroup
    | SetVar
    | UpdateVar
    | Broadcast
    | Notify
    | Emit
    | SendMessage
    | Reveal
    | ReturnStakes
    | TransferStakes
    | TransferStakesSplit
    | BurnStakes
    | When
    | Each
    | Let
    | Cond
    | Maybe
    | Repeat
    | VerifySpeechAct
    | ResolveSpeechActs
    | AssignRoles
    | SetupVisibility
    | ResolveMarked
)


# ---------------------------------------------------------------------------
# Core dispatch
# ---------------------------------------------------------------------------


_STATIC_ALIASES: dict[str, str] = {
    "self": "actor",
    "giver": "proposer",
    "receiver": "responder",
    "sender": "proposer",
    "buyer": "proposer",
    "seller": "responder",
}

# Cache: compiled game id → merged alias dict
_compiled_aliases_cache: dict[str, dict[str, str]] = {}


def _get_entity_aliases(ctx: Context) -> dict[str, str]:
    """Build entity alias map: static defaults + dynamic from compiled game parties.

    Party names in deals map to their canonical binding (first party = 'proposer',
    second = 'responder', etc.). This lets games use custom party names like
    'attacker'/'defender' in effects and have them resolve correctly.
    """
    compiled = ctx.compiled
    if compiled is None:
        return _STATIC_ALIASES

    cache_key = id(compiled)
    cached = _compiled_aliases_cache.get(cache_key)
    if cached is not None:
        return cached

    aliases = dict(_STATIC_ALIASES)
    # Map custom party names → canonical bindings
    _CANONICAL_ROLES = ("actor", "proposer", "responder", "target")
    for deal in compiled.deals.values():
        for party_name in deal.parties:
            if party_name not in aliases and party_name not in _CANONICAL_ROLES:
                # Infer canonical role from party position/properties
                party = deal.parties[party_name]
                if party.excludes:
                    aliases[party_name] = "responder"
                elif party.count:
                    aliases[party_name] = "responders"

    _compiled_aliases_cache[cache_key] = aliases
    return aliases


def _resolve_entity(ref: str | Expr, ctx: Context) -> str:
    """Resolve entity reference from context bindings, aliases, or Expr."""
    if isinstance(ref, Expr):
        result = evaluate(ref, ctx)
        if result is None:
            logger.warning("Entity Expr resolved to None: %r", ref)
            return ""
        return str(result)
    val = ctx.bindings.get(ref)
    if val and isinstance(val, str):
        return val
    aliases = _get_entity_aliases(ctx)
    alias = aliases.get(ref)
    if alias:
        val = ctx.bindings.get(alias)
        if val and isinstance(val, str):
            return val
    # ref might be a literal entity ID (e.g. "p0") — return as-is
    return ref


def _resolve_amount(amount: int | float | str | Expr, ctx: Context) -> float:
    """Resolve amount — number, expression, or param string ref."""
    if isinstance(amount, Expr):
        result = evaluate(amount, ctx)
        if result is None:
            logger.warning("Amount Expr resolved to None: %r", amount)
            return 0.0
        return float(result)
    if isinstance(amount, str):
        params = ctx.bindings.get("params", {})
        if amount in params:
            return float(params[amount])
        logger.warning("Amount param '%s' not found in params: %s", amount, list(params.keys()))
        return 0.0
    return float(amount)


def _resolve_value(value: Any, ctx: Context) -> Any:
    """Resolve a value: Expr → evaluate, string → check params, else literal."""
    if isinstance(value, Expr):
        return evaluate(value, ctx)
    if isinstance(value, str):
        params = ctx.bindings.get("params", {})
        if value in params:
            return params[value]
    return value


def _resolve_resource(resource: str | Expr, ctx: Context) -> str:
    """Resolve resource name — may be Expr for dynamic resource effects."""
    if isinstance(resource, Expr):
        return str(evaluate(resource, ctx))
    return resource


def apply_effect(effect, state, ctx: Context):
    """Apply a single effect to state, return new state."""
    handler = _effect_handlers.get(type(effect))
    if handler is None:
        raise ValueError(f"No handler for effect: {type(effect).__name__}")
    return handler(effect, state, ctx)


def apply_effects(effects: tuple | list, state, ctx: Context):
    """Reduce effects over state, keeping ctx.state in sync."""
    for effect in effects:
        # Update context state so expressions evaluate against current state
        ctx = Context(state=state, compiled=ctx.compiled, bindings=ctx.bindings)
        state = apply_effect(effect, state, ctx)
    return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TEMPLATE_RE = re.compile(r"\{(\w+(?:\.\w+)*)\}")


def _format_template(template: str, ctx: Context) -> str:
    """Simple template substitution: {actor}, {target.role}, etc."""

    def replacer(match):
        path = match.group(1).split(".")
        val = ctx.resolve_path(tuple(path))
        return str(val) if val is not None else match.group(0)

    return _TEMPLATE_RE.sub(replacer, template)


def _return_stakes(state, ctx: Context):
    stakes = ctx.bindings.get("_stakes", {})
    for party, locked in stakes.items():
        eid = _resolve_entity(party, ctx)
        for resource, amount in locked:
            state = state.adjust_resource(eid, resource, amount, ctx.compiled)
    return state


def _transfer_stakes(state, ctx: Context, to_ref: str):
    stakes = ctx.bindings.get("_stakes", {})
    recipient = _resolve_entity(to_ref, ctx)
    for party, locked in stakes.items():
        for resource, amount in locked:
            state = state.adjust_resource(recipient, resource, amount, ctx.compiled)
    return state


def _split_stakes(state, ctx: Context, to_key: str):
    stakes = ctx.bindings.get("_stakes", {})
    recipients = ctx.bindings.get(to_key, [])
    if not recipients:
        return _return_stakes(state, ctx)
    n = len(recipients)
    for party, locked in stakes.items():
        for resource, amount in locked:
            share = amount / n
            for r in recipients:
                rid = _resolve_entity(r, ctx)
                state = state.adjust_resource(rid, resource, share, ctx.compiled)
    return state


# ---------------------------------------------------------------------------
# Handler registrations
# ---------------------------------------------------------------------------

# -- Resource --


@register_effect(Transfer)
def _apply_transfer(effect, state, ctx):
    src = _resolve_entity(effect.source, ctx)
    tgt = _resolve_entity(effect.target, ctx)
    amt = _resolve_amount(effect.amount, ctx)
    resource = _resolve_resource(effect.resource, ctx)
    state = state.adjust_resource(src, resource, -amt, ctx.compiled)
    state = state.adjust_resource(tgt, resource, amt, ctx.compiled)
    return state


@register_effect(Boost)
def _apply_boost(effect, state, ctx):
    eid = _resolve_entity(effect.entity, ctx)
    amt = _resolve_amount(effect.amount, ctx)
    resource = _resolve_resource(effect.resource, ctx)
    return state.adjust_resource(eid, resource, amt, ctx.compiled)


@register_effect(Damage)
def _apply_damage(effect, state, ctx):
    eid = _resolve_entity(effect.entity, ctx)
    amt = _resolve_amount(effect.amount, ctx)
    resource = _resolve_resource(effect.resource, ctx)
    return state.adjust_resource(eid, resource, -amt, ctx.compiled)


@register_effect(SetResource)
def _apply_set_resource(effect, state, ctx):
    eid = _resolve_entity(effect.entity, ctx)
    val = _resolve_amount(effect.value, ctx)
    resource = _resolve_resource(effect.resource, ctx)
    return state.set_resource(eid, resource, val, ctx.compiled)


# -- Entity --


@register_effect(Eliminate)
def _apply_eliminate(effect, state, ctx):
    eid = _resolve_entity(effect.entity, ctx)
    state = state.deactivate(eid)
    state = state.add_history(
        "entity_deactivated",
        entity_id=eid,
        round=state.round,
        phase=state.phase,
    )
    # Fire commitments via callback (set by runtime)
    on_eliminate = ctx.bindings.get("_on_eliminate")
    if on_eliminate:
        state = on_eliminate(eid, state)
    return state


@register_effect(Reactivate)
def _apply_reactivate(effect, state, ctx):
    eid = _resolve_entity(effect.entity, ctx)
    return state.reactivate(eid)


@register_effect(SetAttr)
def _apply_set_attr(effect, state, ctx):
    eid = _resolve_entity(effect.entity, ctx)
    val = _resolve_value(effect.value, ctx)
    return state.set_attr(eid, attr=effect.attr, value=val)


@register_effect(SetAdd)
def _apply_set_add(effect, state, ctx):
    eid = _resolve_entity(effect.entity, ctx)
    val = (
        evaluate(effect.value, ctx) if isinstance(effect.value, Expr) else effect.value
    )
    current = state.get_attr(eid, effect.attr)
    if isinstance(current, frozenset):
        new_val = current | {val}
    elif isinstance(current, (set, list, tuple)):
        new_val = frozenset(current) | {val}
    else:
        new_val = frozenset({val})
    return state.set_attr(eid, effect.attr, new_val)


@register_effect(SetRemove)
def _apply_set_remove(effect, state, ctx):
    eid = _resolve_entity(effect.entity, ctx)
    val = (
        evaluate(effect.value, ctx) if isinstance(effect.value, Expr) else effect.value
    )
    current = state.get_attr(eid, effect.attr)
    if isinstance(current, frozenset):
        new_val = current - {val}
    elif isinstance(current, (set, list, tuple)):
        new_val = frozenset(current) - {val}
    else:
        new_val = frozenset()
    return state.set_attr(eid, effect.attr, new_val)


# -- Relations --


@register_effect(Relate)
def _apply_relate(effect, state, ctx):
    src = _resolve_entity(effect.source, ctx)
    tgt = _resolve_entity(effect.target, ctx)
    return state.add_relation(src, tgt, effect.relation)


@register_effect(Unrelate)
def _apply_unrelate(effect, state, ctx):
    src = _resolve_entity(effect.source, ctx)
    tgt = _resolve_entity(effect.target, ctx)
    key = (src, tgt)
    existing = state.relations.get(key, frozenset())
    new_rels = {**state.relations, key: existing - {effect.relation}}
    return attrs.evolve(state, relations=new_rels)


# -- Groups --


@register_effect(CreateGroup)
def _apply_create_group(effect, state, ctx):
    from engine.runtime.state import Group

    state, gid = state.next_id(effect.type)
    if isinstance(effect.members, Expr):
        member_ids = [
            eid
            for eid in state.get_active_entity_ids()
            if evaluate(effect.members, ctx.with_binding("actor", eid))
        ]
        member_set = frozenset(member_ids)
    elif isinstance(effect.members, list):
        member_set = frozenset(_resolve_entity(m, ctx) for m in effect.members)
    else:
        member_set = frozenset()
    group = Group(
        id=gid, type=effect.type, members=member_set, name=effect.name or effect.type
    )
    return state.add_group(group)


@register_effect(JoinGroup)
def _apply_join_group(effect, state, ctx):
    eid = _resolve_entity(effect.entity, ctx)
    g = state.groups.get(effect.group)
    if not g:
        for g2 in state.groups.values():
            if g2.type == effect.group:
                g = g2
                break
    if g:
        new_group = attrs.evolve(g, members=g.members | {eid})
        new_groups = {**state.groups, g.id: new_group}
        state = attrs.evolve(state, groups=new_groups)
        entity_obj = state.entities[eid]
        state = state.update_entity(eid, groups=entity_obj.groups | {g.id})
    return state


@register_effect(LeaveGroup)
def _apply_leave_group(effect, state, ctx):
    eid = _resolve_entity(effect.entity, ctx)
    g = state.groups.get(effect.group)
    if g:
        new_group = attrs.evolve(g, members=g.members - {eid})
        new_groups = {**state.groups, g.id: new_group}
        state = attrs.evolve(state, groups=new_groups)
        entity_obj = state.entities[eid]
        state = state.update_entity(eid, groups=entity_obj.groups - {g.id})
    return state


@register_effect(DissolveGroup)
def _apply_dissolve_group(effect, state, ctx):
    g = state.groups.get(effect.group)
    if g:
        for mid in g.members:
            entity_obj = state.entities.get(mid)
            if entity_obj:
                state = state.update_entity(mid, groups=entity_obj.groups - {g.id})
        new_groups = {k: v for k, v in state.groups.items() if k != g.id}
        state = attrs.evolve(state, groups=new_groups)
    return state


# -- Variables --


@register_effect(SetVar)
def _apply_set_var(effect, state, ctx):
    val = _resolve_value(effect.value, ctx)
    return state.set_game_var(effect.name, val)


@register_effect(UpdateVar)
def _apply_update_var(effect, state, ctx):
    import copy

    path = effect.path
    if not path:
        return state
    val = _resolve_value(effect.value, ctx) if effect.value is not None else None

    # Resolve any Expr elements in path
    resolved_path = []
    for p in path:
        if isinstance(p, Expr):
            resolved_path.append(str(evaluate(p, ctx)))
        else:
            resolved_path.append(p)

    # Deep-copy current vars and navigate to parent
    new_vars = copy.deepcopy(dict(state.vars_))
    parent = new_vars
    for key in resolved_path[:-1]:
        if isinstance(parent, dict):
            if key not in parent:
                parent[key] = {}
            parent = parent[key]
        elif isinstance(parent, list):
            try:
                parent = parent[int(key)]
            except (ValueError, IndexError):
                return state
        else:
            return state

    leaf = resolved_path[-1]
    op = effect.operation

    if op == "set":
        if isinstance(parent, dict):
            parent[leaf] = val
        elif isinstance(parent, list):
            try:
                parent[int(leaf)] = val
            except (ValueError, IndexError):
                return state
    elif op == "append":
        target = parent.get(leaf) if isinstance(parent, dict) else None
        if target is None:
            target = []
        if isinstance(target, list):
            target.append(val)
            if isinstance(parent, dict):
                parent[leaf] = target
    elif op == "prepend":
        target = parent.get(leaf) if isinstance(parent, dict) else None
        if target is None:
            target = []
        if isinstance(target, list):
            target.insert(0, val)
            if isinstance(parent, dict):
                parent[leaf] = target
    elif op == "append_max":
        # Append with max length: value is the item, key is max size as str
        target = parent.get(leaf) if isinstance(parent, dict) else None
        if target is None:
            target = []
        if isinstance(target, list):
            target.append(val)
            max_size = int(effect.key) if effect.key else len(target)
            if len(target) > max_size:
                target = target[-max_size:]
            if isinstance(parent, dict):
                parent[leaf] = target
    elif op == "remove":
        target = parent.get(leaf) if isinstance(parent, dict) else None
        if isinstance(target, list) and val in target:
            target.remove(val)
            if isinstance(parent, dict):
                parent[leaf] = target
    elif op == "remove_where":
        target = parent.get(leaf) if isinstance(parent, dict) else None
        field_name = effect.key
        if isinstance(target, list) and field_name:
            parent[leaf] = [
                item
                for item in target
                if not (isinstance(item, dict) and item.get(field_name) == val)
            ]
    elif op == "increment":
        current = parent.get(leaf, 0) if isinstance(parent, dict) else 0
        amount = float(val) if val is not None else 1
        if isinstance(parent, dict):
            parent[leaf] = current + amount
    elif op == "decrement":
        current = parent.get(leaf, 0) if isinstance(parent, dict) else 0
        amount = float(val) if val is not None else 1
        if isinstance(parent, dict):
            parent[leaf] = current - amount
    elif op == "sort_by":
        target = parent.get(leaf) if isinstance(parent, dict) else None
        field_name = effect.key
        if isinstance(target, list) and field_name:
            reverse = val == "desc" if val else False
            parent[leaf] = sorted(
                target,
                key=lambda item: (
                    item.get(field_name, 0) if isinstance(item, dict) else 0
                ),
                reverse=reverse,
            )
    elif op == "clear":
        if isinstance(parent, dict):
            parent[leaf] = [] if isinstance(parent.get(leaf), list) else {}
    else:
        return state

    return attrs.evolve(state, vars_=new_vars)


# -- Communication --


@register_effect(Broadcast)
def _apply_broadcast(effect, state, ctx):
    msg = _format_template(effect.template, ctx)
    return state.add_history("broadcast", message=msg)


@register_effect(Notify)
def _apply_notify(effect, state, ctx):
    eid = _resolve_entity(effect.entity, ctx)
    msg = _format_template(effect.template, ctx)
    return state.add_history("notify", entity=eid, message=msg)


@register_effect(Emit)
def _apply_emit(effect, state, ctx):
    return state.add_history("event", event=effect.event, data=effect.data)


@register_effect(SendMessage)
def _apply_send_message(effect, state, ctx):
    from engine.runtime.state import Message

    sender_id = _resolve_entity(effect.sender, ctx)
    content = _format_template(effect.content, ctx)
    state, msg_id = state.next_id("msg")
    msg = Message(
        id=msg_id,
        channel=effect.channel,
        sender=sender_id,
        content=content,
        round=state.round,
        phase=state.phase,
        metadata=effect.metadata,
    )
    state = state.add_message(msg)
    state = state.record_decision(
        {
            "type": "message",
            "channel": effect.channel,
            "sender": sender_id,
            "content": content,
        }
    )
    # Apply channel effects (e.g., whisper → +suspicion)
    cdef = ctx.compiled.channels.get(effect.channel) if ctx.compiled else None
    if cdef and cdef.effects:
        state = apply_effects(cdef.effects, state, ctx)
    return state


@register_effect(Reveal)
def _apply_reveal(effect, state, ctx):
    eid = _resolve_entity(effect.entity, ctx)
    observer = _resolve_entity(effect.to, ctx)
    value = effect.fake if effect.fake is not None else True
    return state.add_reveal(observer, eid, effect.attr, value)


# -- Stakes --


@register_effect(ReturnStakes)
def _apply_return_stakes(effect, state, ctx):
    return _return_stakes(state, ctx)


@register_effect(TransferStakes)
def _apply_transfer_stakes(effect, state, ctx):
    return _transfer_stakes(state, ctx, effect.to)


@register_effect(TransferStakesSplit)
def _apply_split_stakes(effect, state, ctx):
    return _split_stakes(state, ctx, effect.to_key)


@register_effect(BurnStakes)
def _apply_burn_stakes(effect, state, ctx):
    return state.add_history("stakes_burned")


# -- Control flow --


@register_effect(When)
def _apply_when(effect, state, ctx):
    if evaluate(effect.condition, ctx):
        return apply_effects(effect.effects, state, ctx)
    return state


@register_effect(Each)
def _apply_each(effect, state, ctx):
    for eid in state.get_active_entity_ids():
        if evaluate(effect.filter, ctx.with_binding("actor", eid)):
            new_ctx = ctx.with_binding(effect.binding, eid)
            state = apply_effects(effect.effects, state, new_ctx)
    return state


@register_effect(Let)
def _apply_let(effect, state, ctx):
    new_ctx = ctx
    for name, expr in effect.bindings.items():
        val = evaluate(expr, new_ctx)
        new_ctx = new_ctx.with_binding(name, val)
    return apply_effects(effect.effects, state, new_ctx)


@register_effect(Cond)
def _apply_cond(effect, state, ctx):
    for guard, effects in effect.branches:
        if guard is None or evaluate(guard, ctx):
            return apply_effects(effects, state, ctx)
    return state


@register_effect(Maybe)
def _apply_maybe(effect, state, ctx):
    from engine.runtime.rng import DeterministicRNG

    rng = DeterministicRNG(state.rng_state or state.seed)
    roll, rng = rng.next_float()
    state = attrs.evolve(state, rng_state=rng.state)
    if roll < effect.probability:
        return apply_effects(effect.effects, state, ctx)
    return state


@register_effect(Repeat)
def _apply_repeat(effect, state, ctx):
    times = effect.times
    if isinstance(times, Expr):
        times = int(evaluate(times, ctx) or 0)
    for _ in range(times):
        state = apply_effects(effect.effects, state, ctx)
    return state


# -- Speech Acts --


def _verify_pending_speech_act(state, pending, verified_true, ctx):
    """Shared logic: apply verify effects, endorser shared-fate, move to resolved."""
    sa_def = ctx.compiled.speech_acts.get(pending.speech_act_id)
    if not sa_def:
        # No definition — just move to resolved without effects
        status = "verified_true" if verified_true else "verified_false"
        resolved = attrs.evolve(pending, status=status)
        new_pending = {
            k: v
            for k, v in state.pending_speech_acts.items()
            if k != pending.instance_id
        }
        return attrs.evolve(
            state,
            pending_speech_acts=new_pending,
            resolved_speech_acts=(*state.resolved_speech_acts, resolved),
        )

    effects = (
        sa_def.verify_true_effects if verified_true else sa_def.verify_false_effects
    )
    if effects:
        actor_ctx = Context(
            state=state,
            compiled=ctx.compiled,
            bindings={"actor": pending.actor, "target": pending.target or ""},
        )
        state = apply_effects(effects, state, actor_ctx)

        # Shared fate for endorsers
        for endorser_id in pending.endorsers:
            if state.is_active(endorser_id):
                endorser_ctx = Context(
                    state=state,
                    compiled=ctx.compiled,
                    bindings={"actor": endorser_id, "target": pending.target or ""},
                )
                state = apply_effects(effects, state, endorser_ctx)

    status = "verified_true" if verified_true else "verified_false"
    resolved = attrs.evolve(pending, status=status)
    new_pending = {
        k: v for k, v in state.pending_speech_acts.items() if k != pending.instance_id
    }
    state = attrs.evolve(
        state,
        pending_speech_acts=new_pending,
        resolved_speech_acts=(*state.resolved_speech_acts, resolved),
    )
    return state.add_history(
        "speech_act_verified",
        instance_id=pending.instance_id,
        speech_act_id=pending.speech_act_id,
        result=status,
        actor=pending.actor,
    )


@register_effect(VerifySpeechAct)
def _apply_verify_speech_act(effect, state, ctx):
    """Force-verify a specific speech act with given result.

    Applies verify_true/false effects + endorser shared-fate.
    """
    pending = state.pending_speech_acts.get(effect.instance_id)
    if not pending:
        return state
    verified_true = effect.result == "true"
    return _verify_pending_speech_act(state, pending, verified_true, ctx)


@register_effect(ResolveSpeechActs)
def _apply_resolve_speech_acts(effect, state, ctx):
    """Resolve all pending speech acts matching a trigger.

    Evaluates verify_condition for each and applies effects + shared-fate.
    """
    entity = _resolve_entity(effect.entity, ctx) if effect.entity else ""
    to_verify = []
    for sa in state.pending_speech_acts.values():
        if sa.status != "pending":
            continue
        sa_def = ctx.compiled.speech_acts.get(sa.speech_act_id)
        if not sa_def:
            continue
        if effect.trigger not in sa_def.verify_triggers:
            continue
        if effect.trigger == "eliminate" and entity:
            if entity in (sa.actor, sa.target):
                to_verify.append(sa)
        else:
            to_verify.append(sa)

    for sa in to_verify:
        current = state.pending_speech_acts.get(sa.instance_id)
        if not current or current.status != "pending":
            continue
        sa_def = ctx.compiled.speech_acts.get(current.speech_act_id)
        # Evaluate verify_condition
        verified_true = False
        if sa_def and sa_def.verify_condition is not None:
            verify_ctx = Context(
                state=state,
                compiled=ctx.compiled,
                bindings={
                    "actor": current.actor,
                    "target": current.target or "",
                    "params": current.params,
                },
            )
            verified_true = bool(evaluate(sa_def.verify_condition, verify_ctx))
        state = _verify_pending_speech_act(state, current, verified_true, ctx)

    return state


# -- Setup --


@register_effect(AssignRoles)
def _apply_assign_roles(effect, state, ctx):
    return _assign_roles(state, ctx)


@register_effect(SetupVisibility)
def _apply_setup_visibility(effect, state, ctx):
    return _setup_visibility(state, ctx)


@register_effect(ResolveMarked)
def _apply_resolve_marked(effect, state, ctx):
    return _resolve_marked(state, ctx)


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def _assign_roles(state, ctx: Context):
    """Assign roles from compiled game definition using deterministic RNG."""
    from engine.runtime.rng import DeterministicRNG

    compiled = ctx.compiled
    if not compiled.roles:
        return state

    rng = DeterministicRNG(state.rng_state or state.seed)
    players = list(state.entities.keys())
    n = len(players)

    role_pool = []
    filler_role = None
    for role_def in compiled.roles.values():
        if role_def.min_players and n < role_def.min_players:
            continue
        if role_def.filler:
            filler_role = role_def
            continue
        for _ in range(role_def.count):
            role_pool.append(role_def)

    remaining = n - len(role_pool)
    if filler_role and remaining > 0:
        for _ in range(remaining):
            role_pool.append(filler_role)

    role_pool, rng = rng.shuffle(role_pool)

    for player_id, role_def in zip(players, role_pool):
        state = state.set_attr(player_id, "role", role_def.id)
        state = state.set_attr(player_id, "team", role_def.team)
        if role_def.appears_as:
            state = state.set_attr(player_id, "appears_as", role_def.appears_as)

    # Distribute attrs with values + distribute=True (e.g. hidden_type)
    for attr_def in compiled.attrs_defs.values():
        if attr_def.distribute and attr_def.values:
            for player_id in players:
                idx, rng = rng.next_range(len(attr_def.values))
                state = state.set_attr(player_id, attr_def.id, attr_def.values[idx])

    state = attrs.evolve(state, rng_state=rng.state)
    return state


def _setup_visibility(state, ctx: Context):
    """Grant team visibility — teammates can see each other's team membership.

    Only reveals 'team', not 'role'. Roles stay private — special abilities
    (like detective's investigate) grant targeted reveals.
    """
    teams: dict[str, list[str]] = {}
    for eid, entity in state.entities.items():
        team = entity.get_attr("team")
        if team:
            teams.setdefault(team, []).append(eid)

    for team_members in teams.values():
        for a in team_members:
            for b in team_members:
                if a != b:
                    state = state.add_reveal(a, b, "team", True)
    return state


def _resolve_marked(state, ctx: Context):
    """Eliminate entities with 'marked' attr (unless protected).

    Uses the Eliminate effect handler (not raw deactivate) so that
    commitment callbacks (_on_eliminate) fire correctly.
    """
    for eid in list(state.get_active_entity_ids()):
        if state.get_attr(eid, "marked"):
            protected = state.get_attr(eid, "protected")
            if not protected:
                state = _apply_eliminate(Eliminate(entity=eid), state, ctx)
            state = state.set_attr(eid, "marked", False)
    return state
