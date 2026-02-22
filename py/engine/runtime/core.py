"""Game runtime — THE single source of truth for all game logic.

One class, pure functions. No layered delegation.
Handles: phase advancement, deals, votes, victory detection.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import attrs

from engine.errors import E, Err, ErrorInfo, Ok, Result, fail, ok
from engine.expr.core import Expr
from engine.expr.evaluator import Context, evaluate
from engine.runtime.effects import Effect, apply_effect, apply_effects
from engine.runtime.state import (
    CompiledGame,
    Entity,
    GameState,
    HistoryEntry,
    PendingDeal,
    PendingSpeechAct,
    PendingVote,
    PhaseDef,
)

# ---------------------------------------------------------------------------
# Game Runtime
# ---------------------------------------------------------------------------


class GameRuntime:
    """Stateless runtime — all state flows through pure functions."""

    def __init__(self, compiled: CompiledGame):
        self.compiled = compiled
        self._phase_idx = {p.id: i for i, p in enumerate(compiled.phases)}
        self._has_explicit_rounds = any(
            p.starts_round for p in compiled.phases
        )

    def _ctx(self, state: GameState, **bindings: Any) -> Context:
        return Context(state=state, compiled=self.compiled, bindings=bindings)

    # -------------------------------------------------------------------
    # Game lifecycle
    # -------------------------------------------------------------------

    def start_game(
        self,
        player_ids: list[str],
        seed: int = 42,
        params: dict[str, Any] | None = None,
    ) -> GameState:
        """Initialize game state with players and optional param overrides."""
        # Merge game params: defaults from CompiledGame, overridden by user params
        initial_vars = _merge_game_params(self.compiled, params)

        entities = {}
        for pid in player_ids:
            resources = {
                rid: rdef.initial for rid, rdef in self.compiled.resources.items()
            }
            # Apply starting_ prefix overrides from game params
            for param_name, param_val in initial_vars.items():
                if param_name.startswith("starting_") and isinstance(param_val, (int, float)):
                    resource_id = param_name[len("starting_"):]
                    if resource_id in resources:
                        resources[resource_id] = param_val
            entities[pid] = Entity(id=pid, resources=resources)

        # Set initial attrs
        for attr_id, adef in self.compiled.attrs_defs.items():
            if adef.initial is not None:
                for pid in player_ids:
                    entities[pid] = attrs.evolve(
                        entities[pid],
                        attrs_={**entities[pid].attrs_, attr_id: adef.initial},
                    )

        phase = self.compiled.phases[0] if self.compiled.phases else None
        state = GameState(
            entities=entities,
            seed=seed,
            rng_state=seed,
            phase=phase.id if phase else "",
            phase_index=0,
            vars_=initial_vars,
        )
        return state

    def run_setup(self, state: GameState) -> GameState:
        """Run setup phase effects and advance to first interactive phase."""
        phase = self._current_phase(state)
        if phase and phase.category == "setup":
            ctx = self._ctx(state)
            state = apply_effects(phase.effects, state, ctx)
            state = self.advance_phase(state)
        return state

    # -------------------------------------------------------------------
    # Phase management — SINGLE SOURCE OF TRUTH
    # -------------------------------------------------------------------

    def advance_phase(self, state: GameState) -> GameState:
        """Advance to next phase, cascading through automatic phases.

        This is THE advance_phase. No other function advances phases.
        """
        max_cascades = 20
        for _ in range(max_cascades):
            state = self._advance_one(state)
            state = self._run_entry_effects(state)
            state = self._cleanup_phase(state)
            phase = self._current_phase(state)
            if not phase or not phase.automatic:
                return state
        raise RuntimeError(
            f"Phase cascade limit ({max_cascades}) reached at phase {state.phase}"
        )

    def _advance_one(self, state: GameState) -> GameState:
        """Move to next non-skipped phase.

        Resolution order:
        1. Evaluate current phase's transitions (first match wins)
        2. Use current phase's unconditional next
        3. Linear scan fallback (original behavior)
        """
        phases = self.compiled.phases
        n = len(phases)
        if n == 0:
            return state

        idx = state.phase_index
        round_ = state.round
        current = phases[idx] if 0 <= idx < n else None

        # --- Try non-linear transition from current phase ---
        target_id = None
        if current:
            if current.transitions:
                ctx = self._ctx(state)
                for tr in current.transitions:
                    if evaluate(tr.guard, ctx):
                        target_id = tr.target
                        break
            if target_id is None and current.next is not None:
                target_id = current.next

        if target_id is not None:
            return self._jump_to_phase(state, target_id, idx, round_)

        # --- Linear fallback ---
        return self._linear_advance(state, idx, round_)

    def _jump_to_phase(
        self, state: GameState, target_id: str, from_idx: int, round_: int
    ) -> GameState:
        """Jump to a specific phase by ID."""
        target_idx = self._phase_idx.get(target_id)
        if target_idx is None:
            raise RuntimeError(
                f"Transition target '{target_id}' not found in phases"
            )

        phase = self.compiled.phases[target_idx]

        # Round counting
        if self._has_explicit_rounds:
            # starts_round increments only after the first transition (game starts at round 1)
            inc = phase.starts_round and state.phase_transition_count > 0
            new_round = round_ + (1 if inc else 0)
        else:
            new_round = round_ + (1 if target_idx <= from_idx else 0)

        # If target should be skipped, fall through to linear from that position
        if self._should_skip(state, phase, new_round):
            return self._linear_advance(state, target_idx, new_round)

        once = state.executed_once
        if phase.once:
            once = once | {phase.id}

        return attrs.evolve(
            state,
            phase_index=target_idx,
            phase=phase.id,
            round=new_round,
            executed_once=once,
            phase_transition_count=state.phase_transition_count + 1,
        )

    def _linear_advance(
        self, state: GameState, idx: int, round_: int
    ) -> GameState:
        """Original linear phase advancement — walk forward through the tuple."""
        phases = self.compiled.phases
        n = len(phases)

        for i in range(1, n + 1):
            next_idx = (idx + i) % n
            phase = phases[next_idx]

            if self._has_explicit_rounds:
                # starts_round increments only after the first transition
                inc = phase.starts_round and state.phase_transition_count > 0
                new_round = round_ + (1 if inc else 0)
            else:
                # Legacy: round increments when wrapping to start
                new_round = round_ + (1 if next_idx <= idx else 0)

            if self._should_skip(state, phase, new_round):
                continue

            once = state.executed_once
            if phase.once:
                once = once | {phase.id}

            return attrs.evolve(
                state,
                phase_index=next_idx,
                phase=phase.id,
                round=new_round,
                executed_once=once,
                phase_transition_count=state.phase_transition_count + 1,
            )

        raise RuntimeError("All phases skipped — infinite loop prevented")

    def _should_skip(self, state: GameState, phase: PhaseDef, round_num: int) -> bool:
        """Check if a phase should be skipped."""
        # Setup only on round 1
        if phase.category == "setup" and round_num > 1:
            return True
        # Once-phases already executed
        if phase.once and phase.id in state.executed_once:
            return True
        # Guard expression — evaluate against state with pending round number
        if phase.when is not None:
            eval_state = (
                state
                if state.round == round_num
                else attrs.evolve(state, round=round_num)
            )
            ctx = self._ctx(eval_state)
            if not evaluate(phase.when, ctx):
                return True
        return False

    def _run_entry_effects(self, state: GameState) -> GameState:
        """Execute phase entry effects."""
        phase = self._current_phase(state)
        if phase and phase.effects:
            ctx = self._ctx(
                state,
                _on_eliminate=lambda eid, s: self._fire_commitments(
                    "eliminate", eid, s
                ),
            )
            state = apply_effects(phase.effects, state, ctx)
        return state

    def _cleanup_phase(self, state: GameState) -> GameState:
        """Clean up stale data on phase transition."""
        state = self._reset_phase_usage(state)
        state = self._fire_phase_change_commitments(state)
        state = self._process_speech_acts_on_phase_change(state)
        return state

    def _fire_phase_change_commitments(self, state: GameState) -> GameState:
        """Fire commitments with trigger='phase_change' for each alive entity."""
        phase_commitments = [
            c for c in self.compiled.commitments.values()
            if c.trigger == "phase_change"
        ]
        if not phase_commitments:
            return state
        for eid in state.get_active_entity_ids():
            state = self._fire_commitments("phase_change", eid, state)
        return state

    def _reset_phase_usage(self, state: GameState) -> GameState:
        """Reset per-phase usage counters. Pure phase logic — no speech act side effects."""
        new_usage = {}
        for key, counts in state.usage.items():
            new_counts = {k: v for k, v in counts.items() if not k.startswith("phase:")}
            if new_counts:
                new_usage[key] = new_counts
        return attrs.evolve(state, usage=new_usage)

    def _process_speech_acts_on_phase_change(self, state: GameState) -> GameState:
        """Run speech act checks on phase transition. Separated for testability."""
        if self.compiled.speech_acts:
            state = self._check_speech_act_triggers(state, "phase_change")
            state = self._check_inquire_deadlines(state)
            state = self._check_promise_fulfillment(state)
        return state

    def _current_phase(self, state: GameState) -> PhaseDef | None:
        if not self.compiled.phases:
            return None
        if 0 <= state.phase_index < len(self.compiled.phases):
            return self.compiled.phases[state.phase_index]
        return None

    # -------------------------------------------------------------------
    # Param validation
    # -------------------------------------------------------------------

    @staticmethod
    def _validate_params(
        params: dict[str, Any], param_defs: dict[str, Any], ctx: Context | None = None
    ) -> Err | None:
        """Validate params dict against ParamDef specs. Returns Err or None."""
        for name, pdef in param_defs.items():
            if name not in params:
                if pdef.default is not None:
                    params[name] = pdef.default
                else:
                    return fail(E.MISSING_PARAM, name)

            val = params[name]

            if pdef.type == "number":
                if not isinstance(val, (int, float)):
                    return fail(
                        E.INVALID_PARAM,
                        name,
                        f"expected number, got {type(val).__name__}",
                    )
                min_val = pdef.min
                max_val = pdef.max
                if isinstance(min_val, Expr) and ctx is not None:
                    min_val = evaluate(min_val, ctx)
                if isinstance(max_val, Expr) and ctx is not None:
                    max_val = evaluate(max_val, ctx)
                if min_val is not None and val < min_val:
                    return fail(E.INVALID_PARAM, name, f"must be >= {min_val}")
                if max_val is not None and val > max_val:
                    return fail(E.INVALID_PARAM, name, f"must be <= {max_val}")
            elif pdef.type in ("string", "keyword", "player", "resource"):
                if not isinstance(val, str):
                    return fail(
                        E.INVALID_PARAM,
                        name,
                        f"expected string, got {type(val).__name__}",
                    )
                if pdef.options and val not in pdef.options:
                    return fail(
                        E.INVALID_PARAM, name, f"must be one of {list(pdef.options)}"
                    )

        return None

    # -------------------------------------------------------------------
    # Deals
    # -------------------------------------------------------------------

    def start_deal(
        self,
        state: GameState,
        deal_id: str,
        actor_id: str | None = None,
        target_id: str | None = None,
        responder_id: str | None = None,
        responder_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a deal. Returns Result dict."""
        deal = self.compiled.deals.get(deal_id)
        if not deal:
            return fail(E.UNKNOWN_DEAL, deal_id)

        # Check phase allows this deal
        phase = self._current_phase(state)
        if phase and deal_id not in phase.allows and phase.allows:
            return fail(E.DEAL_NOT_ALLOWED, deal_id, phase.id)

        params = params or {}

        # Validate params against ParamDef specs
        if deal.params:
            param_ctx = self._ctx(state, actor=actor_id or "", params=params)
            err = self._validate_params(params, deal.params, param_ctx)
            if err is not None:
                return err

        # Check usage limits
        usage_check = self._check_usage_generic(state, deal_id, actor_id or "", deal)
        if not usage_check["ok"]:
            return usage_check

        # Build context
        bindings = {"params": params}
        if actor_id:
            bindings["actor"] = actor_id
            bindings["proposer"] = actor_id
        if target_id:
            bindings["target"] = target_id
        if responder_id:
            bindings["responder"] = responder_id
        if responder_ids:
            bindings["responders"] = responder_ids
        ctx = self._ctx(state, **bindings)

        # Check party filters (actor/proposer/target — single-entity parties)
        for party_name, party_def in deal.parties.items():
            if party_def.filter is not None:
                party_id = ctx.bindings.get(party_name)
                if party_id and isinstance(party_id, str):
                    party_ctx = ctx.with_binding("actor", party_id)
                    if not evaluate(party_def.filter, party_ctx):
                        return fail(E.FILTER_FAILED, party_name, party_id, deal_id)
                elif party_id and isinstance(party_id, list):
                    # Multilateral — validate each responder
                    for rid in party_id:
                        r_ctx = ctx.with_binding("actor", rid)
                        if not evaluate(party_def.filter, r_ctx):
                            return fail(E.FILTER_FAILED, party_name, rid, deal_id)

        # Validate multilateral count constraints
        responders_party = deal.parties.get("responders")
        if responders_party and responders_party.count:
            rids = responder_ids or []
            lo, hi = responders_party.count
            if len(rids) < lo or len(rids) > hi:
                return fail(
                    E.INVALID_PARAM,
                    "responders",
                    f"need {lo}-{hi} responders, got {len(rids)}",
                )

        # Check guard
        if deal.guard is not None and not evaluate(deal.guard, ctx):
            return fail(E.GUARD_FAILED, deal_id, detail=f"Condition: {deal.guard!r}")

        # Classify deal type
        has_responder_party = (
            "responder" in deal.parties or "responders" in deal.parties
        )
        has_response = bool(deal.response_options)

        if not has_responder_party or not has_response:
            # Single-party / immediate deal
            return self._execute_immediate_deal(state, deal, ctx, actor_id or "")
        else:
            # Bilateral or multilateral — create pending
            return self._create_pending_deal(
                state,
                deal,
                ctx,
                actor_id or "",
                responder_id,
                responder_ids,
                target_id,
                params,
            )

    def _execute_immediate_deal(
        self, state: GameState, deal, ctx: Context, actor_id: str
    ) -> dict[str, Any]:
        """Execute a single-party deal immediately."""
        # Resolve outcome via guard-based selection
        outcome_id, outcome = self._resolve_outcome(deal, ctx)

        # Check outcome guard (same as bilateral deals)
        if outcome.guard is not None and not evaluate(outcome.guard, ctx):
            return fail(E.GUARD_FAILED, f"{deal.id}/{outcome_id}",
                        detail=f"Condition: {outcome.guard!r}")

        # Lock stakes
        state, stakes_locked = self._lock_stakes(state, deal, ctx)
        ctx = ctx.with_binding("_stakes", stakes_locked)

        # Inject commitment callback so Eliminate can trigger commitments
        ctx = ctx.with_binding(
            "_on_eliminate", lambda eid, s: self._fire_commitments("eliminate", eid, s)
        )

        # Apply effects
        state = apply_effects(outcome.effects, state, ctx)

        # Record usage
        state = self._record_usage_generic(state, deal.id, actor_id)

        # Record decision for replay
        decision = {"type": "deal", "deal": deal.id, "proposer": actor_id}
        target = ctx.bindings.get("target")
        if target:
            decision["target"] = target
        deal_params = ctx.bindings.get("params")
        if deal_params:
            decision["params"] = deal_params
        state = state.record_decision(decision)

        # Record history
        state = state.add_history(
            "deal_executed", deal=deal.id, actor=actor_id, outcome=outcome_id
        )

        return ok(state, outcome=outcome_id)

    def _create_pending_deal(
        self,
        state: GameState,
        deal,
        ctx: Context,
        proposer_id: str,
        responder_id: str | None,
        responder_ids: list[str] | None,
        target_id: str | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a pending bilateral/multilateral deal."""
        # Lock proposer stakes
        state, stakes_locked = self._lock_stakes(state, deal, ctx)

        # Generate instance ID
        state, instance_id = state.next_id("deal")

        # Build responder map — bilateral (single) or multilateral (list)
        responders = {}
        if responder_ids:
            for rid in responder_ids:
                responders[rid] = None  # each awaiting response
        elif responder_id:
            responders[responder_id] = None

        pending = PendingDeal(
            instance_id=instance_id,
            deal_id=deal.id,
            proposer=proposer_id,
            responders=responders,
            params=params,
            stakes=stakes_locked,
            target=target_id,
        )

        new_pending = {**state.pending_deals, instance_id: pending}
        state = attrs.evolve(state, pending_deals=new_pending)

        # Record usage
        state = self._record_usage_generic(state, deal.id, proposer_id)

        # Record decision for replay
        decision: dict[str, Any] = {
            "type": "deal",
            "deal": deal.id,
            "proposer": proposer_id,
            "responder": responder_id,
            "responders": responder_ids,
            "params": params,
        }
        if target_id:
            decision["target"] = target_id
        state = state.record_decision(decision)

        state = state.add_history(
            "deal_proposed", deal=deal.id, proposer=proposer_id, instance_id=instance_id
        )

        return ok(state, instance_id=instance_id)

    def respond_to_deal(
        self, state: GameState, instance_id: str, responder_id: str, response: str
    ) -> dict[str, Any]:
        """Respond to a pending deal.

        Bilateral deals resolve immediately on first response.
        Multilateral deals collect responses and resolve per completion_rule:
          - "all"      → resolve when every responder has answered
          - "majority" → resolve when majority answered the same way
          - "any"      → resolve on first response (like bilateral)
        """
        pending = state.pending_deals.get(instance_id)
        if not pending:
            return fail(E.DEAL_NOT_FOUND, instance_id)

        deal = self.compiled.deals.get(pending.deal_id)
        if not deal:
            return fail(E.UNKNOWN_DEAL, pending.deal_id)

        if response not in deal.response_options:
            return fail(E.INVALID_RESPONSE, response, deal.response_options)

        # Validate responder is still active (alive)
        if not state.is_active(responder_id):
            return fail(E.SENDER_INACTIVE, responder_id)

        # Validate this responder is expected and hasn't already answered
        if responder_id not in pending.responders:
            return fail(E.FILTER_FAILED, "responder", responder_id, pending.deal_id)
        if pending.responders[responder_id] is not None:
            return fail(E.INVALID_RESPONSE, response, "already responded")

        is_multilateral = len(pending.responders) > 1

        # Record the individual response
        updated_responders = {**pending.responders, responder_id: response}
        updated_pending = attrs.evolve(pending, responders=updated_responders)

        # Record decision
        state = state.record_decision(
            {
                "type": "respond",
                "instance_id": instance_id,
                "responder": responder_id,
                "response": response,
            }
        )
        state = state.add_history(
            "deal_response",
            deal=pending.deal_id,
            instance_id=instance_id,
            responder=responder_id,
            response=response,
        )

        if is_multilateral:
            resolved = self._check_completion(deal, updated_pending)
        else:
            resolved = response  # bilateral: immediate

        if resolved is None:
            # Not yet complete — update pending deal in-place
            new_pending = {**state.pending_deals, instance_id: updated_pending}
            state = attrs.evolve(state, pending_deals=new_pending)
            return ok(
                state, outcome="pending", waiting=self._waiting_on(updated_pending)
            )

        # Resolve: build context and apply outcome effects
        bindings: dict[str, Any] = {
            "proposer": pending.proposer,
            "responder": responder_id,
            "responders": list(updated_responders.keys()),
            "params": pending.params,
            "_stakes": pending.stakes,
            "_on_eliminate": lambda eid, s: self._fire_commitments("eliminate", eid, s),
        }
        if pending.target:
            bindings["target"] = pending.target
        ctx = self._ctx(state, **bindings)

        outcome = deal.outcomes.get(resolved)
        if not outcome:
            return fail(E.NO_OUTCOME, resolved)

        # Check outcome guard (e.g., responder must have the asset)
        if outcome.guard is not None and not evaluate(outcome.guard, ctx):
            return fail(E.GUARD_FAILED, f"{pending.deal_id}/{resolved}",
                        detail=f"Condition: {outcome.guard!r}")

        state = apply_effects(outcome.effects, state, ctx)

        # Remove pending deal
        new_pending = {k: v for k, v in state.pending_deals.items() if k != instance_id}
        state = attrs.evolve(state, pending_deals=new_pending)

        return ok(state, outcome=resolved)

    @staticmethod
    def _check_completion(deal, pending: PendingDeal) -> str | None:
        """Check if a multilateral deal is complete. Returns resolved outcome or None."""
        responses = pending.responders
        answered = {r: v for r, v in responses.items() if v is not None}
        total = len(responses)

        rule = deal.completion_rule  # "all", "majority", "any"

        if rule == "any":
            # First response resolves
            if answered:
                return next(iter(answered.values()))
            return None

        if rule == "majority":
            # Count votes per option
            counts = Counter(answered.values())
            threshold = total // 2 + 1
            for option, count in counts.most_common():
                if count >= threshold:
                    return option
            # If everyone answered but no majority, fall back to first option
            if len(answered) == total:
                return counts.most_common(1)[0][0] if counts else None
            return None

        # Default: "all" — everyone must answer the same
        if len(answered) < total:
            return None
        # All answered — unanimous?
        unique = set(answered.values())
        if len(unique) == 1:
            return unique.pop()
        # Not unanimous — majority wins (graceful fallback)
        counts = Counter(answered.values())
        return counts.most_common(1)[0][0]

    @staticmethod
    def _waiting_on(pending: PendingDeal) -> list[str]:
        """Return list of responders who haven't answered yet."""
        return [r for r, v in pending.responders.items() if v is None]

    def _lock_stakes(
        self,
        state: GameState,
        deal,
        ctx: Context,
    ) -> tuple[GameState, dict[str, list[tuple[str, float]]]]:
        """Lock stakes — deduct resources, return locked amounts."""
        locked: dict[str, list[tuple[str, float]]] = {}
        for party, stake_list in deal.stakes.items():
            eid = ctx.bindings.get(party)
            if not eid or not isinstance(eid, str):
                continue
            party_locked = []
            for resource, amount_ref in stake_list:
                if isinstance(amount_ref, (int, float)):
                    amount = float(amount_ref)
                elif isinstance(amount_ref, str):
                    # Look up from params
                    amount = float(ctx.bindings.get("params", {}).get(amount_ref, 0))
                elif isinstance(amount_ref, Expr):
                    amount = float(evaluate(amount_ref, ctx) or 0)
                else:
                    amount = 0.0
                state = state.adjust_resource(eid, resource, -amount, self.compiled)
                party_locked.append((resource, amount))
            locked[party] = party_locked
        return state, locked

    # -------------------------------------------------------------------
    # Outcome resolution
    # -------------------------------------------------------------------

    def _resolve_outcome(self, deal, ctx: Context) -> tuple[str, Any]:
        """Select outcome: first matching guard (by priority), or default.

        OutcomeDefs with guard=None serve as default fallback.
        OutcomeDefs with guard are checked in priority order (highest first).
        """
        from engine.runtime.state import OutcomeDef

        # Sort by priority (highest first)
        candidates = sorted(
            deal.outcomes.items(),
            key=lambda kv: kv[1].priority,
            reverse=True,
        )
        default = None
        for key, odef in candidates:
            if odef.guard is None:
                if default is None:
                    default = (key, odef)
                continue
            if evaluate(odef.guard, ctx):
                return key, odef

        if default:
            return default

        # Last resort: first outcome
        key = next(iter(deal.outcomes))
        return key, deal.outcomes[key]

    # -------------------------------------------------------------------
    # Commitment system
    # -------------------------------------------------------------------

    _MAX_COMMITMENT_DEPTH = 5

    def _fire_commitments(
        self, trigger: str, trigger_entity: str, state: GameState, depth: int = 0
    ) -> GameState:
        """Fire all matching commitments for a trigger event.

        Commitments are static DSL rules (not player actions).
        Depth guard prevents infinite recursion (e.g., mutual elimination).
        """
        if depth >= self._MAX_COMMITMENT_DEPTH:
            return state

        for cdef in self.compiled.commitments.values():
            if cdef.trigger != trigger:
                continue
            if cdef.once and cdef.id in state.commitments_fired:
                continue

            ctx = self._ctx(
                state,
                actor=trigger_entity,
                target=trigger_entity,
                _on_eliminate=lambda eid, s: self._fire_commitments(
                    "eliminate", eid, s, depth + 1
                ),
            )

            if cdef.guard is not None and not evaluate(cdef.guard, ctx):
                continue

            # Fire effects
            state = apply_effects(cdef.effects, state, ctx)

            if cdef.once:
                state = attrs.evolve(
                    state,
                    commitments_fired=state.commitments_fired | {cdef.id},
                )

        # Check speech act triggers on elimination
        if trigger == "eliminate" and self.compiled.speech_acts:
            state = self._check_speech_act_triggers(state, "eliminate", trigger_entity)

        return state

    # -------------------------------------------------------------------
    # Messaging
    # -------------------------------------------------------------------

    def send_message(
        self,
        state: GameState,
        channel_id: str,
        sender_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a message through a channel. Returns Result dict.

        This is the unified entry point for both human (UI) and AI (MCP) players.
        Messages are validated against channel rules, recorded in state,
        and any channel effects (e.g., +suspicion for whisper) are applied.
        """
        from engine.runtime.state import Message

        # Validate channel exists
        cdef = self.compiled.channels.get(channel_id)
        if not cdef:
            # Allow ad-hoc private channels (pattern: private:sender:receiver)
            if not channel_id.startswith("private:"):
                return fail(E.UNKNOWN_CHANNEL, channel_id)

        # Check sender is active
        if not state.is_active(sender_id):
            return fail(E.SENDER_INACTIVE, sender_id)

        # Check phase filter
        if cdef and cdef.phase_filter and state.phase not in cdef.phase_filter:
            return fail(E.CHANNEL_NOT_AVAILABLE, channel_id, state.phase)

        # Check write filter (Expr-based access control)
        if cdef and cdef.write_filter is not None:
            ctx = self._ctx(state, actor=sender_id)
            if not evaluate(cdef.write_filter, ctx):
                return fail(E.WRITE_DENIED, sender_id, channel_id)

        # Check group membership for group channels
        if cdef and cdef.type == "group" and cdef.group:
            if not state.entity_in_group(sender_id, cdef.group):
                return fail(E.NOT_IN_GROUP, sender_id, cdef.group)

        # Create message
        state, msg_id = state.next_id("msg")
        msg = Message(
            id=msg_id,
            channel=channel_id,
            sender=sender_id,
            content=content,
            round=state.round,
            phase=state.phase,
            metadata=metadata or {},
        )
        state = state.add_message(msg)

        # Record decision for replay
        state = state.record_decision(
            {
                "type": "message",
                "channel": channel_id,
                "sender": sender_id,
                "content": content,
            }
        )

        # Apply channel effects (e.g., whisper adds suspicion)
        if cdef and cdef.effects:
            ctx = self._ctx(
                state,
                actor=sender_id,
                _on_eliminate=lambda eid, s: self._fire_commitments(
                    "eliminate", eid, s
                ),
            )
            state = apply_effects(cdef.effects, state, ctx)

        state = state.add_history(
            "message_sent",
            channel=channel_id,
            sender=sender_id,
            message_id=msg_id,
        )

        return ok(state, message_id=msg_id)

    # -------------------------------------------------------------------
    # Votes
    # -------------------------------------------------------------------

    def start_vote(
        self,
        state: GameState,
        vote_id: str,
        proposer_id: str | None = None,
        subject_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a vote. Returns Result with instance_id."""
        vote = self.compiled.votes.get(vote_id)
        if not vote:
            return fail(E.UNKNOWN_VOTE, vote_id)

        params = params or {}
        ctx = self._ctx(state, proposer=proposer_id or "", params=params)

        # Compute eligible voters
        eligible = []
        for eid in state.get_active_entity_ids():
            if vote.voters_filter is None:
                eligible.append(eid)
            elif evaluate(vote.voters_filter, ctx.with_binding("actor", eid)):
                eligible.append(eid)

        if not eligible:
            return fail(E.NO_VOTERS)

        # Generate instance ID
        state, instance_id = state.next_id("vote")

        pending = PendingVote(
            instance_id=instance_id,
            vote_id=vote_id,
            proposer=proposer_id,
            subject=subject_id,
            params=params,
            eligible=tuple(eligible),
            options=vote.options,
        )

        new_votes = {**state.pending_votes, instance_id: pending}
        state = attrs.evolve(state, pending_votes=new_votes)

        # Record decision for replay
        decision: dict[str, Any] = {
            "type": "start_vote",
            "vote_id": vote_id,
            "instance_id": instance_id,
        }
        if proposer_id:
            decision["proposer"] = proposer_id
        if subject_id:
            decision["subject"] = subject_id
        if params:
            decision["params"] = params
        state = state.record_decision(decision)

        state = state.add_history(
            "vote_started", vote=vote_id, instance_id=instance_id, proposer=proposer_id
        )

        return ok(state, instance_id=instance_id)

    def cast_vote(
        self, state: GameState, instance_id: str, voter_id: str, option: str
    ) -> dict[str, Any]:
        """Cast a vote. Auto-completes when all voters have voted."""
        pending = state.pending_votes.get(instance_id)
        if not pending:
            return fail(E.VOTE_NOT_FOUND, instance_id)

        if voter_id not in pending.eligible:
            return fail(E.NOT_ELIGIBLE, voter_id)

        if voter_id in pending.votes:
            return fail(E.ALREADY_VOTED, voter_id)

        if option not in pending.options:
            return fail(E.INVALID_OPTION, option, pending.options)

        # Record vote
        new_votes = {**pending.votes, voter_id: option}
        new_pending = attrs.evolve(pending, votes=new_votes)
        new_pending_map = {**state.pending_votes, instance_id: new_pending}
        state = attrs.evolve(state, pending_votes=new_pending_map)

        # Record decision (include vote_id for promise fulfillment scan)
        state = state.record_decision(
            {
                "type": "vote",
                "vote_id": pending.vote_id,
                "instance_id": instance_id,
                "voter": voter_id,
                "option": option,
            }
        )

        # Auto-complete if all voted
        if len(new_votes) >= len(pending.eligible):
            return self.complete_vote(state, instance_id)

        return ok(state, auto_completed=False)

    def complete_vote(self, state: GameState, instance_id: str) -> dict[str, Any]:
        """Complete a vote — tally and apply outcome effects."""
        pending = state.pending_votes.get(instance_id)
        if not pending:
            return fail(E.VOTE_NOT_FOUND, instance_id)

        vote = self.compiled.votes.get(pending.vote_id)
        if not vote:
            return fail(E.UNKNOWN_VOTE, pending.vote_id)

        # Tally votes
        tally = self._tally_votes(pending, vote)

        # Determine outcome
        outcome_id = tally.get("winner") or "failed"

        # Build context
        ctx = self._ctx(
            state,
            proposer=pending.proposer or "",
            subject=pending.subject or "",
            params=pending.params,
            _on_eliminate=lambda eid, s: self._fire_commitments("eliminate", eid, s),
        )

        # Look up outcome and apply effects
        outcome = vote.outcomes.get(outcome_id)
        if outcome:
            state = apply_effects(outcome.effects, state, ctx)

        # Remove pending vote
        new_votes = {k: v for k, v in state.pending_votes.items() if k != instance_id}
        state = attrs.evolve(state, pending_votes=new_votes)

        # Record
        state = state.add_history(
            "vote_completed",
            vote=pending.vote_id,
            instance_id=instance_id,
            tally=tally,
            outcome=outcome_id,
        )

        return ok(state, tally=tally, outcome=outcome_id, auto_completed=True)

    def _tally_votes(self, pending: PendingVote, vote) -> dict[str, Any]:
        """Tally votes and determine winner based on threshold."""
        counts: dict[str, float] = {}
        for voter, option in pending.votes.items():
            weight = pending.weights.get(voter, 1.0)
            counts[option] = counts.get(option, 0) + weight

        total = sum(counts.values())
        if not counts:
            return {"winner": None, "counts": {}, "passed": False}

        # Find option with most votes
        sorted_options = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        top_option, top_count = sorted_options[0]

        # Check threshold
        passed = False
        threshold = vote.threshold
        match threshold:
            case "plurality":
                passed = True  # highest count wins
            case "majority":
                passed = top_count > total / 2
            case "supermajority":
                passed = top_count >= total * 2 / 3
            case "unanimous":
                passed = top_count == len(pending.eligible)
            case _:
                passed = top_count > total / 2

        # Check tie
        tie = len(sorted_options) > 1 and sorted_options[1][1] == top_count

        return {
            "winner": top_option if passed and not tie else None,
            "counts": counts,
            "passed": passed,
            "tie": tie,
            "total": total,
        }

    # -------------------------------------------------------------------
    # Victory detection
    # -------------------------------------------------------------------

    def check_victory(self, state: GameState) -> dict[str, Any] | None:
        """Check all victory conditions. Returns first match or None."""
        for vdef in sorted(self.compiled.victories, key=lambda v: v.priority):
            result = self._check_one_victory(state, vdef)
            if result:
                return result
        return None

    def _check_one_victory(self, state: GameState, vdef) -> dict[str, Any] | None:
        match vdef.type:
            case "single":
                return self._check_single_victory(state, vdef)
            case "distribution":
                return self._check_distribution_victory(state, vdef)
            case _:
                return None

    def _check_single_victory(self, state, vdef) -> dict[str, Any] | None:
        """Check team/individual victory."""
        for eid in state.get_active_entity_ids():
            ctx = self._ctx(state, actor=eid, winner=eid)
            if evaluate(vdef.when, ctx):
                winner = eid
                team = vdef.team or state.get_attr(eid, "team")
                return {
                    "condition": vdef.id,
                    "type": "single",
                    "winner": winner,
                    "team": team,
                    "message": vdef.message,
                }
        return None

    def _check_distribution_victory(self, state, vdef) -> dict[str, Any] | None:
        """Check scored/distribution victory."""
        ctx = self._ctx(state)
        if not evaluate(vdef.when, ctx):
            return None

        # Compute scores
        scores = {}
        for eid in state.get_active_entity_ids():
            entity_ctx = self._ctx(state, actor=eid)
            score = evaluate(vdef.score, entity_ctx) if vdef.score is not None else 0
            scores[eid] = float(score) if score is not None else 0.0

        if not scores:
            return None

        # Deterministic tie-breaking: highest score wins; ties broken by entity ID (sorted)
        max_score = max(scores.values())
        top_players = sorted(eid for eid, s in scores.items() if s == max_score)
        winner = top_players[0]
        return {
            "condition": vdef.id,
            "type": "distribution",
            "winner": winner,
            "scores": scores,
            "message": vdef.message,
        }

    # -------------------------------------------------------------------
    # Speech Acts
    # -------------------------------------------------------------------

    def execute_speech_act(
        self,
        state: GameState,
        speech_act_id: str,
        actor_id: str,
        target_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a performative speech act. Returns Result dict."""
        sa = self.compiled.speech_acts.get(speech_act_id)
        if not sa:
            return fail(E.UNKNOWN_SPEECH_ACT, speech_act_id)

        # Check phase allows this speech act
        phase = self._current_phase(state)
        if phase and phase.allows and speech_act_id not in phase.allows:
            return fail(E.SPEECH_ACT_NOT_ALLOWED, speech_act_id, phase.id)

        # Check phase_filter on the speech act itself
        if sa.phase_filter and state.phase not in sa.phase_filter:
            return fail(E.SPEECH_ACT_NOT_ALLOWED, speech_act_id, state.phase)

        # Check actor is active
        if not state.is_active(actor_id):
            return fail(E.FILTER_FAILED, "actor", actor_id, speech_act_id)

        params = params or {}

        # Validate params
        if sa.params:
            param_ctx = self._ctx(state, actor=actor_id, params=params)
            err = self._validate_params(params, sa.params, param_ctx)
            if err is not None:
                return err

        # Check actor filter
        if sa.actor_filter is not None:
            ctx = self._ctx(state, actor=actor_id)
            if not evaluate(sa.actor_filter, ctx):
                return fail(E.FILTER_FAILED, "actor", actor_id, speech_act_id)

        # Check target filter (for accuse/inquire)
        # Bind actor=target_id for filter evaluation (same pattern as deal party filters),
        # but keep original actor accessible as "proposer" for complex filters.
        if target_id and sa.target_filter is not None:
            target_ctx = self._ctx(
                state, actor=target_id, target=target_id, proposer=actor_id
            )
            if not evaluate(sa.target_filter, target_ctx):
                return fail(E.FILTER_FAILED, "target", target_id, speech_act_id)

        # Check usage limits
        usage_check = self._check_usage_generic(state, speech_act_id, actor_id, sa)
        if not usage_check["ok"]:
            return usage_check

        # Deduct resource cost
        for resource, amount in sa.cost.items():
            current = state.get_resource(actor_id, resource)
            if current < amount:
                return fail(E.INSUFFICIENT_COST, actor_id, resource, amount, current)
            state = state.adjust_resource(actor_id, resource, -amount, self.compiled)

        # Create pending speech act
        state, instance_id = state.next_id("sa")
        pending = PendingSpeechAct(
            instance_id=instance_id,
            speech_act_id=speech_act_id,
            act_type=sa.act_type,
            actor=actor_id,
            target=target_id,
            params=params,
            round_created=state.round,
            phase_created=state.phase,
            phase_index_created=state.phase_index,
            transition_count_created=state.phase_transition_count,
            decision_index=len(state.decisions),  # for promise scan start
        )

        new_pending = {**state.pending_speech_acts, instance_id: pending}
        state = attrs.evolve(state, pending_speech_acts=new_pending)

        # Record usage
        state = self._record_usage_generic(state, speech_act_id, actor_id)

        # Record decision for replay
        decision: dict[str, Any] = {
            "type": "speech_act",
            "speech_act_id": speech_act_id,
            "act_type": sa.act_type,
            "actor": actor_id,
        }
        if target_id:
            decision["target"] = target_id
        if params:
            decision["params"] = params
        state = state.record_decision(decision)

        state = state.add_history(
            "speech_act",
            speech_act_id=speech_act_id,
            act_type=sa.act_type,
            actor=actor_id,
            target=target_id,
            instance_id=instance_id,
        )

        return ok(state, instance_id=instance_id)

    def respond_to_inquire(
        self,
        state: GameState,
        instance_id: str,
        responder_id: str,
        response: str,
    ) -> dict[str, Any]:
        """Respond to an inquire speech act."""
        pending = state.pending_speech_acts.get(instance_id)
        if not pending:
            return fail(E.SPEECH_ACT_NOT_FOUND, instance_id)

        if pending.act_type != "inquire":
            return fail(E.SPEECH_ACT_NOT_FOUND, instance_id)

        if responder_id != pending.target:
            return fail(
                E.FILTER_FAILED, "responder", responder_id, pending.speech_act_id
            )

        if pending.inquire_response is not None:
            return fail(E.INQUIRE_ALREADY_RESPONDED, instance_id)

        sa = self.compiled.speech_acts.get(pending.speech_act_id)
        if (
            sa
            and sa.inquire_response_options
            and response not in sa.inquire_response_options
        ):
            return fail(E.INVALID_RESPONSE, response, sa.inquire_response_options)

        # Update pending speech act with response
        updated = attrs.evolve(pending, inquire_response=response)
        new_pending = {**state.pending_speech_acts, instance_id: updated}
        state = attrs.evolve(state, pending_speech_acts=new_pending)

        # Record decision
        state = state.record_decision(
            {
                "type": "inquire_response",
                "instance_id": instance_id,
                "responder": responder_id,
                "response": response,
            }
        )

        state = state.add_history(
            "inquire_response",
            instance_id=instance_id,
            responder=responder_id,
            response=response,
        )

        return ok(state)

    def endorse_speech_act(
        self,
        state: GameState,
        target_instance_id: str,
        endorser_id: str,
    ) -> dict[str, Any]:
        """Endorse another player's pending speech act."""
        pending = state.pending_speech_acts.get(target_instance_id)
        if not pending or pending.status != "pending":
            return fail(E.NOTHING_TO_ENDORSE, target_instance_id)

        sa = self.compiled.speech_acts.get(pending.speech_act_id)
        if not sa or not sa.endorsable:
            return fail(E.NOTHING_TO_ENDORSE, target_instance_id)

        if endorser_id == pending.actor:
            return fail(E.CANNOT_ENDORSE_OWN)

        if endorser_id in pending.endorsers:
            return fail(E.ALREADY_ENDORSED, endorser_id, target_instance_id)

        if not state.is_active(endorser_id):
            return fail(E.FILTER_FAILED, "endorser", endorser_id, pending.speech_act_id)

        # Deduct endorsement cost (endorsement_cost if set, otherwise same as act cost)
        endorse_cost = (
            sa.endorsement_cost if sa.endorsement_cost is not None else sa.cost
        )
        for resource, amount in endorse_cost.items():
            current = state.get_resource(endorser_id, resource)
            if current < amount:
                return fail(E.INSUFFICIENT_COST, endorser_id, resource, amount, current)
            state = state.adjust_resource(endorser_id, resource, -amount, self.compiled)

        # Add endorser
        updated = attrs.evolve(pending, endorsers=(*pending.endorsers, endorser_id))
        new_pending = {**state.pending_speech_acts, target_instance_id: updated}
        state = attrs.evolve(state, pending_speech_acts=new_pending)

        # Record decision
        state = state.record_decision(
            {
                "type": "endorse",
                "target_instance_id": target_instance_id,
                "endorser": endorser_id,
            }
        )

        state = state.add_history(
            "endorsement",
            target_instance_id=target_instance_id,
            endorser=endorser_id,
            speech_act_id=pending.speech_act_id,
        )

        return ok(state)

    def _verify_speech_act(
        self, state: GameState, pending: PendingSpeechAct
    ) -> GameState:
        """Verify a single speech act against current state.

        Determines verified_true from verify_condition / promise_fulfilled,
        then delegates to the shared _verify_pending_speech_act in effects.py.
        """
        from engine.runtime.effects import _verify_pending_speech_act

        sa = self.compiled.speech_acts.get(pending.speech_act_id)
        if not sa:
            return state

        # Evaluate verify_condition
        ctx = self._ctx(
            state,
            actor=pending.actor,
            target=pending.target or "",
            params=pending.params,
            _on_eliminate=lambda eid, s: self._fire_commitments("eliminate", eid, s),
        )

        verified_true = False
        if sa.verify_condition is not None:
            verified_true = bool(evaluate(sa.verify_condition, ctx))
        elif pending.act_type == "promise" and pending.promise_fulfilled is not None:
            verified_true = pending.promise_fulfilled

        return _verify_pending_speech_act(state, pending, verified_true, ctx)

    def _check_speech_act_triggers(
        self, state: GameState, trigger: str, entity_id: str | None = None
    ) -> GameState:
        """Check all pending speech acts for the given trigger."""
        to_verify = []
        for sa in state.pending_speech_acts.values():
            if sa.status != "pending":
                continue
            sa_def = self.compiled.speech_acts.get(sa.speech_act_id)
            if not sa_def:
                continue
            if trigger not in sa_def.verify_triggers:
                continue

            if trigger == "eliminate" and entity_id:
                # Verify if eliminated entity is actor or target
                if entity_id in (sa.actor, sa.target):
                    to_verify.append(sa)
            else:
                to_verify.append(sa)

        for sa in to_verify:
            # Re-fetch from state in case previous verification changed it
            current = state.pending_speech_acts.get(sa.instance_id)
            if current and current.status == "pending":
                state = self._verify_speech_act(state, current)

        return state

    def _check_inquire_deadlines(self, state: GameState) -> GameState:
        """Check for expired inquire acts and apply silence penalties."""
        for sa in list(state.pending_speech_acts.values()):
            if sa.act_type != "inquire" or sa.inquire_response is not None:
                continue
            sa_def = self.compiled.speech_acts.get(sa.speech_act_id)
            if not sa_def:
                continue

            # Count phases elapsed since inquire was created
            if sa.transition_count_created > 0:
                phases_elapsed = (
                    state.phase_transition_count - sa.transition_count_created
                )
            else:
                # Legacy fallback for speech acts created before non-linear phases
                phases_elapsed = state.phase_index - sa.phase_index_created
            if phases_elapsed >= sa_def.inquire_deadline:
                # Apply silence penalty to target
                if sa.target and sa_def.inquire_silence_effects:
                    ctx = self._ctx(
                        state,
                        actor=sa.target,  # "actor" = penalty target
                        target=sa.target,
                        proposer=sa.actor,  # original inquirer accessible
                        _on_eliminate=lambda eid, s: self._fire_commitments(
                            "eliminate", eid, s
                        ),
                    )
                    state = apply_effects(sa_def.inquire_silence_effects, state, ctx)

                # Mark as expired
                expired = attrs.evolve(sa, status="expired")
                new_pending = {
                    k: v
                    for k, v in state.pending_speech_acts.items()
                    if k != sa.instance_id
                }
                state = attrs.evolve(
                    state,
                    pending_speech_acts=new_pending,
                    resolved_speech_acts=(*state.resolved_speech_acts, expired),
                )

                state = state.add_history(
                    "inquire_expired",
                    instance_id=sa.instance_id,
                    target=sa.target,
                )

        return state

    def _check_promise_fulfillment(self, state: GameState) -> GameState:
        """Check if promised actions were performed."""
        for sa in list(state.pending_speech_acts.values()):
            if sa.act_type != "promise":
                continue
            sa_def = self.compiled.speech_acts.get(sa.speech_act_id)
            if not sa_def or not sa_def.promise_action:
                continue

            # Scan decisions AFTER the promise was created for matching action by actor.
            # Check both deal and vote decision types (e.g. promise_action="mission_vote").
            promise_found = False
            for decision in state.decisions[sa.decision_index :]:
                dtype = decision.get("type", "")
                if dtype == "deal" and decision.get("deal") == sa_def.promise_action:
                    if (
                        decision.get("proposer") == sa.actor
                        or decision.get("actor") == sa.actor
                    ):
                        promise_found = True
                        break
                elif (
                    dtype == "vote" and decision.get("vote_id") == sa_def.promise_action
                ):
                    if decision.get("voter") == sa.actor:
                        promise_found = True
                        break

            # Check deadline
            rounds_elapsed = state.round - sa.round_created
            if (
                sa_def.promise_deadline is not None
                and rounds_elapsed >= sa_def.promise_deadline
            ):
                # Deadline reached — resolve
                updated = attrs.evolve(sa, promise_fulfilled=promise_found)
                new_pending = {**state.pending_speech_acts, sa.instance_id: updated}
                state = attrs.evolve(state, pending_speech_acts=new_pending)
                state = self._verify_speech_act(
                    state, state.pending_speech_acts.get(sa.instance_id, updated)
                )
            elif promise_found and sa.promise_fulfilled is None:
                # Promise fulfilled early
                updated = attrs.evolve(sa, promise_fulfilled=True)
                new_pending = {**state.pending_speech_acts, sa.instance_id: updated}
                state = attrs.evolve(state, pending_speech_acts=new_pending)
                state = self._verify_speech_act(
                    state, state.pending_speech_acts.get(sa.instance_id, updated)
                )

        return state

    def _check_usage_generic(
        self, state: GameState, action_id: str, actor_id: str, action_def
    ) -> dict[str, Any]:
        """Check per-round/per-phase/per-game usage limits (generic for deals + speech acts)."""
        key = f"{actor_id}:{action_id}"
        usage = state.usage.get(key, {})

        if action_def.per_round is not None:
            round_key = f"round:{state.round}"
            used = usage.get(round_key, 0)
            if used >= action_def.per_round:
                return fail(E.USAGE_LIMIT, action_id, "this round",
                            detail=f"{used}/{action_def.per_round} per round (resets next round)")

        if action_def.per_phase is not None:
            phase_key = f"phase:{state.phase}"
            used = usage.get(phase_key, 0)
            if used >= action_def.per_phase:
                return fail(E.USAGE_LIMIT, action_id, "this phase",
                            detail=f"{used}/{action_def.per_phase} per phase (resets next phase)")

        if action_def.per_game is not None:
            game_key = "game"
            used = usage.get(game_key, 0)
            if used >= action_def.per_game:
                return fail(E.USAGE_LIMIT, action_id, "for game",
                            detail=f"{used}/{action_def.per_game} per game (permanent)")

        return ok(state)

    def _record_usage_generic(
        self, state: GameState, action_id: str, actor_id: str
    ) -> GameState:
        """Increment usage counters (generic for deals + speech acts)."""
        key = f"{actor_id}:{action_id}"
        usage = dict(state.usage)
        counts = dict(usage.get(key, {}))
        counts[f"round:{state.round}"] = counts.get(f"round:{state.round}", 0) + 1
        counts[f"phase:{state.phase}"] = counts.get(f"phase:{state.phase}", 0) + 1
        counts["game"] = counts.get("game", 0) + 1
        usage[key] = counts
        return attrs.evolve(state, usage=usage)

    def end_game(self, state: GameState, victory: dict[str, Any]) -> GameState:
        """End the game with a victory result."""
        # Verify all remaining speech acts on game end
        state = self._check_speech_act_triggers(state, "game_end")
        state = attrs.evolve(state, status="ended", victory_result=victory)
        state = state.add_history("game_ended", victory=victory)
        return state


# ---------------------------------------------------------------------------
# Game parameter helpers
# ---------------------------------------------------------------------------


def _merge_game_params(
    compiled: CompiledGame, user_params: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge compiled defaults with user overrides, validate types and ranges."""
    import warnings

    result: dict[str, Any] = {}
    provided = user_params or {}

    for pid, pdef in compiled.game_params.items():
        if pid in provided:
            val = provided[pid]
            if pdef.type == "number":
                if not isinstance(val, (int, float)):
                    raise ValueError(
                        f"Game param '{pid}': expected number, got {type(val).__name__}"
                    )
                if pdef.min is not None and val < pdef.min:
                    raise ValueError(f"Game param '{pid}': {val} < min {pdef.min}")
                if pdef.max is not None and val > pdef.max:
                    raise ValueError(f"Game param '{pid}': {val} > max {pdef.max}")
            elif pdef.type == "keyword":
                if pdef.options and val not in pdef.options:
                    raise ValueError(
                        f"Game param '{pid}': '{val}' not in {list(pdef.options)}"
                    )
            result[pid] = val
        else:
            result[pid] = pdef.default

    unknown = set(provided) - set(compiled.game_params)
    if unknown:
        warnings.warn(
            f"Unknown game params ignored: {sorted(unknown)}. "
            f"Available: {sorted(compiled.game_params)}",
            stacklevel=3,
        )

    return result
