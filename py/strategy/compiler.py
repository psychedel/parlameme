"""Prompt compiler — convert Strategy document to LLM system prompt.

Produces an XML-sectioned system prompt following Anthropic's context
engineering best practices.  Every section has a user-defined override
OR a fallback generated from the game's existing ContextConfig / hints.

Reuses:
  - engine/runtime/state.py: ContextConfig, PhaseHint, RoleHint, ChannelHint
  - mcp/mechanics.py: outcome_summary(), describe_deal_mechanics()
"""

from __future__ import annotations

from typing import Any

from engine.runtime.state import CompiledGame
from mcp.mechanics import outcome_summary
from strategy.schema import PERSONALITY_AXES, Strategy


def compile_strategy(strategy: Strategy, compiled: CompiledGame) -> str:
    """Compile a Strategy document into an XML-sectioned system prompt.

    Returns a string suitable as a system message for Claude / GPT / etc.
    Typically ~1000-1500 tokens.
    """
    sections: list[str] = []

    sections.append(_section_identity(strategy, compiled))
    sections.append(_section_priorities(strategy))
    sections.append(_section_phase_tactics(strategy, compiled))
    sections.append(_section_role_guidance(strategy, compiled))
    sections.append(_section_deal_rules(strategy, compiled))
    sections.append(_section_channel_strategy(strategy, compiled))
    sections.append(_section_instructions())

    return "\n\n".join(s for s in sections if s)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _section_identity(strategy: Strategy, compiled: CompiledGame) -> str:
    ctx = compiled.context
    parts = [f"<identity>"]
    parts.append(f"You are an AI player in {compiled.name}.")
    if ctx.game_summary:
        parts.append(ctx.game_summary)
    if strategy.persona:
        parts.append(strategy.persona)
    elif strategy.archetype:
        parts.append(f"Play style: {strategy.archetype.replace('_', ' ')}.")
    if ctx.score_explanation:
        parts.append(f"Scoring: {ctx.score_explanation}")
    parts.append("</identity>")
    return "\n".join(parts)


def _section_priorities(strategy: Strategy) -> str:
    lines = ["<priorities>"]

    if strategy.priorities:
        ranked = [
            f"{i}. {p.replace('_', ' ').title()}"
            for i, p in enumerate(strategy.priorities, 1)
        ]
        lines.append("Your priorities in order: " + ", ".join(ranked))

    # Personality axes as behavioral guidance
    personality = strategy.personality
    if personality:
        traits: list[str] = []
        for axis in PERSONALITY_AXES:
            val = personality.get(axis, 0.5)
            label = axis.replace("_", " ")
            if val <= 0.2:
                traits.append(f"very low {label}")
            elif val <= 0.4:
                traits.append(f"low {label}")
            elif val >= 0.8:
                traits.append(f"very high {label}")
            elif val >= 0.6:
                traits.append(f"high {label}")
            # Skip 0.4-0.6 (neutral — no mention needed)
        if traits:
            lines.append("Personality: " + ", ".join(traits) + ".")

    lines.append("</priorities>")
    return "\n".join(lines)


def _section_phase_tactics(strategy: Strategy, compiled: CompiledGame) -> str:
    ctx = compiled.context
    lines = ["<phase_tactics>"]

    for phase_def in compiled.phases:
        pid = phase_def.id
        if phase_def.automatic:
            continue  # Agent can't act in automatic phases

        # User override > PhaseHint fallback
        user_tactic = strategy.phase_tactics.get(pid, "")
        if user_tactic:
            lines.append(f"{pid}: {user_tactic}")
        else:
            hint = ctx.phase_hints.get(pid)
            if hint:
                parts = [hint.summary] if hint.summary else []
                for tip in hint.tips:
                    parts.append(tip)
                if parts:
                    text = ". ".join(parts)
                    prefix = "[CRITICAL] " if hint.urgency == "critical" else ""
                    lines.append(f"{prefix}{pid}: {text}")

    lines.append("</phase_tactics>")
    # If only tags and nothing between, skip entirely
    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def _section_role_guidance(strategy: Strategy, compiled: CompiledGame) -> str:
    if not compiled.roles:
        return ""  # Auction has no roles

    ctx = compiled.context
    lines = ["<role_guidance>"]

    for role_id in compiled.roles:
        user_override = strategy.role_overrides.get(role_id, "")
        if user_override:
            lines.append(f"If your role is {role_id}: {user_override}")
        else:
            hint = ctx.role_hints.get(role_id)
            if hint and hint.strategy:
                parts = [hint.strategy]
                if hint.allies:
                    parts.append(f"Allies: {', '.join(hint.allies)}")
                if hint.threats:
                    parts.append(f"Threats: {', '.join(hint.threats)}")
                if hint.key_actions:
                    parts.append(f"Key actions: {', '.join(hint.key_actions)}")
                if hint.phase_tips:
                    tips = "; ".join(
                        f"{ph}: {tip}" for ph, tip in hint.phase_tips.items()
                    )
                    parts.append(f"Phase tips — {tips}")
                lines.append(f"If your role is {role_id}: {'. '.join(parts)}")

    lines.append("</role_guidance>")
    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def _section_deal_rules(strategy: Strategy, compiled: CompiledGame) -> str:
    ctx = compiled.context
    lines = ["<deal_rules>"]

    # Sort deals by priority (most important first)
    deal_items = sorted(
        compiled.deals.items(),
        key=lambda kv: ctx.deal_priorities.get(kv[0], 50),
        reverse=True,
    )
    for deal_id, deal_def in deal_items:
        user_rule = strategy.deal_rules.get(deal_id, "")
        if user_rule:
            lines.append(f"{deal_id}: {user_rule}")
        else:
            # Auto-generate a brief description from outcomes
            doc = deal_def.doc or ""
            outcomes = outcome_summary(deal_def.outcomes) if deal_def.outcomes else ""
            if doc or outcomes:
                desc = doc
                if outcomes:
                    desc += f" Outcomes: {outcomes}" if desc else outcomes
                lines.append(f"{deal_id}: {desc}")

    lines.append("</deal_rules>")
    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def _section_channel_strategy(strategy: Strategy, compiled: CompiledGame) -> str:
    if not compiled.channels:
        return ""

    ctx = compiled.context
    lines = ["<channel_strategy>"]

    for ch_id in compiled.channels:
        user_rule = strategy.channel_rules.get(ch_id, "")
        if user_rule:
            lines.append(f"{ch_id}: {user_rule}")
        else:
            hint = ctx.channel_hints.get(ch_id)
            if hint:
                parts = []
                if hint.when_to_use:
                    parts.append(hint.when_to_use)
                if hint.risk:
                    parts.append(f"Risk: {hint.risk}")
                if parts:
                    lines.append(f"{ch_id}: {'. '.join(parts)}")

    lines.append("</channel_strategy>")
    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def _section_instructions() -> str:
    return (
        "<instructions>\n"
        "Use the `act` tool to observe game state and take actions. "
        "Use `wait_for_turn` between actions to wait for other players. "
        "Think step by step about your strategy before each action. "
        "Be concise in your reasoning.\n"
        "</instructions>"
    )


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Rough token count estimate (1 token ~ 4 chars for English)."""
    return len(text) // 4
