"""Strategy list page — browse, create, fork, and manage strategies."""

from __future__ import annotations

import uuid
from typing import Any

from nicegui import app, ui

from server.components.layout import page_layout
from server.components.ui_kit import empty_state, glass_card, section_header
from server.theme import GAME_COLORS, GAME_ICONS
from strategy.archetypes import ARCHETYPES, get_archetypes
from strategy.schema import Strategy
from strategy.stats import strategy_stats
from strategy.store import StrategyStore

_store = StrategyStore()

# Game display info (injected at register time)
_games: dict[str, dict[str, Any]] = {}


def _get_user_id() -> str:
    """Get or create a persistent user ID from browser storage."""
    storage = app.storage.browser
    uid = storage.get("user_id")
    if not uid:
        uid = uuid.uuid4().hex[:12]
        storage["user_id"] = uid
    return uid


def register(games: dict[str, dict[str, Any]]) -> None:
    """Register the /strategies page."""
    _games.update(games)

    @ui.page("/strategies")
    def strategies_page():
        user_id = _get_user_id()

        with page_layout("Agent Workshop"):
            # Create button
            with ui.row().classes("w-full justify-end"):
                ui.button(
                    "New Strategy",
                    on_click=lambda: _create_new(user_id),
                    icon="add",
                ).props("unelevated no-caps color=primary").classes("rounded-lg")

            with ui.tabs().classes("w-full").props("no-caps dense") as tabs:
                ui.tab("My Strategies", icon="person")
                ui.tab("Archetypes", icon="auto_awesome")
                ui.tab("Community", icon="public")

            with ui.tab_panels(tabs, value="My Strategies").classes("w-full"):
                with ui.tab_panel("My Strategies"):
                    _render_my_strategies(user_id)

                with ui.tab_panel("Archetypes"):
                    _render_archetypes(user_id)

                with ui.tab_panel("Community"):
                    _render_community(user_id)


def _create_new(user_id: str) -> None:
    """Create a new blank strategy and navigate to editor."""
    s = Strategy(author=user_id)
    _store.save(s)
    ui.navigate.to(f"/workshop/{s.id}")


def _render_my_strategies(user_id: str) -> None:
    """Render user's strategies as cards."""
    strategies = _store.list_by_author(user_id)

    if not strategies:
        empty_state(
            "No strategies yet",
            icon="lightbulb",
            sub="Pick an Archetype to get started, or create a blank strategy.",
        )
        return

    with ui.row().classes("w-full gap-4 flex-wrap"):
        for s in strategies:
            _render_strategy_card(s, user_id, show_edit=True)


def _render_archetypes(user_id: str) -> None:
    """Render pre-built archetype templates grouped by game."""
    for game_id, info in _games.items():
        templates = get_archetypes(game_id)
        if not templates:
            continue

        icon = GAME_ICONS.get(game_id, "casino")
        color = GAME_COLORS.get(game_id, "#6D28D9")

        with ui.column().classes("w-full gap-3"):
            with ui.row().classes("items-center gap-2"):
                ui.icon(icon).classes("text-xl").style(f"color: {color}")
                ui.label(info.get("name", game_id)).classes(
                    "font-display text-lg font-semibold"
                )

            with ui.row().classes("w-full gap-4 flex-wrap"):
                for t in templates:
                    _render_archetype_card(t, user_id)

            ui.separator()


def _render_community(user_id: str) -> None:
    """Render public community strategies with leaderboard stats."""
    public = _store.list_public()

    if not public:
        empty_state("No public strategies yet", icon="public")
        return

    all_stats = strategy_stats()

    def _sort_key(s: Strategy):
        st = all_stats.get(s.id, {})
        games = st.get("games", 0)
        wr = st.get("win_rate", 0.0) if games > 0 else -1
        return (-wr, -games)

    public.sort(key=_sort_key)

    with ui.row().classes("w-full gap-4 flex-wrap"):
        for s in public:
            stats = all_stats.get(s.id)
            _render_strategy_card(s, user_id, show_fork=True, stats=stats)


# ---------------------------------------------------------------------------
# Card components
# ---------------------------------------------------------------------------


def _render_strategy_card(
    s: Strategy,
    user_id: str,
    show_edit: bool = False,
    show_fork: bool = False,
    stats: dict | None = None,
) -> None:
    """Render a single strategy card with optional win/loss stats."""
    game_info = _games.get(s.game_id, {})
    icon = GAME_ICONS.get(s.game_id, "casino")
    color = GAME_COLORS.get(s.game_id, "#6D28D9")

    with glass_card(extra_classes="w-full sm:w-72"):
        with ui.row().classes("items-center gap-2"):
            ui.icon(icon).classes("text-lg").style(f"color: {color}")
            ui.label(s.name).classes(
                "font-display font-semibold truncate tracking-tight"
            )

        game_name = game_info.get("name", s.game_id)
        ui.label(f"{game_name} \u00b7 v{s.version}").classes("text-xs text-gray-400")

        if s.archetype:
            ui.badge(s.archetype.replace("_", " ").title()).props(
                "outline rounded"
            ).classes("text-xs px-2")

        if s.persona:
            ui.label(s.persona[:80] + ("..." if len(s.persona) > 80 else "")).classes(
                "text-xs text-gray-500 mt-1"
            )

        # Win/loss stats
        if stats and stats.get("games", 0) > 0:
            wins = stats["wins"]
            losses = stats["losses"]
            wr = stats["win_rate"]
            with ui.row().classes("items-center gap-2 mt-1"):
                ui.label(f"W:{wins} L:{losses}").classes(
                    "text-xs font-mono text-gray-500"
                )
                wr_color = "positive" if wr >= 0.5 else "negative"
                ui.badge(f"{wr:.0%}").props(f"color={wr_color} dense rounded").classes(
                    "text-xs px-2"
                )

        with ui.row().classes("w-full gap-2 mt-3"):
            if show_edit:
                ui.button(
                    "Edit",
                    on_click=lambda sid=s.id: ui.navigate.to(f"/workshop/{sid}"),
                    icon="edit",
                ).props("flat no-caps dense").classes("flex-grow")
            if show_fork:

                async def _fork(sid: str = s.id) -> None:
                    forked = _store.fork(sid, user_id)
                    if forked:
                        ui.notify(f"Forked: {forked.name}", type="positive")
                        ui.navigate.to(f"/workshop/{forked.id}")
                    else:
                        ui.notify("Failed to fork strategy", type="negative")

                ui.button("Fork", on_click=_fork, icon="fork_right").props(
                    "flat no-caps dense"
                ).classes("flex-grow")


def _render_archetype_card(t: Strategy, user_id: str) -> None:
    """Render an archetype template card with 'Use' button."""
    with glass_card(extra_classes="w-full sm:w-72"):
        ui.label(t.name).classes("font-display text-base font-semibold tracking-tight")
        if t.persona:
            ui.label(t.persona[:100] + ("..." if len(t.persona) > 100 else "")).classes(
                "text-xs text-gray-500 mt-1"
            )

        traits: list[str] = []
        for axis, val in t.personality.items():
            if val >= 0.7:
                traits.append(f"High {axis.replace('_', ' ')}")
            elif val <= 0.3:
                traits.append(f"Low {axis.replace('_', ' ')}")
        if traits:
            ui.label(" \u00b7 ".join(traits)).classes("text-xs text-gray-400 mt-1")

        async def _use_archetype(template: Strategy = t) -> None:
            new = Strategy(
                name=template.name,
                game_id=template.game_id,
                author=user_id,
                archetype=template.archetype,
                personality=dict(template.personality),
                priorities=template.priorities,
                persona=template.persona,
                phase_tactics=dict(template.phase_tactics),
                role_overrides=dict(template.role_overrides),
                deal_rules=dict(template.deal_rules),
                channel_rules=dict(template.channel_rules),
                forked_from=template.id,
            )
            _store.save(new)
            ui.navigate.to(f"/workshop/{new.id}")

        ui.button(
            "Use as Starting Point",
            on_click=_use_archetype,
            icon="auto_awesome",
        ).props("unelevated no-caps dense color=primary").classes(
            "w-full mt-3 rounded-lg"
        )
