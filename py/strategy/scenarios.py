"""Scenario extraction and testing — sandbox for strategy evaluation.

Extracts interesting decision points from game archives, then tests a
strategy against them either deterministically (free, instant) or via
LLM (cheap, ~2 sec per scenario).

A Scenario is a frozen game state + available actions + context —
a specific moment where a player must make a decision.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any

import attrs

from engine.archive import Archive, _apply_decision, load_archive  # noqa: PLC2701
from engine.runtime.core import GameRuntime
from engine.runtime.state import CompiledGame, GameState, view_for
from games import REGISTRY as GAME_REGISTRY
from mcp.formatters import format_available_actions, format_status

log = logging.getLogger(__name__)

ARCHIVE_DIR = Path("data/archives")


# ---------------------------------------------------------------------------
# Scenario data structure
# ---------------------------------------------------------------------------


@attrs.frozen
class Scenario:
    """A decision point extracted from a game archive."""

    id: str  # "{archive_id}@{step}"
    game_id: str
    archive_id: str
    step: int  # decision index in archive
    player_id: str  # who must decide

    # Human-readable context
    phase: str
    round: int
    description: str  # what the player sees (formatted status)
    available_actions: str  # formatted actions list

    # What actually happened (ground truth)
    actual_decision: dict[str, Any]

    # Tags for filtering
    category: str = ""  # "deal", "vote", "respond", "speech_act"


# ---------------------------------------------------------------------------
# Scenario extraction
# ---------------------------------------------------------------------------


def extract_scenarios(
    game_id: str,
    limit: int = 10,
    archive_dir: Path = ARCHIVE_DIR,
) -> list[Scenario]:
    """Extract interesting decision points from archives.

    Scans archives for the given game, replays them to find moments where
    a player had multiple options to choose from. Returns up to `limit`
    scenarios sorted by interestingness.
    """
    compiled = GAME_REGISTRY.get(game_id)
    if not compiled:
        return []

    archives = _load_game_archives(game_id, archive_dir)
    if not archives:
        return []

    all_scenarios: list[Scenario] = []

    for archive_id, archive in archives:
        try:
            scenarios = _extract_from_archive(archive_id, archive, compiled)
            all_scenarios.extend(scenarios)
        except Exception as exc:
            log.debug("Failed to extract from %s: %s", archive_id, exc)

    # Sort by interestingness (deals > votes > responses, later rounds better)
    all_scenarios.sort(key=_interestingness, reverse=True)

    # Deduplicate by phase+round to get variety
    seen = set()
    unique: list[Scenario] = []
    for s in all_scenarios:
        key = (s.phase, s.round, s.category)
        if key not in seen:
            seen.add(key)
            unique.append(s)

    return unique[:limit]


def _load_game_archives(game_id: str, archive_dir: Path) -> list[tuple[str, Archive]]:
    """Load all archives for a game type."""
    if not archive_dir.exists():
        return []

    result = []
    for path in archive_dir.glob("*.json"):
        try:
            archive = load_archive(path)
            if archive.game_id == game_id and len(archive.decisions) >= 5:
                result.append((path.stem, archive))
        except Exception:
            continue

    # Shuffle to get variety across runs
    random.shuffle(result)
    return result[:10]  # Process at most 10 archives


def _extract_from_archive(
    archive_id: str, archive: Archive, compiled: CompiledGame
) -> list[Scenario]:
    """Replay an archive and extract decision points."""
    runtime = GameRuntime(compiled)
    state = runtime.start_game(list(archive.players), archive.seed)
    state = runtime.run_setup(state)

    scenarios: list[Scenario] = []

    for i, decision in enumerate(archive.decisions):
        # Only extract player-initiated decisions (not timeouts, auto-advances)
        dtype = decision.get("type", "")
        if dtype in (
            "advance_phase",
            "timeout_advance",
            "timeout_expire_deal",
            "timeout_auto_vote",
            "message",
        ):
            state = _apply_decision(runtime, state, decision)
            continue

        # Determine who made this decision
        player_id = _decision_player(decision)
        if not player_id or player_id not in state.entities:
            state = _apply_decision(runtime, state, decision)
            continue

        # Only include if player is active
        entity = state.entities.get(player_id)
        if not entity or not entity.active:
            state = _apply_decision(runtime, state, decision)
            continue

        # Build scenario from current state BEFORE applying decision
        try:
            view = view_for(state, player_id, compiled)
            view["_state"] = state  # formatters expect this
            status_text = format_status(view, compiled, player_id)
            actions_text = format_available_actions(state, compiled, player_id)
        except Exception:
            state = _apply_decision(runtime, state, decision)
            continue

        # Skip if no meaningful actions available
        if "no actions" in actions_text.lower() or len(actions_text) < 20:
            state = _apply_decision(runtime, state, decision)
            continue

        scenario = Scenario(
            id=f"{archive_id}@{i}",
            game_id=compiled.id,
            archive_id=archive_id,
            step=i,
            player_id=player_id,
            phase=state.phase,
            round=state.round,
            description=status_text[:500],
            available_actions=actions_text[:500],
            actual_decision=dict(decision),
            category=dtype,
        )
        scenarios.append(scenario)

        # Apply decision and continue
        state = _apply_decision(runtime, state, decision)

    return scenarios


def _decision_player(decision: dict[str, Any]) -> str:
    """Extract the acting player from a decision dict."""
    for key in ("proposer", "actor", "voter", "responder", "sender"):
        val = decision.get(key)
        if val:
            return val
    return ""


def _interestingness(s: Scenario) -> float:
    """Score a scenario by how interesting it is for testing."""
    score = 0.0
    # Later rounds are more interesting (more context)
    score += s.round * 2
    # Deals are more strategic than votes
    if s.category == "deal":
        score += 10
    elif s.category == "respond":
        score += 8
    elif s.category == "vote":
        score += 6
    elif s.category == "speech_act":
        score += 5
    # Longer action descriptions mean more choices
    score += len(s.available_actions) / 100
    return score


# ---------------------------------------------------------------------------
# Synthetic scenario generation (no archives needed)
# ---------------------------------------------------------------------------


def generate_synthetic_scenarios(
    game_id: str,
    count: int = 5,
    seed: int = 42,
) -> list[Scenario]:
    """Generate scenarios by playing random valid moves — no archives needed.

    Starts a game with dummy players, advances through phases, picks random
    deals from what's available, and captures each decision point as a Scenario.
    Works for any game in the registry.
    """
    compiled = GAME_REGISTRY.get(game_id)
    if not compiled:
        return []

    min_p = compiled.min_players
    players = [f"bot_{i}" for i in range(min_p)]
    runtime = GameRuntime(compiled)
    state = runtime.start_game(players, seed=seed)
    state = runtime.run_setup(state)

    scenarios: list[Scenario] = []
    rng = random.Random(seed)

    for step in range(200):  # safety bound
        if state.status != "active":
            break
        if len(scenarios) >= count:
            break

        # Try to find a player who can act
        active_players = [p for p in players if state.entities.get(p, _DUMMY).active]
        if not active_players:
            break

        rng.shuffle(active_players)
        acted = False

        for player_id in active_players:
            # Check available deals
            for deal_id in compiled.deals:
                result = runtime.start_deal(state, deal_id, actor_id=player_id)
                if not result["ok"]:
                    continue

                # Capture scenario BEFORE applying
                try:
                    view = view_for(state, player_id, compiled)
                    view["_state"] = state
                    status_text = format_status(view, compiled, player_id)
                    actions_text = format_available_actions(state, compiled, player_id)
                except Exception:
                    actions_text = ""
                    status_text = ""

                if len(actions_text) >= 20:
                    scenario = Scenario(
                        id=f"synthetic-{game_id}@{step}",
                        game_id=game_id,
                        archive_id=f"synthetic-{seed}",
                        step=step,
                        player_id=player_id,
                        phase=state.phase,
                        round=state.round,
                        description=status_text[:500],
                        available_actions=actions_text[:500],
                        actual_decision={"type": "deal", "deal": deal_id, "actor": player_id},
                        category="deal",
                    )
                    scenarios.append(scenario)

                state = result["state"]
                acted = True
                break  # one action per player per step

            if acted:
                break

        if not acted:
            # No deal worked — try advancing phase
            try:
                state = runtime.advance_phase(state)
            except Exception:
                break

    return scenarios[:count]


class _DummyEntity:
    """Minimal stand-in for missing entity lookups."""

    active = False


_DUMMY = _DummyEntity()


# ---------------------------------------------------------------------------
# Deterministic testing (free, instant)
# ---------------------------------------------------------------------------


@attrs.frozen
class DeterministicResult:
    """Result of rule-based strategy evaluation on a scenario."""

    scenario_id: str
    matches: tuple[str, ...]  # which strategy rules matched
    confidence: str  # "high", "medium", "low"
    suggestion: str  # what the strategy suggests
    actual: str  # what actually happened


def evaluate_deterministic(
    scenario: Scenario, strategy_dict: dict[str, Any]
) -> DeterministicResult:
    """Test strategy rules against a scenario without LLM.

    Parses strategy text fields for keywords/patterns and checks if they
    match the scenario's available actions. Not 100% accurate but instant
    and free — good for quick iteration.
    """
    matches: list[str] = []
    suggestion_parts: list[str] = []

    phase = scenario.phase
    category = scenario.category
    actual_action = scenario.actual_decision.get(
        "deal",
        scenario.actual_decision.get(
            "vote_id", scenario.actual_decision.get("type", "unknown")
        ),
    )

    # Check phase tactics
    phase_tactics = strategy_dict.get("phase_tactics", {})
    tactic = phase_tactics.get(phase, "")
    if tactic:
        matches.append(f"phase_tactic[{phase}]")
        suggestion_parts.append(f"Phase tactic: {tactic}")

    # Check deal rules — match against available_actions text, not just actual
    deal_rules = strategy_dict.get("deal_rules", {})
    available_lower = scenario.available_actions.lower()
    for deal_id, rule in deal_rules.items():
        if deal_id.lower() in available_lower:
            matches.append(f"deal_rule[{deal_id}]")
            suggestion_parts.append(f"Deal rule ({deal_id}): {rule}")

    # Check role overrides
    role_overrides = strategy_dict.get("role_overrides", {})
    for role_id, override in role_overrides.items():
        if role_id.lower() in scenario.description.lower():
            matches.append(f"role_override[{role_id}]")
            suggestion_parts.append(f"Role override: {override}")
            break

    # Check persona keywords against available actions
    persona = strategy_dict.get("persona", "")
    personality = strategy_dict.get("personality", {})

    # Personality-based heuristics
    aggression = personality.get("aggression", 0.5)
    honesty = personality.get("honesty", 0.5)
    risk_tolerance = personality.get("risk_tolerance", 0.5)

    if aggression >= 0.7:
        suggestion_parts.append("Personality suggests aggressive action")
    elif aggression <= 0.3:
        suggestion_parts.append("Personality suggests cautious approach")

    if "accept" in available_lower and category == "respond":
        if risk_tolerance >= 0.6:
            suggestion_parts.append("High risk tolerance → likely accept")
        elif risk_tolerance <= 0.3:
            suggestion_parts.append("Low risk tolerance → likely reject")

    # Priority-based hints
    priorities = strategy_dict.get("priorities", ())
    if priorities:
        top = priorities[0] if isinstance(priorities, (list, tuple)) else ""
        if top:
            suggestion_parts.append(f"Top priority: {top}")

    # Determine confidence
    if len(matches) >= 2:
        confidence = "high"
    elif len(matches) == 1:
        confidence = "medium"
    else:
        confidence = "low"

    suggestion = (
        " | ".join(suggestion_parts)
        if suggestion_parts
        else "No specific guidance found"
    )

    return DeterministicResult(
        scenario_id=scenario.id,
        matches=tuple(matches),
        confidence=confidence,
        suggestion=suggestion,
        actual=f"{category}: {actual_action}",
    )


# ---------------------------------------------------------------------------
# LLM-based testing (cheap, ~2 sec per scenario)
# ---------------------------------------------------------------------------


@attrs.frozen
class LLMTestResult:
    """Result of LLM-based strategy evaluation on a scenario."""

    scenario_id: str
    chosen_action: str
    reasoning: str
    matches_actual: bool  # did the LLM choose the same as the original player
    error: str = ""


async def evaluate_with_llm(
    scenario: Scenario,
    system_prompt: str,
    provider: Any,  # LLMProvider
) -> LLMTestResult:
    """Test a compiled strategy prompt against a scenario using an LLM.

    Sends the scenario description + available actions + compiled strategy
    to the LLM and asks what it would do. Single call, ~100-200 tokens output.
    """
    user_message = (
        f"You are playing as {scenario.player_id} in a game.\n\n"
        f"Current situation:\n{scenario.description}\n\n"
        f"Available actions:\n{scenario.available_actions}\n\n"
        f"What action do you take? Respond with:\n"
        f"ACTION: <action name>\n"
        f"REASON: <brief reason (1-2 sentences)>"
    )

    try:
        response = await provider.complete(
            messages=[{"role": "user", "content": user_message}],
            system=system_prompt,
        )

        text = response.content
        chosen = ""
        reasoning = ""

        for line in text.split("\n"):
            line = line.strip()
            if line.upper().startswith("ACTION:"):
                chosen = line[7:].strip()
            elif line.upper().startswith("REASON:"):
                reasoning = line[7:].strip()

        if not chosen:
            chosen = text[:100]

        actual_action = scenario.actual_decision.get(
            "deal",
            scenario.actual_decision.get(
                "vote_id", scenario.actual_decision.get("type", "")
            ),
        )
        matches = (
            actual_action.lower() in chosen.lower()
            or chosen.lower() in actual_action.lower()
        )

        return LLMTestResult(
            scenario_id=scenario.id,
            chosen_action=chosen,
            reasoning=reasoning or text[:200],
            matches_actual=matches,
        )

    except Exception as exc:
        return LLMTestResult(
            scenario_id=scenario.id,
            chosen_action="",
            reasoning="",
            matches_actual=False,
            error=str(exc),
        )
