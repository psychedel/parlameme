"""Mechanical description utilities — convert effect/expr trees to human-readable text.

Used by deal_mechanics tool and enriched game_rules to expose how game
mechanics actually work. Provides FACTS, not tips.
"""

from __future__ import annotations

from typing import Any

from engine.expr.core import And, Arith, Call, Cmp, Expr, If, Lit, Not, Or, Ref
from engine.runtime.effects import (
    Boost,
    Broadcast,
    BurnStakes,
    Cond,
    CreateGroup,
    Damage,
    DissolveGroup,
    Each,
    Eliminate,
    Emit,
    JoinGroup,
    LeaveGroup,
    Let,
    Maybe,
    Notify,
    Reactivate,
    Relate,
    Repeat,
    ResolveSpeechActs,
    ReturnStakes,
    Reveal,
    SendMessage,
    SetAttr,
    SetResource,
    SetVar,
    Transfer,
    TransferStakes,
    TransferStakesSplit,
    Unrelate,
    UpdateVar,
    VerifySpeechAct,
    When,
)
from engine.runtime.state import DealDef, OutcomeDef, SpeechActDef, VoteDef

# ---------------------------------------------------------------------------
# Expr → human-readable text
# ---------------------------------------------------------------------------


def describe_expr(expr: Any) -> str:
    """Convert an Expr AST node to readable text. Best-effort."""
    if expr is None:
        return "always"
    if not isinstance(expr, Expr):
        return repr(expr)

    match expr:
        case Lit(value=v):
            if isinstance(v, str):
                return f'"{v}"'
            if v is None:
                return "none"
            if v is True:
                return "true"
            if v is False:
                return "false"
            return str(v)

        case Ref(parts=parts):
            return ".".join(parts)

        case Cmp(op=op, lhs=lhs, rhs=rhs):
            op_words = {
                "==": "is",
                "!=": "is not",
                ">": ">",
                ">=": ">=",
                "<": "<",
                "<=": "<=",
            }
            return f"{describe_expr(lhs)} {op_words.get(op, op)} {describe_expr(rhs)}"

        case Arith(op=op, lhs=lhs, rhs=rhs):
            return f"{describe_expr(lhs)} {op} {describe_expr(rhs)}"

        case And(exprs=exprs):
            return " and ".join(describe_expr(e) for e in exprs)

        case Or(exprs=exprs):
            return " or ".join(describe_expr(e) for e in exprs)

        case Not(inner=inner):
            return f"not ({describe_expr(inner)})"

        case Call(fn=fn, args=args):
            if fn == "alive?" and not args:
                return "alive"
            if fn == "dead?" and not args:
                return "dead"
            if fn == "alive?" and len(args) == 1:
                return f"{describe_expr(args[0])} is alive"
            if fn == "dead?" and len(args) == 1:
                return f"{describe_expr(args[0])} is dead"
            if fn == "count_where" and len(args) == 1:
                return f"count({describe_expr(args[0])})"
            if fn == "has_relation" and len(args) >= 2:
                return f"has relation {describe_expr(args[1])}"
            if fn == "in_group" and len(args) >= 1:
                return f"in group {describe_expr(args[0])}"
            if fn == "resource_of" and len(args) == 2:
                return f"{describe_expr(args[0])}'s {describe_expr(args[1])}"
            # Try registry doc for unknown functions
            from engine.expr.registry import fn_registry

            args_str = ", ".join(describe_expr(a) for a in args)
            if fn_registry.has(fn):
                fdef = fn_registry._fns[fn]
                if fdef.doc:
                    return f"{fdef.doc}({args_str})" if args else fdef.doc
            return f"{fn}({args_str})"

        case If(condition=cond, then_=then, else_=else_):
            return (
                f"if {describe_expr(cond)} then {describe_expr(then)} "
                f"else {describe_expr(else_)}"
            )

    return repr(expr)


# ---------------------------------------------------------------------------
# Effect → human-readable text
# ---------------------------------------------------------------------------

# Effects to skip in descriptions (engine internals, not player-facing)
_SKIP_TYPES = (Emit, Notify)


def _resolve_amount(amount: Any) -> str:
    """Format an amount that may be int, float, str (param ref), or Expr."""
    if isinstance(amount, Expr):
        return describe_expr(amount)
    if isinstance(amount, str):
        return amount
    if isinstance(amount, float) and amount == int(amount):
        return str(int(amount))
    return str(amount)


def describe_effect(effect: Any, indent: int = 0) -> str | None:
    """Convert a single effect to human-readable text. Returns None for skipped effects."""
    if isinstance(effect, _SKIP_TYPES):
        return None

    prefix = "  " * indent

    match effect:
        # Resource effects
        case Boost(entity=ent, resource=res, amount=amt):
            res_str = describe_expr(res) if isinstance(res, Expr) else res
            return f"{prefix}+{_resolve_amount(amt)} {res_str} to {ent}"

        case Damage(entity=ent, resource=res, amount=amt):
            res_str = describe_expr(res) if isinstance(res, Expr) else res
            return f"{prefix}-{_resolve_amount(amt)} {res_str} to {ent}"

        case Transfer(source=src, target=tgt, resource=res, amount=amt):
            res_str = describe_expr(res) if isinstance(res, Expr) else res
            return (
                f"{prefix}Transfer {_resolve_amount(amt)} {res_str} from {src} to {tgt}"
            )

        case SetResource(entity=ent, resource=res, value=val):
            res_str = describe_expr(res) if isinstance(res, Expr) else res
            return f"{prefix}Set {ent}'s {res_str} to {_resolve_amount(val)}"

        # Entity effects
        case Eliminate(entity=ent):
            ent_str = _resolve_amount(ent) if isinstance(ent, Expr) else ent
            return f"{prefix}Eliminate {ent_str}"

        case Reactivate(entity=ent):
            return f"{prefix}Reactivate {ent}"

        case SetAttr(entity=ent, attr=attr, value=val):
            val_str = _resolve_amount(val) if isinstance(val, Expr) else repr(val)
            return f"{prefix}Set {ent}.{attr} = {val_str}"

        # Relation effects
        case Relate(source=src, target=tgt, relation=rel):
            return f"{prefix}Create relation: {src} --{rel}--> {tgt}"

        case Unrelate(source=src, target=tgt, relation=rel):
            return f"{prefix}Remove relation: {src} --{rel}--> {tgt}"

        # Group effects
        case CreateGroup(type=gtype):
            return f"{prefix}Create {gtype} group"

        case JoinGroup(entity=ent, group=grp):
            return f"{prefix}{ent} joins {grp}"

        case LeaveGroup(entity=ent, group=grp):
            return f"{prefix}{ent} leaves {grp}"

        case DissolveGroup(group=grp):
            return f"{prefix}Dissolve {grp}"

        # Variable effects
        case SetVar(name=name, value=val):
            val_str = _resolve_amount(val) if isinstance(val, Expr) else repr(val)
            return f"{prefix}Set {name} = {val_str}"

        case UpdateVar(path=path, operation=op, value=val, key=key):
            path_str = ".".join(path)
            val_str = (
                _resolve_amount(val)
                if isinstance(val, Expr)
                else repr(val)
                if val is not None
                else ""
            )
            key_str = f" (by {key})" if key else ""
            return f"{prefix}Update {path_str}: {op} {val_str}{key_str}".rstrip()

        # Visibility effects
        case Reveal(entity=ent, attr=attr, to=to, fake=fake):
            if fake is not None:
                return f"{prefix}Reveal {ent}'s {attr} to {to} (FAKE: {fake})"
            return f"{prefix}Reveal {ent}'s {attr} to {to}"

        # Stake effects
        case ReturnStakes():
            return f"{prefix}Return locked stakes"

        case TransferStakes(to=to):
            return f"{prefix}Transfer locked stakes to {to}"

        case TransferStakesSplit(to_key=key):
            return f"{prefix}Split locked stakes to {key}"

        case BurnStakes():
            return f"{prefix}Locked stakes are burned"

        # Communication effects
        case Broadcast(template=tmpl):
            return f'{prefix}Announce: "{tmpl}"'

        case SendMessage(channel=ch, content=content):
            return f'{prefix}Send to {ch}: "{content}"'

        # Control flow
        case When(condition=cond, effects=effs):
            cond_str = describe_expr(cond)
            inner = describe_effects(effs, indent + 1)
            if len(inner) == 1:
                return f"{prefix}If {cond_str}: {inner[0].strip()}"
            lines = [f"{prefix}If {cond_str}:"]
            lines.extend(inner)
            return "\n".join(lines)

        case Cond(branches=branches):
            lines = []
            for guard, effs in branches:
                inner = describe_effects(effs, indent + 1)
                if not inner:
                    continue  # skip empty branches (e.g. Otherwise with no effects)
                if guard is None:
                    lines.append(f"{prefix}Otherwise:")
                else:
                    lines.append(f"{prefix}If {describe_expr(guard)}:")
                lines.extend(inner)
            return "\n".join(lines)

        case Maybe(probability=prob, effects=effs):
            pct = int(prob * 100)
            inner = describe_effects(effs, indent + 1)
            if len(inner) == 1:
                return f"{prefix}{pct}% chance: {inner[0].strip()}"
            lines = [f"{prefix}{pct}% chance:"]
            lines.extend(inner)
            return "\n".join(lines)

        case Each(binding=var, filter=filt, effects=effs):
            filt_str = describe_expr(filt)
            inner = describe_effects(effs, indent + 1)
            if len(inner) == 1:
                return f"{prefix}For each {var} ({filt_str}): {inner[0].strip()}"
            lines = [f"{prefix}For each {var} ({filt_str}):"]
            lines.extend(inner)
            return "\n".join(lines)

        case Let(bindings=binds, effects=effs):
            bind_strs = [f"{k}={describe_expr(v)}" for k, v in binds.items()]
            inner = describe_effects(effs, indent + 1)
            lines = [f"{prefix}Let {', '.join(bind_strs)}:"]
            lines.extend(inner)
            return "\n".join(lines)

        case Repeat(times=n, effects=effs):
            inner = describe_effects(effs, indent + 1)
            lines = [f"{prefix}Repeat {_resolve_amount(n)} times:"]
            lines.extend(inner)
            return "\n".join(lines)

        # Custom/setup effects — use docstring or class name
        case _:
            type_name = type(effect).__name__
            doc = getattr(type(effect), "__doc__", None)
            if doc:
                # Use first sentence of docstring
                first_sentence = doc.strip().split("\n")[0].rstrip(".")
                return f"{prefix}{first_sentence}"
            return f"{prefix}{type_name}"


def describe_effects(effects: tuple | list, indent: int = 0) -> list[str]:
    """Describe a sequence of effects, filtering out skipped ones."""
    result = []
    for eff in effects:
        desc = describe_effect(eff, indent)
        if desc is not None:
            result.append(desc)
    return result


# ---------------------------------------------------------------------------
# Stakes → human-readable
# ---------------------------------------------------------------------------


def describe_stakes(stakes: dict[str, tuple | list]) -> str:
    """Describe deal stakes. Example: 'proposer locks: caps (amount)'"""
    parts = []
    for party, resources in stakes.items():
        res_parts = []
        for item in resources:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                res_name, amount = item
                res_parts.append(f"{res_name} ({amount})")
            else:
                res_parts.append(str(item))
        parts.append(f"{party} locks: {', '.join(res_parts)}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Outcome → human-readable
# ---------------------------------------------------------------------------


def describe_outcome(outcome_id: str, outcome: OutcomeDef) -> str:
    """Describe an outcome. Uses .doc if available, else auto-generates from effects."""
    if outcome.doc:
        return outcome.doc
    effects = describe_effects(outcome.effects)
    if effects:
        return "; ".join(line.strip() for line in effects)
    return outcome_id


def describe_outcome_detail(outcome_id: str, outcome: OutcomeDef) -> list[str]:
    """Full detail for an outcome: doc line + effect breakdown."""
    lines = []
    effect_lines = describe_effects(outcome.effects, indent=1)
    if outcome.doc:
        lines.append(f"**{outcome_id}** — {outcome.doc}")
    elif effect_lines:
        # No doc but has effects — auto-summarize from first effect
        lines.append(f"**{outcome_id}**")
    else:
        # No doc, no effects — just the ID (e.g. "abstain" with no effects)
        lines.append(f"**{outcome_id}** — (no effect)")
        return lines
    lines.extend(effect_lines)
    return lines


# ---------------------------------------------------------------------------
# Full deal/vote/speech_act mechanics
# ---------------------------------------------------------------------------


def describe_deal_mechanics(deal_id: str, deal: DealDef) -> str:
    """Full mechanical breakdown of a deal."""
    lines = [f"## Mechanics: {deal_id} (deal)"]

    if deal.doc:
        lines.append(deal.doc)

    # Parties
    party_parts = []
    for name, pdef in deal.parties.items():
        filter_str = describe_expr(pdef.filter) if pdef.filter is not None else "any"
        if pdef.count:
            party_parts.append(
                f"{name} ({filter_str}, {pdef.count[0]}-{pdef.count[1]} players)"
            )
        else:
            party_parts.append(f"{name} ({filter_str})")
    if party_parts:
        lines.append(f"Parties: {', '.join(party_parts)}")

    # Parameters
    if deal.params:
        param_parts = []
        for pid, pdef in deal.params.items():
            if isinstance(pdef, dict):
                ptype = pdef.get("type", "any")
                pmin = pdef.get("min")
                pmax = pdef.get("max")
                label = pdef.get("label", pid)
                if pmin is not None and pmax is not None:
                    param_parts.append(f"{label}: {ptype} {pmin}-{pmax}")
                else:
                    param_parts.append(f"{label}: {ptype}")
            else:
                ptype = pdef.type
                label = pdef.label or pid
                if pdef.min is not None and pdef.max is not None:
                    param_parts.append(f"{label}: {ptype} {pdef.min}-{pdef.max}")
                elif pdef.options:
                    param_parts.append(f"{label}: {', '.join(pdef.options)}")
                else:
                    param_parts.append(f"{label}: {ptype}")
        lines.append(f"Parameters: {'; '.join(param_parts)}")

    # Stakes
    if deal.stakes:
        lines.append(f"Stakes: {describe_stakes(deal.stakes)}")

    # Guard
    if deal.guard is not None:
        lines.append(f"Guard: {describe_expr(deal.guard)}")

    # Usage limits
    usage_parts = []
    if deal.per_round is not None:
        usage_parts.append(f"{deal.per_round}/round")
    if deal.per_phase is not None:
        usage_parts.append(f"{deal.per_phase}/phase")
    if deal.per_game is not None:
        usage_parts.append(f"{deal.per_game}/game")
    lines.append(f"Usage: {', '.join(usage_parts) if usage_parts else 'unlimited'}")

    # Outcomes
    if deal.outcomes:
        # Immediate deals: single "ok" outcome → show effects inline
        ok_only = list(deal.outcomes.keys()) == ["ok"]
        if ok_only:
            lines.append("")
            lines.append("### Effects")
            for desc in describe_effects(deal.outcomes["ok"].effects, indent=0):
                lines.append(desc)
        else:
            lines.append("")
            lines.append("### Outcomes")
            for oid, odef in deal.outcomes.items():
                for line in describe_outcome_detail(oid, odef):
                    lines.append(line)

    return "\n".join(lines)


def describe_vote_mechanics(vote_id: str, vote: VoteDef) -> str:
    """Full mechanical breakdown of a vote."""
    lines = [f"## Mechanics: {vote_id} (vote)"]

    if vote.doc:
        lines.append(vote.doc)

    if vote.options:
        lines.append(f"Options: {', '.join(vote.options)}")
    lines.append(f"Threshold: {vote.threshold}")
    if vote.subject:
        lines.append(f"Subject: player target required")

    if vote.outcomes:
        lines.append("")
        lines.append("### Outcomes")
        for oid, odef in vote.outcomes.items():
            for line in describe_outcome_detail(oid, odef):
                lines.append(line)

    return "\n".join(lines)


def describe_speech_act_mechanics(sa_id: str, sa: SpeechActDef) -> str:
    """Full mechanical breakdown of a speech act."""
    lines = [f"## Mechanics: {sa_id} ({sa.act_type})"]

    if sa.doc:
        lines.append(sa.doc)

    # Cost
    if sa.cost:
        cost_parts = [f"{amt} {res}" for res, amt in sa.cost.items()]
        lines.append(f"Cost: {', '.join(cost_parts)}")
    else:
        lines.append("Cost: free")

    # Verification
    if sa.verify_triggers:
        lines.append(f"Verified on: {', '.join(sa.verify_triggers)}")
    if sa.verify_condition is not None:
        lines.append(f"Condition: {describe_expr(sa.verify_condition)}")
    if sa.verify_true_effects:
        true_desc = describe_effects(sa.verify_true_effects)
        if true_desc:
            lines.append(f"If true: {'; '.join(d.strip() for d in true_desc)}")
    if sa.verify_false_effects:
        false_desc = describe_effects(sa.verify_false_effects)
        if false_desc:
            lines.append(f"If false: {'; '.join(d.strip() for d in false_desc)}")

    # Endorsement
    if sa.endorsable:
        if sa.endorsement_cost:
            e_parts = [f"{amt} {res}" for res, amt in sa.endorsement_cost.items()]
            lines.append(f"Endorsement cost: {', '.join(e_parts)}")
        else:
            lines.append("Endorsement cost: same as cost")

    # Promise specifics
    if sa.promise_action:
        lines.append(f"Tracks action: {sa.promise_action}")
    if sa.promise_deadline is not None:
        lines.append(f"Promise deadline: {sa.promise_deadline} rounds")

    # Inquire specifics
    if sa.inquire_response_options:
        lines.append(f"Response options: {', '.join(sa.inquire_response_options)}")
    if sa.inquire_deadline:
        lines.append(f"Response deadline: {sa.inquire_deadline} phases")
    if sa.inquire_silence_effects:
        silence_desc = describe_effects(sa.inquire_silence_effects)
        if silence_desc:
            lines.append(
                f"Silence penalty: {'; '.join(d.strip() for d in silence_desc)}"
            )

    # Usage
    usage_parts = []
    if sa.per_round is not None:
        usage_parts.append(f"{sa.per_round}/round")
    if sa.per_phase is not None:
        usage_parts.append(f"{sa.per_phase}/phase")
    if sa.per_game is not None:
        usage_parts.append(f"{sa.per_game}/game")
    if sa.phase_filter:
        usage_parts.append(f"phases: {', '.join(sa.phase_filter)}")
    if usage_parts:
        lines.append(f"Limits: {' | '.join(usage_parts)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Short summaries (for tool descriptions and inline rules)
# ---------------------------------------------------------------------------


def outcome_summary(outcomes: dict[str, OutcomeDef]) -> str:
    """One-line summary of outcomes for tool descriptions.

    Example: "accept (caps transferred) | reject (returned) | expose (reputation damage)"
    """
    parts = []
    for oid, odef in outcomes.items():
        if odef.doc:
            # Truncate to first clause
            short = odef.doc.split(" — ")[0] if " — " in odef.doc else odef.doc
            if len(short) > 50:
                short = short[:47] + "..."
            parts.append(f"{oid} ({short})")
        else:
            parts.append(oid)
    return " | ".join(parts)


def speech_act_verification_summary(sa: SpeechActDef) -> str:
    """One-line verification summary for tool descriptions.

    Example: "Verified on: eliminate. True: +15 rep. False: -20 rep."
    Excludes Broadcast effects to keep it short.
    """
    parts = []
    if sa.verify_triggers:
        parts.append(f"Verified on: {', '.join(sa.verify_triggers)}")
    if sa.verify_true_effects:
        true_desc = _short_effects(sa.verify_true_effects)
        if true_desc:
            parts.append(f"True: {'; '.join(true_desc)}")
    if sa.verify_false_effects:
        false_desc = _short_effects(sa.verify_false_effects)
        if false_desc:
            parts.append(f"False: {'; '.join(false_desc)}")
    return ". ".join(parts)


def _short_effects(effects: tuple | list) -> list[str]:
    """Describe effects for short summaries — skip Broadcast/SendMessage/Emit/Notify."""
    result = []
    for eff in effects:
        if isinstance(eff, (Broadcast, SendMessage, *_SKIP_TYPES)):
            continue
        desc = describe_effect(eff)
        if desc is not None:
            result.append(desc.strip())
    return result
