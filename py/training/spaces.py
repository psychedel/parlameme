"""Action and observation space builders for RL training.

Builds flat discrete action spaces with masking from CompiledGame.
Handles: deals (with discretized params), votes, responses, advance_phase, noop.

Observation encoder flattens view_for() output into fixed-size numpy arrays.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from engine.runtime.state import CompiledGame, GameState, Visibility, view_for
from mcp.formatters import can_player_use_deal, can_player_start_vote
from mcp.schema import classify_parties


# ---------------------------------------------------------------------------
# Action space
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionSlot:
    """One slot in the flat discrete action table."""

    type: str  # "noop", "advance_phase", "deal", "vote", "respond"
    meta: dict[str, Any] = field(default_factory=dict)


class ActionSpaceBuilder:
    """Build flat discrete action space from CompiledGame.

    Action layout:
        [0]: noop
        [1]: advance_phase
        [2..]: deals, votes, respond actions
    """

    def __init__(
        self,
        compiled: CompiledGame,
        num_bins: int = 10,
        player_ids: list[str] | None = None,
    ):
        self.compiled = compiled
        self.num_bins = num_bins
        self._player_ids = player_ids or []

        self._action_table: list[ActionSlot] = []
        self._build_action_table()

    def _build_action_table(self) -> None:
        """Enumerate all possible discrete actions."""
        self._action_table.append(ActionSlot("noop"))
        self._action_table.append(ActionSlot("advance_phase"))

        for deal_id, deal in self.compiled.deals.items():
            param_combos = self._enumerate_param_combos(deal.params)
            classification = classify_parties(deal.parties)

            if classification.type == "bilateral":
                for target_idx in range(len(self._player_ids)):
                    for combo in param_combos:
                        self._action_table.append(ActionSlot(
                            "deal",
                            {
                                "deal_id": deal_id,
                                "target_player_idx": target_idx,
                                "params": combo,
                                "bilateral": True,
                                "respondent_key": classification.respondent,
                                "target_key": classification.target,
                            },
                        ))
            elif classification.type == "immediate":
                for combo in param_combos:
                    self._action_table.append(ActionSlot(
                        "deal",
                        {"deal_id": deal_id, "params": combo, "bilateral": False},
                    ))
            # Skip multilateral for MVP

        for vote_id, vote in self.compiled.votes.items():
            if vote.subject is not None:
                for subj_idx in range(len(self._player_ids)):
                    for option in vote.options:
                        self._action_table.append(ActionSlot(
                            "vote",
                            {"vote_id": vote_id, "option": option, "subject_idx": subj_idx},
                        ))
            else:
                for option in vote.options:
                    self._action_table.append(ActionSlot(
                        "vote",
                        {"vote_id": vote_id, "option": option},
                    ))

        for deal_id, deal in self.compiled.deals.items():
            for resp in deal.response_options:
                self._action_table.append(ActionSlot(
                    "respond",
                    {"deal_id": deal_id, "response": resp},
                ))

    def _enumerate_param_combos(self, params: dict[str, Any]) -> list[dict]:
        """Enumerate all discrete parameter combinations."""
        if not params:
            return [{}]

        param_lists: dict[str, list] = {}
        for param_id, pdef in params.items():
            if pdef.type == "number":
                lo = pdef.min if isinstance(pdef.min, (int, float)) else 1
                hi = pdef.max if isinstance(pdef.max, (int, float)) else max(lo * 10, lo + 100)
                if hi <= lo:
                    hi = lo + 1
                bins = np.linspace(lo, hi, self.num_bins).tolist()
                if isinstance(lo, int) and isinstance(hi, int):
                    bins = sorted(set(int(round(b)) for b in bins))
                param_lists[param_id] = bins
            elif pdef.type in ("keyword", "resource") and pdef.options:
                param_lists[param_id] = list(pdef.options)
            elif pdef.type == "player":
                param_lists[param_id] = list(range(len(self._player_ids)))
            else:
                param_lists[param_id] = [pdef.default] if pdef.default is not None else [""]

        keys = list(param_lists.keys())
        values = [param_lists[k] for k in keys]
        return [dict(zip(keys, combo)) for combo in itertools.product(*values)]

    @property
    def num_actions(self) -> int:
        return len(self._action_table)

    def action_mask(
        self,
        agent: str,
        state: GameState,
        compiled: CompiledGame,
    ) -> np.ndarray:
        """Compute binary action mask for current agent."""
        mask = np.zeros(len(self._action_table), dtype=np.int8)

        if state.status == "ended" or not state.is_active(agent):
            mask[0] = 1
            return mask

        mask[0] = 1  # noop always available

        phase_def = None
        for p in compiled.phases:
            if p.id == state.phase:
                phase_def = p
                break

        if phase_def is None:
            return mask

        has_pending = _has_pending_action(state, agent)
        has_any_pending = bool(state.pending_deals) or bool(state.pending_votes)
        if not has_any_pending:
            mask[1] = 1  # advance_phase

        allowed = set(phase_def.allows)
        active = set(state.get_active_entity_ids())

        for idx in range(2, len(self._action_table)):
            slot = self._action_table[idx]

            if slot.type == "deal":
                deal_id = slot.meta["deal_id"]
                if deal_id not in allowed:
                    continue
                if not can_player_use_deal(state, compiled, deal_id, agent):
                    continue
                if slot.meta.get("bilateral"):
                    target_idx = slot.meta["target_player_idx"]
                    if target_idx >= len(self._player_ids):
                        continue
                    target = self._player_ids[target_idx]
                    if target == agent or target not in active:
                        continue
                mask[idx] = 1

            elif slot.type == "vote":
                vote_id = slot.meta["vote_id"]
                pending = _find_pending_vote(state, vote_id, agent)
                if pending:
                    if (slot.meta["option"] in pending.options
                            and agent in pending.eligible
                            and agent not in pending.votes):
                        mask[idx] = 1
                elif (vote_id in allowed and not has_pending
                      and can_player_start_vote(state, compiled, vote_id, agent)):
                    mask[idx] = 1

            elif slot.type == "respond":
                for pd in state.pending_deals.values():
                    if (pd.deal_id == slot.meta["deal_id"]
                            and agent in pd.responders
                            and pd.responders[agent] is None):
                        deal = compiled.deals.get(pd.deal_id)
                        if deal and slot.meta["response"] in deal.response_options:
                            mask[idx] = 1
                            break

        return mask

    def decode_action(
        self,
        action_idx: int,
        agent: str,
        state: GameState,
    ) -> dict[str, Any]:
        """Decode action index into an action specification dict."""
        if action_idx < 0 or action_idx >= len(self._action_table):
            return {"type": "noop"}

        slot = self._action_table[action_idx]

        if slot.type == "noop":
            return {"type": "noop"}

        if slot.type == "advance_phase":
            return {"type": "advance_phase"}

        if slot.type == "deal":
            spec: dict[str, Any] = {"type": "deal", "deal_id": slot.meta["deal_id"]}
            params = dict(slot.meta.get("params", {}))

            # Resolve player indices to IDs
            deal = self.compiled.deals.get(slot.meta["deal_id"])
            if deal:
                for k, v in list(params.items()):
                    pdef = deal.params.get(k)
                    if pdef and pdef.type == "player" and isinstance(v, int):
                        if 0 <= v < len(self._player_ids):
                            params[k] = self._player_ids[v]

            if params:
                spec["params"] = params

            if slot.meta.get("bilateral"):
                target_idx = slot.meta["target_player_idx"]
                if 0 <= target_idx < len(self._player_ids):
                    target_pid = self._player_ids[target_idx]
                    if slot.meta.get("respondent_key"):
                        spec["responder"] = target_pid
                    if slot.meta.get("target_key"):
                        spec["target"] = target_pid
                    if not slot.meta.get("respondent_key") and not slot.meta.get("target_key"):
                        spec["responder"] = target_pid

            return spec

        if slot.type == "vote":
            vote_id = slot.meta["vote_id"]
            pending = _find_pending_vote(state, vote_id, agent)
            if (pending and agent in pending.eligible
                    and agent not in pending.votes):
                return {
                    "type": "vote_cast",
                    "instance_id": pending.instance_id,
                    "option": slot.meta["option"],
                }
            spec = {"type": "vote_start", "vote_id": vote_id}
            if "subject_idx" in slot.meta:
                subj_idx = slot.meta["subject_idx"]
                if 0 <= subj_idx < len(self._player_ids):
                    spec["subject"] = self._player_ids[subj_idx]
            return spec

        if slot.type == "respond":
            for pd in state.pending_deals.values():
                if (pd.deal_id == slot.meta["deal_id"]
                        and agent in pd.responders
                        and pd.responders[agent] is None):
                    return {
                        "type": "respond",
                        "instance_id": pd.instance_id,
                        "response": slot.meta["response"],
                    }
            return {"type": "noop"}

        return {"type": "noop"}


# ---------------------------------------------------------------------------
# Observation encoder
# ---------------------------------------------------------------------------


class ObservationEncoder:
    """Encode view_for() output into fixed-size numpy array.

    Components:
        phase one-hot [num_phases]
        round normalized [1]
        self resources [num_resources]
        self attrs [num_attrs]
        per-player features [max_players * (1 + num_resources + num_attrs)]
        game vars [10]
        pending indicators [3]
    """

    def __init__(self, compiled: CompiledGame, max_players: int):
        self.compiled = compiled
        self.max_players = max_players
        self._resource_ids = sorted(compiled.resources.keys())
        self._attr_ids = sorted(compiled.attrs_defs.keys())
        self._phase_ids = [p.id for p in compiled.phases]

        phase_dim = len(self._phase_ids)
        round_dim = 1
        self_res_dim = len(self._resource_ids)
        self_attr_dim = len(self._attr_ids)
        player_dim = max_players * (1 + len(self._resource_ids) + len(self._attr_ids))
        game_var_dim = 10
        pending_dim = 3

        self._obs_dim = (
            phase_dim + round_dim + self_res_dim + self_attr_dim
            + player_dim + game_var_dim + pending_dim
        )

    @property
    def obs_dim(self) -> int:
        return self._obs_dim

    def encode(
        self,
        state: GameState,
        observer_id: str,
        compiled: CompiledGame,
    ) -> np.ndarray:
        """Encode game state as numpy array from observer's perspective."""
        view = view_for(state, observer_id, compiled)
        obs = np.zeros(self._obs_dim, dtype=np.float32)
        offset = 0

        # Phase one-hot
        phase = view["phase"]
        if phase in self._phase_ids:
            obs[offset + self._phase_ids.index(phase)] = 1.0
        offset += len(self._phase_ids)

        # Round normalized (assume max ~20)
        obs[offset] = min(view["round"] / 20.0, 1.0)
        offset += 1

        # Self resources
        me = view["entities"].get(observer_id, {})
        my_res = me.get("resources", {})
        for i, rid in enumerate(self._resource_ids):
            val = my_res.get(rid, 0)
            obs[offset + i] = self._norm_resource(val, rid)
        offset += len(self._resource_ids)

        # Self attrs
        my_attrs = me.get("attrs", {})
        for i, aid in enumerate(self._attr_ids):
            obs[offset + i] = self._encode_attr(my_attrs.get(aid), aid)
        offset += len(self._attr_ids)

        # Per-player features
        all_pids = list(view["entities"].keys())
        for p_idx in range(self.max_players):
            if p_idx < len(all_pids):
                pid = all_pids[p_idx]
                entity = view["entities"][pid]
                obs[offset] = 1.0 if entity["active"] else 0.0
                offset += 1
                p_res = entity.get("resources", {})
                for i, rid in enumerate(self._resource_ids):
                    obs[offset + i] = self._norm_resource(p_res.get(rid, 0), rid)
                offset += len(self._resource_ids)
                p_attrs = entity.get("attrs", {})
                for i, aid in enumerate(self._attr_ids):
                    obs[offset + i] = self._encode_attr(p_attrs.get(aid), aid)
                offset += len(self._attr_ids)
            else:
                offset += 1 + len(self._resource_ids) + len(self._attr_ids)

        # Game vars (first 10, normalized)
        game_vars = view.get("vars", {})
        var_keys = sorted(game_vars.keys())[:10]
        for i, key in enumerate(var_keys):
            val = game_vars[key]
            if isinstance(val, (int, float)):
                obs[offset + i] = float(np.clip(val / 100.0, -1.0, 1.0))
        offset += 10

        # Pending indicators: my pending deal, my pending vote, any global pending
        has_deal = any(
            observer_id in pd.responders and pd.responders[observer_id] is None
            for pd in state.pending_deals.values()
        )
        has_vote = any(
            observer_id in pv.eligible and observer_id not in pv.votes
            for pv in state.pending_votes.values()
        )
        has_any_pending = bool(state.pending_deals) or bool(state.pending_votes)
        obs[offset] = 1.0 if has_deal else 0.0
        obs[offset + 1] = 1.0 if has_vote else 0.0
        obs[offset + 2] = 1.0 if has_any_pending else 0.0

        return obs

    def _norm_resource(self, val: int | float, rid: str) -> float:
        rdef = self.compiled.resources.get(rid)
        if rdef is None:
            return 0.0
        if rdef.initial and rdef.initial > 0:
            return float(np.clip(val / (rdef.initial * 2), -1.0, 1.0))
        lo = rdef.bounds[0] if rdef.bounds[0] is not None else 0
        hi = rdef.bounds[1] if rdef.bounds[1] is not None else 100
        if hi == lo:
            return 0.0
        return float(np.clip((val - lo) / (hi - lo) * 2 - 1, -1.0, 1.0))

    def _encode_attr(self, val: Any, attr_id: str) -> float:
        adef = self.compiled.attrs_defs.get(attr_id)
        if adef and adef.values:
            if val in adef.values:
                return adef.values.index(val) / max(1, len(adef.values) - 1)
            return 0.0
        if isinstance(val, bool):
            return 1.0 if val else 0.0
        if isinstance(val, (int, float)):
            return float(np.clip(val / 100.0, -1.0, 1.0))
        return 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_pending_action(state: GameState, agent: str) -> bool:
    for pd in state.pending_deals.values():
        if agent in pd.responders and pd.responders[agent] is None:
            return True
    for pv in state.pending_votes.values():
        if agent in pv.eligible and agent not in pv.votes:
            return True
    return False


def _find_pending_vote(state: GameState, vote_id: str, agent: str):
    for pv in state.pending_votes.values():
        if pv.vote_id == vote_id and agent in pv.eligible and agent not in pv.votes:
            return pv
    return None
