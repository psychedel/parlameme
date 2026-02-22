"""Workshop editor page — build and edit AI agent strategies.

Split-panel layout: left = editor (tabbed sections), right = live prompt preview.
Updates preview on every edit via debounced compile_strategy().
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from nicegui import app, ui

from engine.runtime.state import CompiledGame
from games import REGISTRY as GAME_REGISTRY
from server.components.ui_kit import glass_card
from server.theme import apply_theme
from strategy.archetypes import get_archetypes
from strategy.compiler import compile_strategy, estimate_tokens
from strategy.scenarios import (
    DeterministicResult,
    evaluate_deterministic,
    extract_scenarios,
)
from strategy.schema import PERSONALITY_AXES, PRIORITY_OPTIONS, Strategy
from strategy.store import StrategyStore

log = logging.getLogger(__name__)

_store = StrategyStore()
_games: dict[str, dict[str, Any]] = {}


def _get_user_id() -> str:
    storage = app.storage.browser
    uid = storage.get("user_id")
    if not uid:
        import uuid

        uid = uuid.uuid4().hex[:12]
        storage["user_id"] = uid
    return uid


def register(games: dict[str, dict[str, Any]]) -> None:
    """Register workshop pages."""
    _games.update(games)

    @ui.page("/workshop/{strategy_id}")
    def workshop_page(strategy_id: str):
        apply_theme()
        user_id = _get_user_id()

        strategy = _store.load(strategy_id)
        if not strategy:
            ui.label("Strategy not found").classes("text-xl text-red-600")
            ui.button(
                "Back",
                on_click=lambda: ui.navigate.to("/strategies"),
            ).props("unelevated no-caps").classes("rounded-lg")
            return

        state = _strategy_to_state(strategy)

        # --- Header ---
        with ui.header().classes("items-center justify-between px-4"):
            with ui.row().classes("items-center gap-2"):
                ui.button(
                    icon="arrow_back",
                    on_click=lambda: ui.navigate.to("/strategies"),
                ).props("flat round dense color=dark")
                header_label = ui.label(state["name"]).classes(
                    "font-display text-lg font-semibold tracking-tight"
                )
            ui.button(
                "Save", on_click=lambda: _save(state, strategy_id), icon="save"
            ).props("unelevated no-caps color=positive").classes("rounded-lg")

        # --- Main split layout ---
        with (
            ui.splitter(value=55)
            .classes("w-full max-w-7xl mx-auto")
            .style("height: calc(100vh - 120px)") as splitter
        ):
            with splitter.before:
                _render_editor(state, strategy_id, header_label)

            with splitter.after:
                _render_preview(state)

        # --- Footer actions ---
        with ui.row().classes("w-full max-w-7xl mx-auto px-4 pb-4 gap-3 justify-end"):
            ui.button(
                "Test Scenarios",
                on_click=lambda: _show_scenario_dialog(state),
                icon="science",
            ).props("unelevated no-caps color=accent").classes("rounded-lg")
            ui.button(
                "Play in New Game",
                on_click=lambda: ui.navigate.to(f"/workshop/play/{strategy_id}"),
                icon="play_arrow",
            ).props("unelevated no-caps color=primary").classes("rounded-lg")
            ui.button(
                "Save",
                on_click=lambda: _save(state, strategy_id),
                icon="save",
            ).props("unelevated no-caps color=positive").classes("rounded-lg")


# ---------------------------------------------------------------------------
# Editor panel (left side)
# ---------------------------------------------------------------------------


def _render_editor(
    state: dict[str, Any], strategy_id: str, header_label: ui.label
) -> None:
    """Render the tabbed editor panel."""
    with ui.column().classes("w-full h-full p-3 gap-2"):
        with ui.tabs().classes("w-full").props("no-caps dense") as tabs:
            ui.tab("Basics", icon="tune")
            ui.tab("Phases", icon="timeline")
            ui.tab("Roles", icon="groups")
            ui.tab("Deals", icon="handshake")
            ui.tab("Advanced", icon="settings")

        with ui.tab_panels(tabs, value="Basics").classes("w-full flex-grow"):
            with ui.tab_panel("Basics"):
                _render_basics_tab(state, header_label)

            with ui.tab_panel("Phases"):
                _render_phases_tab(state)

            with ui.tab_panel("Roles"):
                _render_roles_tab(state)

            with ui.tab_panel("Deals"):
                _render_deals_tab(state)

            with ui.tab_panel("Advanced"):
                _render_advanced_tab(state)


def _render_basics_tab(state: dict[str, Any], header_label: ui.label) -> None:
    """Basics: name, game, archetype, personality sliders, priorities."""
    with ui.scroll_area().classes("w-full"):
        with ui.column().classes("w-full gap-4 p-2"):
            name_input = (
                ui.input("Strategy Name", value=state["name"])
                .props("outlined dense")
                .classes("w-full")
            )
            name_input.on(
                "update:model-value",
                lambda e: (
                    _update(state, "name", e.args) or header_label.set_text(e.args)
                ),
            )

            game_options = {gid: info["name"] for gid, info in _games.items()}
            game_select = (
                ui.select(
                    game_options,
                    value=state["game_id"] or None,
                    label="Game",
                )
                .props("outlined dense")
                .classes("w-full")
            )

            def _on_game_change(e):
                old_game = state.get("game_id", "")
                new_game = e.args
                if new_game != old_game:
                    state["game_id"] = new_game
                    state["phase_tactics"] = {}
                    state["role_overrides"] = {}
                    state["deal_rules"] = {}
                    state["channel_rules"] = {}
                    state["archetype"] = ""

            game_select.on("update:model-value", _on_game_change)

            _render_archetype_selector(state)

            ui.separator()
            ui.label("Personality").classes("font-display text-base font-semibold")

            # Personality sliders with colored tracks
            _SLIDER_COLORS = {
                "aggression": "#DC2626",
                "cooperation": "#16A34A",
                "risk_tolerance": "#D97706",
                "deception": "#7C3AED",
                "loyalty": "#0891B2",
                "adaptability": "#2563EB",
            }

            for axis in PERSONALITY_AXES:
                val = state["personality"].get(axis, 0.5)
                label = axis.replace("_", " ").title()
                color = _SLIDER_COLORS.get(axis, "#6D28D9")
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(label).classes("w-28 text-sm text-gray-500")
                    slider = (
                        ui.slider(min=0, max=1, step=0.1, value=val)
                        .props(f'color="{color}"')
                        .classes("flex-grow")
                    )
                    val_label = ui.label(f"{val:.1f}").classes(
                        "w-10 text-sm text-right font-mono text-gray-500"
                    )

                    def _on_slider(e, ax=axis, vl=val_label):
                        state["personality"][ax] = e.args
                        vl.set_text(f"{e.args:.1f}")

                    slider.on("update:model-value", _on_slider)

            ui.separator()
            ui.label("Priorities").classes("font-display text-base font-semibold")

            _render_priorities(state)

            ui.separator()
            ui.label("Persona").classes("font-display text-base font-semibold")
            ui.label("Free-text personality description for your agent").classes(
                "text-xs text-gray-400"
            )
            persona_area = (
                ui.textarea(
                    value=state.get("persona", ""),
                    placeholder="Describe your agent's personality and play style...",
                )
                .props("outlined autogrow rows=3")
                .classes("w-full")
            )
            persona_area.on(
                "update:model-value",
                lambda e: _update(state, "persona", e.args),
            )


def _render_archetype_selector(state: dict[str, Any]) -> None:
    """Archetype dropdown — selecting one pre-fills fields."""
    game_id = state.get("game_id", "")
    if not game_id:
        ui.label("Select a game first to see archetypes").classes(
            "text-xs text-gray-400"
        )
        return

    templates = get_archetypes(game_id)
    if not templates:
        return

    options = {"": "-- No archetype --"}
    for t in templates:
        options[t.archetype] = t.name

    arch_select = (
        ui.select(
            options,
            value=state.get("archetype", ""),
            label="Start from Archetype",
        )
        .props("outlined dense")
        .classes("w-full")
    )

    def _on_archetype(e):
        archetype_id = e.args
        state["archetype"] = archetype_id
        if not archetype_id:
            return
        for t in templates:
            if t.archetype == archetype_id:
                state["persona"] = t.persona
                state["personality"] = dict(t.personality)
                state["priorities"] = list(t.priorities)
                state["phase_tactics"] = dict(t.phase_tactics)
                state["role_overrides"] = dict(t.role_overrides)
                state["deal_rules"] = dict(t.deal_rules)
                state["channel_rules"] = dict(t.channel_rules)
                ui.notify(f"Loaded archetype: {t.name}", type="info")
                ui.navigate.to(ui.context.client.page.path)
                break

    arch_select.on("update:model-value", _on_archetype)


def _render_priorities(state: dict[str, Any]) -> None:
    """Priority list with up/down reorder buttons."""
    priorities = state.get("priorities", list(PRIORITY_OPTIONS[:4]))
    if not isinstance(priorities, list):
        priorities = list(priorities)
        state["priorities"] = priorities

    @ui.refreshable
    def priority_list():
        for i, p in enumerate(priorities):
            with ui.row().classes("w-full items-center gap-1"):
                ui.label(f"{i + 1}.").classes("w-6 text-sm text-gray-400")
                ui.label(p.replace("_", " ").title()).classes("flex-grow text-sm")
                if i > 0:
                    ui.button(
                        icon="arrow_upward",
                        on_click=lambda idx=i: _move_priority(
                            priorities, idx, -1, priority_list
                        ),
                    ).props("dense flat round size=sm")
                else:
                    ui.space().classes("w-10")
                if i < len(priorities) - 1:
                    ui.button(
                        icon="arrow_downward",
                        on_click=lambda idx=i: _move_priority(
                            priorities, idx, 1, priority_list
                        ),
                    ).props("dense flat round size=sm")
                else:
                    ui.space().classes("w-10")

    priority_list()

    available = [p for p in PRIORITY_OPTIONS if p not in priorities]
    if available:
        options = {p: p.replace("_", " ").title() for p in available}
        with ui.row().classes("w-full items-center gap-2 mt-2"):
            add_select = (
                ui.select(options, label="Add priority")
                .props("outlined dense")
                .classes("flex-grow")
            )

            def _add(select=add_select):
                if select.value and select.value not in priorities:
                    priorities.append(select.value)
                    select.value = None
                    priority_list.refresh()

            ui.button(icon="add", on_click=_add).props("dense flat round")


def _move_priority(priorities: list, idx: int, direction: int, refreshable) -> None:
    """Swap priority at idx with neighbor in direction."""
    new_idx = idx + direction
    if 0 <= new_idx < len(priorities):
        priorities[idx], priorities[new_idx] = priorities[new_idx], priorities[idx]
        refreshable.refresh()


def _render_phases_tab(state: dict[str, Any]) -> None:
    """Per-phase tactic text areas with PhaseHint info."""
    game_id = state.get("game_id", "")
    compiled = GAME_REGISTRY.get(game_id)
    if not compiled:
        ui.label("Select a game in Basics tab first").classes("text-sm text-gray-400")
        return

    tactics = state.setdefault("phase_tactics", {})

    with ui.scroll_area().classes("w-full"):
        with ui.column().classes("w-full gap-3 p-2"):
            for phase_def in compiled.phases:
                if phase_def.automatic and phase_def.id == "setup":
                    continue

                hint = compiled.context.phase_hints.get(phase_def.id)
                with glass_card(extra_classes="w-full"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(phase_def.id.replace("_", " ").title()).classes(
                            "font-display font-semibold text-sm"
                        )
                        if phase_def.automatic:
                            ui.badge("auto").props("outline dense rounded").classes(
                                "text-xs px-1"
                            )

                    if hint:
                        if hint.summary:
                            ui.label(hint.summary).classes("text-xs text-gray-400")
                        if hint.tips:
                            for tip in hint.tips:
                                ui.label(f"Tip: {tip}").classes(
                                    "text-xs text-gray-400 italic"
                                )

                    if phase_def.allows:
                        actions = ", ".join(phase_def.allows)
                        ui.label(f"Actions: {actions}").classes("text-xs text-gray-300")

                    area = (
                        ui.textarea(
                            value=tactics.get(phase_def.id, ""),
                            placeholder=hint.summary
                            if hint and hint.summary
                            else f"Your tactic for {phase_def.id}...",
                        )
                        .props("outlined autogrow rows=2")
                        .classes("w-full mt-1")
                    )
                    area.on(
                        "update:model-value",
                        lambda e, pid=phase_def.id: _update_dict(tactics, pid, e.args),
                    )


def _render_roles_tab(state: dict[str, Any]) -> None:
    """Per-role override text areas with RoleHint info."""
    game_id = state.get("game_id", "")
    compiled = GAME_REGISTRY.get(game_id)
    if not compiled:
        ui.label("Select a game in Basics tab first").classes("text-sm text-gray-400")
        return

    if not compiled.roles:
        ui.label("This game has no roles").classes("text-sm text-gray-400")
        return

    overrides = state.setdefault("role_overrides", {})

    with ui.scroll_area().classes("w-full"):
        with ui.column().classes("w-full gap-3 p-2"):
            for role_id, role_def in compiled.roles.items():
                hint = compiled.context.role_hints.get(role_id)
                with glass_card(extra_classes="w-full"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(role_id.replace("_", " ").title()).classes(
                            "font-display font-semibold text-sm"
                        )
                        if hasattr(role_def, "team") and role_def.team:
                            ui.badge(role_def.team).props(
                                "outline dense rounded"
                            ).classes("text-xs px-1")

                    if hasattr(role_def, "doc") and role_def.doc:
                        ui.label(role_def.doc[:120]).classes("text-xs text-gray-400")

                    if hint:
                        if hint.strategy:
                            ui.label(
                                f"Default strategy: {hint.strategy[:100]}"
                            ).classes("text-xs text-gray-400 italic")
                        if hint.allies:
                            ui.label(f"Allies: {', '.join(hint.allies)}").classes(
                                "text-xs text-gray-300"
                            )
                        if hint.threats:
                            ui.label(f"Threats: {', '.join(hint.threats)}").classes(
                                "text-xs text-gray-300"
                            )

                    area = (
                        ui.textarea(
                            value=overrides.get(role_id, ""),
                            placeholder=hint.strategy[:80]
                            if hint and hint.strategy
                            else f"Your override for {role_id}...",
                        )
                        .props("outlined autogrow rows=2")
                        .classes("w-full mt-1")
                    )
                    area.on(
                        "update:model-value",
                        lambda e, rid=role_id: _update_dict(overrides, rid, e.args),
                    )


def _render_deals_tab(state: dict[str, Any]) -> None:
    """Per-deal rule text areas with outcome info."""
    game_id = state.get("game_id", "")
    compiled = GAME_REGISTRY.get(game_id)
    if not compiled:
        ui.label("Select a game in Basics tab first").classes("text-sm text-gray-400")
        return

    if not compiled.deals:
        ui.label("This game has no deals").classes("text-sm text-gray-400")
        return

    rules = state.setdefault("deal_rules", {})

    with ui.scroll_area().classes("w-full"):
        with ui.column().classes("w-full gap-3 p-2"):
            for deal_id, deal_def in compiled.deals.items():
                with glass_card(extra_classes="w-full"):
                    ui.label(deal_id.replace("_", " ").title()).classes(
                        "font-display font-semibold text-sm"
                    )

                    if deal_def.doc:
                        ui.label(deal_def.doc[:150]).classes("text-xs text-gray-400")

                    if deal_def.outcomes:
                        from mcp.mechanics import outcome_summary

                        summary = outcome_summary(deal_def.outcomes)
                        if summary:
                            with ui.expansion("Outcomes", icon="info").classes(
                                "w-full text-xs"
                            ):
                                ui.label(summary).classes("text-xs text-gray-500")

                    limits = []
                    if deal_def.per_round:
                        limits.append(f"{deal_def.per_round}/round")
                    if deal_def.per_phase:
                        limits.append(f"{deal_def.per_phase}/phase")
                    if deal_def.per_game:
                        limits.append(f"{deal_def.per_game}/game")
                    if limits:
                        ui.label(f"Limits: {', '.join(limits)}").classes(
                            "text-xs text-gray-300"
                        )

                    area = (
                        ui.textarea(
                            value=rules.get(deal_id, ""),
                            placeholder=f"Your rule for {deal_id}...",
                        )
                        .props("outlined autogrow rows=2")
                        .classes("w-full mt-1")
                    )
                    area.on(
                        "update:model-value",
                        lambda e, did=deal_id: _update_dict(rules, did, e.args),
                    )


def _render_advanced_tab(state: dict[str, Any]) -> None:
    """Advanced: channel rules, tags, public toggle."""
    game_id = state.get("game_id", "")
    compiled = GAME_REGISTRY.get(game_id)

    with ui.scroll_area().classes("w-full"):
        with ui.column().classes("w-full gap-4 p-2"):
            if compiled and compiled.channels:
                ui.label("Channel Strategy").classes(
                    "font-display text-base font-semibold"
                )
                ch_rules = state.setdefault("channel_rules", {})

                for ch_id, ch_def in compiled.channels.items():
                    hint = compiled.context.channel_hints.get(ch_id)
                    with glass_card(extra_classes="w-full"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(ch_id.replace("_", " ").title()).classes(
                                "font-display font-semibold text-sm"
                            )
                            if hasattr(ch_def, "type"):
                                ui.badge(ch_def.type).props(
                                    "outline dense rounded"
                                ).classes("text-xs px-1")

                        if hint:
                            if hint.when_to_use:
                                ui.label(hint.when_to_use).classes(
                                    "text-xs text-gray-400"
                                )
                            if hint.risk:
                                ui.label(f"Risk: {hint.risk}").classes(
                                    "text-xs text-gray-400 italic"
                                )

                        area = (
                            ui.textarea(
                                value=ch_rules.get(ch_id, ""),
                                placeholder=hint.when_to_use[:60]
                                if hint and hint.when_to_use
                                else f"How to use {ch_id}...",
                            )
                            .props("outlined autogrow rows=2")
                            .classes("w-full mt-1")
                        )
                        area.on(
                            "update:model-value",
                            lambda e, cid=ch_id: _update_dict(ch_rules, cid, e.args),
                        )

                ui.separator()

            ui.label("Tags").classes("font-display text-base font-semibold")
            tags_input = (
                ui.input(
                    "Tags (comma-separated)",
                    value=", ".join(state.get("tags", [])),
                )
                .props("outlined dense")
                .classes("w-full")
            )
            tags_input.on(
                "update:model-value",
                lambda e: _update(
                    state,
                    "tags",
                    [t.strip() for t in (e.args or "").split(",") if t.strip()],
                ),
            )

            ui.separator()
            with ui.row().classes("items-center gap-4"):
                ui.label("Share publicly").classes("text-sm")
                public_switch = ui.switch(value=state.get("public", False))
                public_switch.on(
                    "update:model-value",
                    lambda e: _update(state, "public", e.args),
                )
                ui.label("Other players can see and fork your strategy").classes(
                    "text-xs text-gray-400"
                )


# ---------------------------------------------------------------------------
# Preview panel (right side)
# ---------------------------------------------------------------------------


def _render_preview(state: dict[str, Any]) -> None:
    """Live preview of compiled system prompt."""
    with ui.column().classes("w-full h-full p-3 gap-2"):
        ui.label("Compiled System Prompt").classes("font-display font-semibold text-sm")

        @ui.refreshable
        def preview_content():
            compiled = GAME_REGISTRY.get(state.get("game_id", ""))
            if not compiled:
                ui.label("Select a game to see preview").classes(
                    "text-sm text-gray-400"
                )
                return

            strategy = _state_to_strategy(state)
            prompt = compile_strategy(strategy, compiled)
            tokens = estimate_tokens(prompt)

            ui.label(f"~{tokens} tokens").classes("text-xs text-gray-400 font-mono")
            ui.code(prompt).classes("w-full text-xs").style(
                "max-height: calc(100vh - 220px); overflow-y: auto;"
            )

        preview_content()
        ui.timer(2.0, preview_content.refresh)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def _strategy_to_state(s: Strategy) -> dict[str, Any]:
    """Convert frozen Strategy to mutable dict for editing."""
    return {
        "id": s.id,
        "name": s.name,
        "game_id": s.game_id,
        "author": s.author,
        "archetype": s.archetype,
        "personality": dict(s.personality),
        "priorities": list(s.priorities),
        "persona": s.persona,
        "phase_tactics": dict(s.phase_tactics),
        "role_overrides": dict(s.role_overrides),
        "deal_rules": dict(s.deal_rules),
        "channel_rules": dict(s.channel_rules),
        "version": s.version,
        "forked_from": s.forked_from,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "tags": list(s.tags),
        "public": s.public,
    }


def _state_to_strategy(state: dict[str, Any]) -> Strategy:
    """Convert mutable state dict back to frozen Strategy."""
    return Strategy(
        id=state["id"],
        name=state.get("name", "Untitled"),
        game_id=state.get("game_id", ""),
        author=state.get("author", ""),
        archetype=state.get("archetype", ""),
        personality=dict(state.get("personality", {})),
        priorities=tuple(state.get("priorities", ())),
        persona=state.get("persona", ""),
        phase_tactics={k: v for k, v in state.get("phase_tactics", {}).items() if v},
        role_overrides={k: v for k, v in state.get("role_overrides", {}).items() if v},
        deal_rules={k: v for k, v in state.get("deal_rules", {}).items() if v},
        channel_rules={k: v for k, v in state.get("channel_rules", {}).items() if v},
        version=state.get("version", 1),
        forked_from=state.get("forked_from"),
        created_at=state.get("created_at", 0),
        updated_at=time.time(),
        tags=tuple(state.get("tags", ())),
        public=state.get("public", False),
    )


def _update(state: dict[str, Any], key: str, value: Any) -> None:
    """Update a state field."""
    state[key] = value


def _update_dict(target: dict, key: str, value: str) -> None:
    """Update a dict field, removing empty values."""
    if value:
        target[key] = value
    else:
        target.pop(key, None)


def _save(state: dict[str, Any], strategy_id: str) -> None:
    """Save current state to store."""
    if not state.get("game_id"):
        ui.notify("Please select a game first", type="negative")
        return
    if not state.get("name") or state["name"] == "Untitled Strategy":
        ui.notify("Please give your strategy a name", type="warning")
    strategy = _state_to_strategy(state)
    strategy = strategy.bump_version()
    state["version"] = strategy.version
    _store.save(strategy)
    ui.notify(f"Saved v{strategy.version}", type="positive")


def _show_scenario_dialog(state: dict[str, Any]) -> None:
    """Open a dialog showing scenario test results for the current strategy."""
    game_id = state.get("game_id", "")
    if not game_id:
        ui.notify("Please select a game first", type="negative")
        return

    with (
        ui.dialog().props("maximized") as dialog,
        ui.card().classes("w-full max-w-4xl mx-auto"),
    ):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Scenario Testing").classes("font-display text-xl font-semibold")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")

        ui.label(
            "Test your strategy against real game situations extracted from archives"
        ).classes("text-sm text-gray-400")

        ui.separator()

        @ui.refreshable
        def scenario_results():
            scenarios = extract_scenarios(game_id, limit=8)
            if not scenarios:
                with ui.column().classes("items-center gap-4 py-8"):
                    ui.icon("science").classes("text-4xl text-gray-300")
                    ui.label("No scenarios available").classes("text-lg text-gray-400")
                    ui.label(
                        "Play some games first to generate scenarios for testing."
                    ).classes("text-sm text-gray-300")
                return

            ui.label(f"Found {len(scenarios)} decision points").classes(
                "text-sm text-gray-500"
            )

            for scenario in scenarios:
                result = evaluate_deterministic(scenario, state)
                _render_scenario_result(scenario, result)

        scenario_results()

        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button(
                "Refresh", on_click=scenario_results.refresh, icon="refresh"
            ).props("flat no-caps")
            ui.button("Close", on_click=dialog.close).props("flat no-caps")

    dialog.open()


def _render_scenario_result(scenario: "Scenario", result: DeterministicResult) -> None:
    """Render a single scenario with its test result."""
    confidence_color = {
        "high": "rgba(22, 163, 74, 0.08)",
        "medium": "rgba(217, 119, 6, 0.08)",
        "low": "rgba(107, 114, 128, 0.06)",
    }.get(result.confidence, "rgba(107, 114, 128, 0.06)")

    with (
        ui.card()
        .classes("glass-static w-full")
        .style(f"background: {confidence_color} !important")
    ):
        with ui.row().classes("items-center gap-2"):
            ui.badge(scenario.phase).props("outline dense rounded").classes(
                "text-xs px-1"
            )
            ui.badge(f"R{scenario.round}").props("outline dense rounded").classes(
                "text-xs px-1"
            )
            ui.badge(scenario.category).props("dense rounded").classes("text-xs px-1")
            ui.space()
            confidence_icon = {
                "high": "check_circle",
                "medium": "help",
                "low": "remove_circle",
            }.get(result.confidence, "help")
            _conf_color = {
                "high": "text-green-600",
                "medium": "text-amber-600",
                "low": "text-gray-400",
            }.get(result.confidence, "text-gray-400")
            ui.icon(confidence_icon).classes(f"text-sm {_conf_color}")
            ui.label(f"{result.confidence} confidence").classes("text-xs text-gray-500")

        with ui.expansion("Situation", icon="visibility").classes("w-full text-xs"):
            ui.label(scenario.description).classes("text-xs font-mono text-gray-500")

        with ui.expansion("Available Actions", icon="list").classes("w-full text-xs"):
            ui.label(scenario.available_actions).classes(
                "text-xs font-mono text-gray-500"
            )

        if result.matches:
            ui.label("Matching rules:").classes(
                "text-xs font-semibold mt-2 text-gray-500"
            )
            for match in result.matches:
                ui.label(f"  {match}").classes("text-xs text-gray-400")

        ui.label(f"Strategy suggests: {result.suggestion}").classes(
            "text-xs text-gray-500 italic"
        )
        ui.label(f"Actual play: {result.actual}").classes("text-xs text-gray-400")
