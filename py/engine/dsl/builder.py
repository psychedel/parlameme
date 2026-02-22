"""DSL Builder — fluent method chaining for game definitions.

Usage:
    game = (
        Game("duel", "Duel", players=(2, 2))
        .resource("health", initial=100, visibility="public")
        .phase("action", allows=["attack", "defend"])
        .deal("attack", ...)
        .victory("knockout", when=..., type="distribution", score=actor.health)
        .build()
    )
"""

from __future__ import annotations

from typing import Any

import attrs

from engine.expr.core import Expr
from engine.runtime.effects import Effect
from engine.runtime.state import (
    AttrDef,
    ChannelDef,
    ChannelHint,
    CommitmentDef,
    CompiledGame,
    ContextConfig,
    DealDef,
    GameParamDef,
    GroupTypeDef,
    OutcomeDef,
    ParamDef,
    PartyDef,
    PhaseDef,
    PhaseHint,
    ResourceDef,
    RoleDef,
    RoleHint,
    SpeechActDef,
    TransitionDef,
    VarHint,
    VictoryDef,
    Visibility,
    VoteDef,
)


def _to_visibility(v: str | Visibility) -> Visibility:
    """Accept both string and Visibility enum."""
    if isinstance(v, Visibility):
        return v
    return Visibility(v)


class Game:
    """Fluent builder for game definitions."""

    def __init__(self, id: str, name: str = "", players: tuple[int, int] = (2, 12)):
        self._id = id
        self._name = name or id
        self._min_players, self._max_players = players
        self._resources: dict[str, ResourceDef] = {}
        self._attrs: dict[str, AttrDef] = {}
        self._roles: dict[str, RoleDef] = {}
        self._group_types: dict[str, GroupTypeDef] = {}
        self._deals: dict[str, DealDef] = {}
        self._votes: dict[str, VoteDef] = {}
        self._phases: list[PhaseDef] = []
        self._victories: list[VictoryDef] = []
        self._channels: dict[str, ChannelDef] = {}
        self._commitments: dict[str, CommitmentDef] = {}
        self._speech_acts: dict[str, SpeechActDef] = {}
        self._game_params: dict[str, GameParamDef] = {}
        self._context: ContextConfig | None = None

    # -------------------------------------------------------------------
    # Resource & Attributes
    # -------------------------------------------------------------------

    def resource(
        self,
        id: str,
        initial: int | float = 0,
        visibility: str | Visibility = "public",
        bounds: tuple[int | float | None, int | float | None] = (None, None),
        transferable: bool = True,
    ) -> Game:
        self._resources[id] = ResourceDef(
            id=id,
            initial=initial,
            visibility=_to_visibility(visibility),
            bounds=bounds,
            transferable=transferable,
        )
        return self

    def param(
        self,
        id: str,
        default: int | float | str = 0,
        *,
        type: str = "number",
        min: int | float | None = None,
        max: int | float | None = None,
        options: tuple[str, ...] | list[str] = (),
        label: str = "",
    ) -> Game:
        """Declare a tunable game parameter.

        Game params are injected into state.vars_ at start_game() and are
        accessible via game.param_name in Expr evaluation. Training infra
        can override defaults to prevent agent overfitting.
        """
        self._game_params[id] = GameParamDef(
            id=id,
            default=default,
            type=type,
            min=min,
            max=max,
            options=tuple(options) if isinstance(options, list) else options,
            label=label,
        )
        return self

    def attr(
        self,
        id: str,
        initial: Any = None,
        visibility: str | Visibility = "public",
        values: tuple[str, ...] | list[str] | None = None,
        distribute: bool = False,
    ) -> Game:
        vals = tuple(values) if values else None
        self._attrs[id] = AttrDef(
            id=id,
            initial=initial,
            visibility=_to_visibility(visibility),
            values=vals,
            distribute=distribute,
        )
        return self

    # -------------------------------------------------------------------
    # Roles & Groups
    # -------------------------------------------------------------------

    def roles(self, role_configs: dict[str, dict[str, Any] | RoleDef]) -> Game:
        for rid, cfg in role_configs.items():
            if isinstance(cfg, RoleDef):
                self._roles[rid] = cfg
            else:
                self._roles[rid] = RoleDef(id=rid, **cfg)
        return self

    def role(self, id: str, team: str, **kwargs) -> Game:
        self._roles[id] = RoleDef(id=id, team=team, **kwargs)
        return self

    def group(
        self,
        id: str,
        visible: bool = True,
        exclusive: bool = False,
        knows_members: bool = True,
        linked_fate: bool = False,
        max_size: int | None = None,
    ) -> Game:
        self._group_types[id] = GroupTypeDef(
            id=id,
            visible=visible,
            exclusive=exclusive,
            knows_members=knows_members,
            linked_fate=linked_fate,
            max_size=max_size,
        )
        return self

    # -------------------------------------------------------------------
    # Deals
    # -------------------------------------------------------------------

    def deal(
        self,
        id: str,
        actor: Expr | None = None,
        target: Expr | None = None,
        proposer: Expr | None = None,
        responder: Expr | None = None,
        responders: dict[str, Any] | None = None,
        parties: dict[str, PartyDef | dict] | None = None,
        params: dict[str, ParamDef | dict] | None = None,
        stakes: dict[str, list[tuple[str, Any]]] | None = None,
        guard: Expr | None = None,
        responses: list[str] | None = None,
        outcomes: dict[str, OutcomeDef | dict] | None = None,
        completion_rule: str = "all",
        per_round: int | None = None,
        per_phase: int | None = None,
        per_game: int | None = None,
        effects: list | None = None,
        doc: str = "",
    ) -> Game:
        """Define a deal.

        Shorthand:
            actor=<filter>       → single-party deal (actor filter)
            proposer + responder → bilateral deal
            proposer + responders → multilateral deal
            effects=[]           → immediate effects (no response needed)
        """
        # Build parties
        built_parties: dict[str, PartyDef] = {}
        if parties:
            for pname, pdef in parties.items():
                if isinstance(pdef, PartyDef):
                    built_parties[pname] = pdef
                else:
                    built_parties[pname] = PartyDef(**pdef)
        else:
            if actor is not None:
                built_parties["actor"] = PartyDef(filter=actor)
            if proposer is not None:
                built_parties["proposer"] = PartyDef(filter=proposer)
            if responder is not None:
                built_parties["responder"] = PartyDef(
                    filter=responder, excludes=("proposer",)
                )
            if responders is not None:
                # responders={"filter": expr, "count": (min, max)}
                # or responders={"filter": expr, "count": [min, max]}
                r_filter = responders.get("filter")
                r_count = responders.get("count")
                if isinstance(r_count, list):
                    r_count = tuple(r_count)
                built_parties["responders"] = PartyDef(
                    filter=r_filter,
                    excludes=("proposer",),
                    count=r_count,
                )
            if target is not None:
                built_parties["target"] = PartyDef(filter=target)

        # Build params
        built_params: dict[str, ParamDef] = {}
        if params:
            for pname, pdef in params.items():
                if isinstance(pdef, ParamDef):
                    built_params[pname] = pdef
                else:
                    built_params[pname] = ParamDef(**pdef)

        # Build outcomes
        built_outcomes: dict[str, OutcomeDef] = {}
        if outcomes:
            for oname, odef in outcomes.items():
                if isinstance(odef, OutcomeDef):
                    built_outcomes[oname] = odef
                elif isinstance(odef, dict):
                    built_outcomes[oname] = OutcomeDef(**odef)
                else:
                    # Assume it's a list of effects
                    built_outcomes[oname] = OutcomeDef(effects=tuple(odef))
        elif effects:
            # Shorthand: effects list → single "ok" outcome
            built_outcomes["ok"] = OutcomeDef(effects=tuple(effects))

        # Response options
        response_options = tuple(responses) if responses else ()
        if not response_options and built_outcomes and built_parties.get("responder"):
            # Auto-derive from outcome keys
            response_options = tuple(built_outcomes.keys())

        self._deals[id] = DealDef(
            id=id,
            parties=built_parties,
            params=built_params,
            stakes=stakes or {},
            guard=guard,
            response_options=response_options,
            outcomes=built_outcomes,
            completion_rule=completion_rule,
            per_round=per_round,
            per_phase=per_phase,
            per_game=per_game,
            doc=doc,
        )
        return self

    # -------------------------------------------------------------------
    # Votes
    # -------------------------------------------------------------------

    def vote(
        self,
        id: str,
        voters: Expr | None = None,
        proposer: Expr | None = None,
        subject: dict[str, Any] | None = None,
        options: list[str] | tuple[str, ...] = (),
        threshold: str = "majority",
        visibility: str = "public",
        outcomes: dict[str, OutcomeDef | dict | list] | None = None,
        doc: str = "",
    ) -> Game:
        built_outcomes: dict[str, OutcomeDef] = {}
        if outcomes:
            for oname, odef in outcomes.items():
                if isinstance(odef, OutcomeDef):
                    built_outcomes[oname] = odef
                elif isinstance(odef, dict):
                    built_outcomes[oname] = OutcomeDef(**odef)
                else:
                    built_outcomes[oname] = OutcomeDef(effects=tuple(odef))

        self._votes[id] = VoteDef(
            id=id,
            proposer_filter=proposer,
            voters_filter=voters,
            subject=subject,
            options=tuple(options),
            threshold=threshold,
            visibility=visibility,
            outcomes=built_outcomes,
            doc=doc,
        )
        return self

    # -------------------------------------------------------------------
    # Phases
    # -------------------------------------------------------------------

    def phase(
        self,
        id: str,
        name: str = "",
        category: str = "action",
        when: Expr | None = None,
        automatic: bool = False,
        once: bool = False,
        parallel: bool = False,
        allows: list[str] | None = None,
        effects: list | None = None,
        channels: list[str] | None = None,
        resolution: str = "first_wins",
        priorities: dict[str, int] | None = None,
        duration: int | None = None,
        next: str | None = None,
        transitions: list[TransitionDef] | None = None,
        starts_round: bool = False,
        reward_expr: Any = None,
    ) -> Game:
        self._phases.append(
            PhaseDef(
                id=id,
                name=name or id,
                category=category,
                when=when,
                automatic=automatic,
                once=once,
                parallel=parallel,
                allows=tuple(allows or []),
                effects=tuple(effects or []),
                channels=tuple(channels or []),
                resolution=resolution,
                priorities=priorities or {},
                duration=duration,
                next=next,
                transitions=tuple(transitions or []),
                starts_round=starts_round,
                reward_expr=reward_expr,
            )
        )
        return self

    @staticmethod
    def transition(guard, target: str) -> TransitionDef:
        """Create a conditional phase transition rule."""
        return TransitionDef(guard=guard, target=target)

    # -------------------------------------------------------------------
    # Victory
    # -------------------------------------------------------------------

    def victory(
        self,
        id: str,
        when: Expr | None = None,
        type: str = "single",
        team: str | None = None,
        score: Expr | None = None,
        priority: int = 100,
        shared: bool = False,
        individual: bool = False,
        message: str = "",
    ) -> Game:
        self._victories.append(
            VictoryDef(
                id=id,
                type=type,
                when=when,
                team=team,
                score=score,
                priority=priority,
                shared=shared,
                individual=individual,
                message=message,
            )
        )
        return self

    # -------------------------------------------------------------------
    # Channels & Commitments
    # -------------------------------------------------------------------

    def channel(
        self,
        id: str,
        type: str = "public",
        group: str | None = None,
        write_filter: Expr | None = None,
        read_filter: Expr | None = None,
        phases: list[str] | None = None,
        max_participants: int | None = None,
        effects: list | None = None,
        description: str = "",
    ) -> Game:
        self._channels[id] = ChannelDef(
            id=id,
            type=type,
            group=group,
            write_filter=write_filter,
            read_filter=read_filter,
            phase_filter=tuple(phases) if phases else (),
            max_participants=max_participants,
            effects=tuple(effects) if effects else (),
            description=description,
        )
        return self

    def commitment(
        self,
        id: str,
        trigger: str = "eliminate",
        guard: Expr | None = None,
        effects: list | None = None,
        once: bool = False,
        doc: str = "",
    ) -> Game:
        self._commitments[id] = CommitmentDef(
            id=id,
            trigger=trigger,
            guard=guard,
            effects=tuple(effects or []),
            once=once,
            doc=doc,
        )
        return self

    # -------------------------------------------------------------------
    # Speech Acts
    # -------------------------------------------------------------------

    def speech_act(
        self,
        id: str,
        act_type: str,
        actor_filter: Expr | None = None,
        target_filter: Expr | None = None,
        cost: dict[str, int | float] | None = None,
        endorsement_cost: dict[str, int | float] | None = None,
        params: dict[str, ParamDef | dict] | None = None,
        verify_condition: Expr | None = None,
        verify_triggers: list[str] | tuple[str, ...] = ("eliminate", "game_end"),
        verify_true_effects: list | None = None,
        verify_false_effects: list | None = None,
        inquire_response_options: list[str] | None = None,
        inquire_deadline: int = 1,
        inquire_silence_effects: list | None = None,
        promise_action: str | None = None,
        promise_deadline: int | None = None,
        per_round: int | None = None,
        per_phase: int | None = None,
        per_game: int | None = None,
        endorsable: bool = True,
        visibility: str = "public",
        phase_filter: list[str] | None = None,
        doc: str = "",
    ) -> Game:
        """Define a performative speech act.

        Six act types: claim, accuse, promise, predict, endorse, inquire.
        """
        built_params: dict[str, ParamDef] = {}
        if params:
            for pname, pdef in params.items():
                if isinstance(pdef, ParamDef):
                    built_params[pname] = pdef
                else:
                    built_params[pname] = ParamDef(**pdef)

        self._speech_acts[id] = SpeechActDef(
            id=id,
            act_type=act_type,
            actor_filter=actor_filter,
            target_filter=target_filter,
            cost=cost or {},
            endorsement_cost=endorsement_cost,
            params=built_params,
            verify_condition=verify_condition,
            verify_triggers=tuple(verify_triggers),
            verify_true_effects=tuple(verify_true_effects or []),
            verify_false_effects=tuple(verify_false_effects or []),
            inquire_response_options=tuple(inquire_response_options or []),
            inquire_deadline=inquire_deadline,
            inquire_silence_effects=tuple(inquire_silence_effects or []),
            promise_action=promise_action,
            promise_deadline=promise_deadline,
            per_round=per_round,
            per_phase=per_phase,
            per_game=per_game,
            endorsable=endorsable,
            visibility=visibility,
            phase_filter=tuple(phase_filter or []),
            doc=doc,
        )
        return self

    # -------------------------------------------------------------------
    # Context (AI agent annotations)
    # -------------------------------------------------------------------

    def context(
        self,
        game_summary: str = "",
        score_explanation: str = "",
        var_hints: list[VarHint] | None = None,
        phase_hints: list[PhaseHint] | None = None,
        role_hints: list[RoleHint] | None = None,
        channel_hints: list[ChannelHint] | None = None,
        deal_priorities: dict[str, int] | None = None,
    ) -> Game:
        """Add AI agent context annotations.

        Context is metadata only — doesn't affect gameplay mechanics
        and is excluded from the source hash (changing tips never
        invalidates archives).
        """
        self._context = ContextConfig(
            game_summary=game_summary,
            score_explanation=score_explanation,
            var_hints={v.id: v for v in (var_hints or [])},
            phase_hints={p.id: p for p in (phase_hints or [])},
            role_hints={r.id: r for r in (role_hints or [])},
            channel_hints={c.id: c for c in (channel_hints or [])},
            deal_priorities=deal_priorities or {},
        )
        return self

    # -------------------------------------------------------------------
    # Build
    # -------------------------------------------------------------------

    @staticmethod
    def _collect_refs(obj: Any, refs: set[str]) -> None:
        """Walk an Expr/Effect tree and collect root Ref names."""
        from engine.expr.core import Ref as RefNode

        if isinstance(obj, RefNode):
            if obj.parts:
                refs.add(obj.parts[0])
            return

        # Expr subtypes with child exprs
        from engine.expr.core import (
            And,
            Arith,
            Call,
            Cmp,
            If,
            Not,
            Or,
        )

        if isinstance(obj, Cmp):
            Game._collect_refs(obj.lhs, refs)
            Game._collect_refs(obj.rhs, refs)
        elif isinstance(obj, Arith):
            Game._collect_refs(obj.lhs, refs)
            Game._collect_refs(obj.rhs, refs)
        elif isinstance(obj, And):
            for e in obj.exprs:
                Game._collect_refs(e, refs)
        elif isinstance(obj, Or):
            for e in obj.exprs:
                Game._collect_refs(e, refs)
        elif isinstance(obj, Not):
            Game._collect_refs(obj.inner, refs)
        elif isinstance(obj, Call):
            for a in obj.args:
                Game._collect_refs(a, refs)
        elif isinstance(obj, If):
            Game._collect_refs(obj.condition, refs)
            Game._collect_refs(obj.then_, refs)
            Game._collect_refs(obj.else_, refs)
        elif isinstance(obj, Expr):
            # Unknown Expr subtype — skip
            pass
        elif hasattr(obj, "__dataclass_fields__"):
            # Effect dataclasses — walk all fields
            for field_name in obj.__dataclass_fields__:
                val = getattr(obj, field_name, None)
                if val is not None:
                    Game._collect_refs(val, refs)
        elif isinstance(obj, (tuple, list)):
            for item in obj:
                Game._collect_refs(item, refs)
        elif isinstance(obj, dict):
            for v in obj.values():
                Game._collect_refs(v, refs)

    @staticmethod
    def _validate_bindings(
        action_id: str,
        action_type: str,
        available: set[str],
        effects_and_exprs: list[Any],
        errors: list[str],
    ) -> None:
        """Check that Ref roots in effects/exprs match available bindings."""
        refs: set[str] = set()
        for obj in effects_and_exprs:
            if obj is not None:
                Game._collect_refs(obj, refs)
        # Filter out non-binding refs (literal entity IDs, strings, etc.)
        # Only flag refs that look like binding names but aren't available
        BUILTIN = {"game", "params", "claim", "self"}
        for ref in refs:
            if ref in available or ref in BUILTIN:
                continue
            # Single-character binding names are likely Each bindings (p, m, etc.)
            if len(ref) == 1:
                continue
            errors.append(
                f"{action_type} '{action_id}' references binding '{ref}' "
                f"which is not in available bindings {sorted(available)}"
            )

    def build(self) -> CompiledGame:
        """Validate and freeze into CompiledGame."""
        # Basic validation
        if not self._phases:
            raise ValueError(f"Game '{self._id}' has no phases")
        if not self._victories:
            raise ValueError(f"Game '{self._id}' has no victory conditions")

        # Cross-reference validation
        all_action_ids = set(self._deals) | set(self._votes) | set(self._speech_acts)
        phase_ids = {p.id for p in self._phases}
        errors: list[str] = []
        for phase in self._phases:
            for action_id in phase.allows:
                if action_id not in all_action_ids:
                    errors.append(
                        f"Phase '{phase.id}' allows '{action_id}' "
                        f"but no deal or vote with that ID exists"
                    )
            for ch_id in phase.channels:
                if ch_id not in self._channels:
                    errors.append(
                        f"Phase '{phase.id}' references channel '{ch_id}' "
                        f"but no channel with that ID exists"
                    )
            # Validate phase transition targets
            if phase.next is not None and phase.next not in phase_ids:
                errors.append(
                    f"Phase '{phase.id}' has next='{phase.next}' "
                    f"but no phase with that ID exists"
                )
            for tr in phase.transitions:
                if tr.target not in phase_ids:
                    errors.append(
                        f"Phase '{phase.id}' has transition to '{tr.target}' "
                        f"but no phase with that ID exists"
                    )
        if errors:
            raise ValueError(
                f"Game '{self._id}' has cross-reference errors:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

        # Binding validation: check that Ref roots match available bindings
        import warnings

        binding_warnings: list[str] = []
        for deal_id, deal in self._deals.items():
            available = set(deal.parties.keys()) | {"actor", "target"}
            exprs: list = []
            if deal.guard is not None:
                exprs.append(deal.guard)
            for pdef in deal.parties.values():
                if pdef.filter is not None:
                    exprs.append(pdef.filter)
            for odef in deal.outcomes.values():
                exprs.append(odef.effects)
                if odef.guard is not None:
                    exprs.append(odef.guard)
            Game._validate_bindings(
                deal_id, "deal", available, exprs, binding_warnings
            )

        for vote_id, vote in self._votes.items():
            available = {"actor", "target", "subject", "proposer"}
            exprs = []
            if vote.proposer_filter is not None:
                exprs.append(vote.proposer_filter)
            if vote.voters_filter is not None:
                exprs.append(vote.voters_filter)
            for odef in vote.outcomes.values():
                exprs.append(odef.effects)
            Game._validate_bindings(
                vote_id, "vote", available, exprs, binding_warnings
            )

        for sa_id, sa in self._speech_acts.items():
            available = {"actor", "target", "subject", "claim"}
            exprs = []
            if sa.actor_filter is not None:
                exprs.append(sa.actor_filter)
            if sa.target_filter is not None:
                exprs.append(sa.target_filter)
            if sa.verify_condition is not None:
                exprs.append(sa.verify_condition)
            exprs.append(sa.verify_true_effects)
            exprs.append(sa.verify_false_effects)
            Game._validate_bindings(
                sa_id, "speech_act", available, exprs, binding_warnings
            )

        for w in binding_warnings:
            warnings.warn(f"Game '{self._id}': {w}", stacklevel=2)

        # Add default alive() filter to deals that don't specify one
        for deal in self._deals.values():
            for party_name, party in deal.parties.items():
                if party.filter is None:
                    # Default: alive() for all parties
                    from engine.expr.functions import alive

                    new_party = PartyDef(
                        filter=alive(),
                        excludes=party.excludes,
                        not_in_group=party.not_in_group,
                        count=party.count,
                    )
                    deal = DealDef(
                        id=deal.id,
                        parties={**deal.parties, party_name: new_party},
                        params=deal.params,
                        stakes=deal.stakes,
                        guard=deal.guard,
                        response_options=deal.response_options,
                        outcomes=deal.outcomes,
                        completion_rule=deal.completion_rule,
                        per_round=deal.per_round,
                        per_phase=deal.per_phase,
                        per_game=deal.per_game,
                        doc=deal.doc,
                    )
                    self._deals[deal.id] = deal

        import hashlib

        # Strip training metadata (reward_expr) from phases for hash —
        # changing reward signals shouldn't invalidate archives
        phases_for_hash = tuple(
            attrs.evolve(p, reward_expr=None) if p.reward_expr is not None else p
            for p in self._phases
        )

        # Compute source hash from actual content (deterministic via attrs.frozen repr)
        # Excludes: context, game_params, reward_expr (training metadata)
        content = repr(
            (
                self._id,
                self._name,
                self._min_players,
                self._max_players,
                sorted(self._resources.items()),
                sorted(self._attrs.items()),
                sorted(self._roles.items()),
                sorted(self._group_types.items()),
                sorted(self._deals.items()),
                sorted(self._votes.items()),
                phases_for_hash,
                self._victories,
                sorted(self._channels.items()),
                sorted(self._commitments.items()),
                sorted(self._speech_acts.items()),
            )
        )
        source_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        return CompiledGame(
            id=self._id,
            name=self._name,
            min_players=self._min_players,
            max_players=self._max_players,
            resources=dict(self._resources),
            attrs_defs=dict(self._attrs),
            roles=dict(self._roles),
            group_types=dict(self._group_types),
            deals=dict(self._deals),
            votes=dict(self._votes),
            phases=tuple(self._phases),
            victories=tuple(self._victories),
            channels=dict(self._channels),
            commitments=dict(self._commitments),
            speech_acts=dict(self._speech_acts),
            source_hash=source_hash,
            context=self._context or ContextConfig(),
            game_params=dict(self._game_params),
        )
