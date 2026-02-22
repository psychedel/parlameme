"""Agent play page — watch an AI agent play a game in real-time.

Split layout:
  Left (55%): live game state (entity cards, phase badges, deltas)
  Right (45%): agent decision log (actions, reasoning, results)

Provider and API key are set in the UI — no env vars required.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from nicegui import app, ui

from agent.bots import BotRunner
from agent.bridge import InProcessBridge
from agent.providers import create_provider
from agent.runner import AgentRunner, TurnEntry
from engine.runtime.state import view_for
from games import REGISTRY as GAME_REGISTRY
from server.components.entity_card import render_entity_card
from server.components.ui_kit import empty_state, glass_card, status_chip
from server.theme import apply_theme
from strategy.store import StrategyStore

log = logging.getLogger(__name__)

_store = StrategyStore()
_games: dict[str, dict[str, Any]] = {}

# Running agents: strategy_id → agent_state dict
_running_agents: dict[str, dict[str, Any]] = {}


def register(games: dict[str, dict[str, Any]]) -> None:
    """Register agent play pages."""
    _games.update(games)

    @ui.page("/workshop/play/{strategy_id}")
    def agent_play_page(strategy_id: str):
        apply_theme()

        strategy = _store.load(strategy_id)
        if not strategy:
            with ui.column().classes("w-full max-w-3xl mx-auto px-4 py-12"):
                empty_state(
                    "Strategy not found",
                    icon="search_off",
                    sub="The strategy may have been deleted",
                )
                ui.button(
                    "Back to Workshop",
                    on_click=lambda: ui.navigate.to("/strategies"),
                    icon="arrow_back",
                ).props("flat no-caps")
            return

        if not strategy.game_id:
            with ui.column().classes("w-full max-w-3xl mx-auto px-4 py-12"):
                empty_state(
                    "No game selected",
                    icon="sports_esports",
                    sub="Edit the strategy and select a game first",
                )
                ui.button(
                    "Edit Strategy",
                    on_click=lambda: ui.navigate.to(f"/workshop/{strategy_id}"),
                    icon="edit",
                ).props("unelevated no-caps color=primary")
            return

        compiled = GAME_REGISTRY.get(strategy.game_id)
        if not compiled:
            with ui.column().classes("w-full max-w-3xl mx-auto px-4 py-12"):
                empty_state(
                    f"Unknown game: {strategy.game_id}",
                    icon="error_outline",
                )
            return

        game_info = _games.get(strategy.game_id, {})

        agent_state: dict[str, Any] = {
            "status": "idle",
            "turns": [],
            "task": None,
            "runner": None,
            "bot_runner": None,
            "session_id": None,
            "session": None,
            "game_listener": None,
        }

        # --- Header ---
        with ui.header().classes("items-center justify-between px-4"):
            with ui.row().classes("items-center gap-2"):
                ui.button(
                    icon="arrow_back",
                    on_click=lambda: ui.navigate.to(f"/workshop/{strategy_id}"),
                ).props("flat round dense color=dark")
                ui.icon("smart_toy").classes("text-lg text-gray-400")
                ui.label(strategy.name).classes(
                    "font-display text-lg font-semibold tracking-tight"
                )
                ui.label(f"/ {game_info.get('name', strategy.game_id)}").classes(
                    "text-sm text-gray-400"
                )
            with ui.row().classes("items-center gap-2"):
                ui.button(
                    "Edit Strategy",
                    on_click=lambda: ui.navigate.to(f"/workshop/{strategy_id}"),
                    icon="edit",
                ).props("flat no-caps dense color=dark")

        with ui.column().classes("w-full max-w-7xl mx-auto px-4 py-6 gap-4"):
            # --- Controls bar ---
            with glass_card(extra_classes="w-full"):
                with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                    # Provider selector
                    provider_select = (
                        ui.select(
                            {"anthropic": "Anthropic", "ollama": "Ollama (local)"},
                            value="anthropic",
                            label="Provider",
                        )
                        .props("outlined dense")
                        .classes("w-40")
                    )

                    # Model override (optional)
                    model_input = (
                        ui.input(
                            "Model",
                            placeholder="default",
                        )
                        .props("outlined dense")
                        .classes("w-44")
                    )

                    # API key input
                    api_key_input = (
                        ui.input(
                            "API Key",
                            placeholder="sk-...",
                            password=True,
                            password_toggle_button=True,
                        )
                        .props("outlined dense")
                        .classes("w-56")
                    )

                    ui.space()

                    # Status badge
                    @ui.refreshable
                    def status_badge():
                        status = agent_state["status"]
                        color_map = {
                            "idle": "grey-7",
                            "running": "positive",
                            "finished": "accent",
                            "error": "negative",
                            "stopped": "warning",
                        }
                        status_chip(status.upper(), color_map.get(status, "grey-7"))
                        turns = agent_state["turns"]
                        if turns:
                            ui.label(f"{len(turns)} turns").classes(
                                "text-xs text-gray-400"
                            )

                    status_badge()

                with ui.row().classes("gap-2 mt-2"):
                    ui.button(
                        "Start",
                        icon="play_arrow",
                        on_click=lambda: _start_agent(
                            strategy,
                            compiled,
                            agent_state,
                            provider_select.value,
                            model_input.value or None,
                            api_key_input.value,
                            game_state_view,
                            turn_log,
                            status_badge,
                            performance_view,
                        ),
                    ).props("unelevated no-caps color=positive").classes("rounded-lg")
                    ui.button(
                        "Stop",
                        icon="stop",
                        on_click=lambda: _stop_agent(agent_state, status_badge),
                    ).props("unelevated no-caps color=negative").classes("rounded-lg")
                    ui.button(
                        "Restart",
                        icon="replay",
                        on_click=lambda: _restart_agent(
                            strategy,
                            compiled,
                            agent_state,
                            provider_select.value,
                            model_input.value or None,
                            api_key_input.value,
                            game_state_view,
                            turn_log,
                            status_badge,
                            performance_view,
                        ),
                    ).props("flat no-caps color=primary").classes("rounded-lg")

            # --- Split view: Game State | Agent Log ---
            with (
                ui.splitter(value=55)
                .classes("w-full")
                .style("min-height: 60vh") as splitter
            ):
                with splitter.before:

                    @ui.refreshable
                    def game_state_view():
                        _render_game_state(agent_state, compiled)

                    game_state_view()

                with splitter.after:

                    @ui.refreshable
                    def turn_log():
                        _render_turn_log(agent_state["turns"])

                    turn_log()

            # --- Performance summary (shown when game ends) ---
            @ui.refreshable
            def performance_view():
                _render_performance(agent_state)

            performance_view()


# ---------------------------------------------------------------------------
# Game state panel (left side)
# ---------------------------------------------------------------------------

_prev_resources: dict[str, dict[str, dict]] = {}  # session_id → eid → resources


def _render_game_state(agent_state: dict, compiled):
    """Render live game state from the agent's session."""
    session = agent_state.get("session")
    status = agent_state["status"]

    if not session or status == "idle":
        with ui.column().classes("items-center gap-3 py-12 w-full"):
            ui.icon("sports_esports").classes("text-4xl text-gray-300")
            ui.label("Game state will appear here").classes("text-sm text-gray-400")
            ui.label("Press Start to begin").classes("text-xs text-gray-300")
        return

    s = session.state
    agent_player = agent_state.get("agent_player", "agent-0")
    view = view_for(s, agent_player, compiled)
    sid = agent_state.get("session_id", "")

    with ui.column().classes("w-full gap-3"):
        # Phase / round badges
        with ui.row().classes("items-center gap-2 flex-wrap"):
            ui.badge(f"Round {view['round']}").props("color=primary rounded").classes(
                "px-2"
            )
            ui.badge(f"Phase: {view['phase']}").props(
                "color=secondary rounded"
            ).classes("px-2")
            ui.badge(view["status"].upper()).props(
                f"color={'positive' if view['status'] == 'active' else 'negative'} rounded"
            ).classes("px-2")

        # Victory result
        if s.victory_result:
            winner = s.victory_result.get("winner", "?")
            cond = s.victory_result.get("condition", "?")
            with (
                ui.element("div")
                .classes("w-full rounded-lg p-3")
                .style("background: #F0FDF4; border: 1px solid #BBF7D0")
            ):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("emoji_events").classes("text-lg text-green-600")
                    ui.label(f"{winner} wins!").classes(
                        "font-display font-semibold text-green-700"
                    )
                    ui.label(f"({cond})").classes("text-sm text-green-600")

        # Entity cards
        prev = _prev_resources.get(sid, {})
        with ui.row().classes("w-full gap-3 flex-wrap"):
            for eid, entity in view["entities"].items():
                prev_res = prev.get(eid)
                render_entity_card(
                    eid,
                    entity,
                    is_self=(eid == agent_player),
                    compiled=compiled,
                    prev_resources=prev_res,
                )

        # Update prev_resources for next refresh
        new_prev: dict[str, dict] = {}
        for eid, entity in view["entities"].items():
            new_prev[eid] = dict(entity.get("resources", {}))
        _prev_resources[sid] = new_prev

        # Pending votes summary
        if view.get("pending_votes"):
            with (
                ui.element("div")
                .classes("w-full rounded-lg p-2")
                .style("background: #F5F3FF; border: 1px solid #DDD6FE")
            ):
                for pv in view["pending_votes"]:
                    voted = len(pv.get("votes", {}))
                    eligible = len(pv.get("eligible", []))
                    ui.label(
                        f"Vote: {pv.get('vote_type', '?')} [{voted}/{eligible}]"
                    ).classes("text-sm text-violet-700")

        # Messages (last few)
        messages = view.get("messages", [])
        if messages:
            with (
                ui.element("div")
                .classes("w-full rounded-lg p-2 mt-1")
                .style("background: #F8F8FA; border: 1px solid #E5E4EA")
            ):
                ui.label("Recent Messages").classes(
                    "text-xs text-gray-400 uppercase tracking-wider mb-1"
                )
                for msg in messages[-5:]:
                    ui.chat_message(
                        text=msg["content"],
                        name=msg["sender"],
                        sent=(msg["sender"] == agent_player),
                        stamp=f"R{msg['round']}",
                    )


# ---------------------------------------------------------------------------
# Turn log panel (right side)
# ---------------------------------------------------------------------------


def _render_turn_log(turns: list[TurnEntry]):
    """Render the agent decision log — reverse chronological."""
    if not turns:
        with ui.column().classes("items-center gap-3 py-12 w-full"):
            ui.icon("psychology").classes("text-4xl text-gray-300")
            ui.label("Agent decisions will appear here").classes(
                "text-sm text-gray-400"
            )
        return

    ui.label("Agent Log").classes(
        "text-xs text-gray-400 uppercase tracking-wider font-semibold"
    )

    with ui.scroll_area().classes("w-full").style("max-height: 60vh"):
        with ui.column().classes("w-full gap-2"):
            for entry in reversed(turns[-50:]):
                _render_turn_entry(entry)


def _render_turn_entry(entry: TurnEntry) -> None:
    """Render a single turn entry in the agent log."""
    if entry.error:
        border_color = "#FCA5A5"
        bg_color = "#FEF2F2"
        icon = "error"
        icon_color = "text-red-500"
    elif entry.action == "game_ended":
        border_color = "#86EFAC"
        bg_color = "#F0FDF4"
        icon = "emoji_events"
        icon_color = "text-green-600"
    elif entry.action == "think":
        border_color = "#E5E4EA"
        bg_color = "#F8F8FA"
        icon = "psychology"
        icon_color = "text-gray-400"
    else:
        border_color = "#DDD6FE"
        bg_color = "#FAFAFE"
        icon = "smart_toy"
        icon_color = "text-violet-500"

    with (
        ui.element("div")
        .classes("w-full rounded-lg p-3")
        .style(
            f"background: {bg_color}; "
            f"border: 1px solid {border_color}; "
            f"border-left: 3px solid {border_color}"
        )
    ):
        with ui.row().classes("items-center gap-2"):
            ui.icon(icon).classes(f"text-sm {icon_color}")
            ui.label(f"Turn {entry.turn}").classes("font-display text-sm font-semibold")
            if entry.action:
                ui.badge(entry.action).props("outline dense rounded").classes("text-xs")

        if entry.args:
            args_str = ", ".join(f"{k}={v}" for k, v in entry.args.items())
            ui.label(args_str).classes("text-xs font-mono text-gray-500 mt-1")

        if entry.reasoning:
            # Expandable reasoning
            text_short = entry.reasoning[:150]
            is_long = len(entry.reasoning) > 150
            with (
                ui.expansion(
                    text=text_short + ("..." if is_long else ""),
                    icon="lightbulb",
                )
                .classes("w-full text-xs")
                .props("dense")
            ):
                if is_long:
                    ui.label(entry.reasoning).classes(
                        "text-xs text-gray-500 italic whitespace-pre-wrap"
                    )

        if entry.result_summary:
            summary = entry.result_summary[:200]
            if len(entry.result_summary) > 200:
                summary += "..."
            ui.label(f"Result: {summary}").classes("text-xs text-gray-400 mt-1")

        if entry.error:
            ui.label(f"Error: {entry.error}").classes(
                "text-xs text-red-600 font-semibold mt-1"
            )


# ---------------------------------------------------------------------------
# Performance summary
# ---------------------------------------------------------------------------


def _render_performance(agent_state: dict):
    """Render performance summary when game is finished."""
    if agent_state["status"] not in ("finished", "error", "stopped"):
        return

    turns = agent_state["turns"]
    if not turns:
        return

    session = agent_state.get("session")

    with glass_card(extra_classes="w-full"):
        ui.label("Performance Summary").classes(
            "font-display font-semibold text-sm tracking-tight"
        )

        with ui.row().classes("gap-6 flex-wrap mt-2"):
            # Total turns
            with ui.column().classes("gap-0"):
                ui.label("Turns").classes(
                    "text-xs text-gray-400 uppercase tracking-wider"
                )
                ui.label(str(len(turns))).classes("text-lg font-semibold")

            # Actions breakdown
            action_counts: dict[str, int] = {}
            error_count = 0
            for t in turns:
                if t.error:
                    error_count += 1
                if t.action:
                    action_counts[t.action] = action_counts.get(t.action, 0) + 1

            with ui.column().classes("gap-0"):
                ui.label("Actions").classes(
                    "text-xs text-gray-400 uppercase tracking-wider"
                )
                ui.label(str(sum(action_counts.values()))).classes(
                    "text-lg font-semibold"
                )

            with ui.column().classes("gap-0"):
                ui.label("Errors").classes(
                    "text-xs text-gray-400 uppercase tracking-wider"
                )
                ui.label(str(error_count)).classes(
                    f"text-lg font-semibold {'text-red-600' if error_count else ''}"
                )

            # Outcome
            if session and session.state.victory_result:
                vr = session.state.victory_result
                winner = vr.get("winner", "?")
                agent_player = agent_state.get("agent_player", "agent-0")
                won = winner == agent_player

                with ui.column().classes("gap-0"):
                    ui.label("Result").classes(
                        "text-xs text-gray-400 uppercase tracking-wider"
                    )
                    if won:
                        ui.label("VICTORY").classes(
                            "text-lg font-semibold text-green-600"
                        )
                    else:
                        ui.label(f"Lost to {winner}").classes(
                            "text-lg font-semibold text-red-600"
                        )

        # Action breakdown table
        if action_counts:
            with ui.row().classes("gap-2 flex-wrap mt-2"):
                for action, count in sorted(action_counts.items(), key=lambda x: -x[1]):
                    if action in ("think", "game_ended"):
                        continue
                    ui.badge(f"{action}: {count}").props("rounded outline").classes(
                        "text-xs px-2"
                    )


# ---------------------------------------------------------------------------
# Agent lifecycle
# ---------------------------------------------------------------------------


async def _start_agent(
    strategy,
    compiled,
    agent_state,
    provider_type: str,
    model: str | None,
    api_key: str,
    game_state_refreshable,
    turn_log_refreshable,
    status_badge_refreshable,
    performance_refreshable,
) -> None:
    """Create a game session and start the agent."""
    if agent_state["status"] == "running":
        ui.notify("Agent is already running", type="warning")
        return

    # Validate API key for Anthropic
    if provider_type == "anthropic" and not api_key:
        ui.notify(
            "Enter an API key for Anthropic provider",
            type="negative",
            timeout=4000,
        )
        return

    agent_state["status"] = "running"
    agent_state["turns"] = []
    status_badge_refreshable.refresh()
    performance_refreshable.refresh()
    ui.notify("Starting agent...", type="info")

    try:
        from server.sessions import create_session, remove_session

        # Create a game session with bot players
        session_id = f"agent-{uuid.uuid4().hex[:8]}"
        min_players = compiled.min_players
        agent_player = "agent-0"
        players = [agent_player] + [f"bot-{i}" for i in range(1, min_players)]

        extra_meta = {
            "strategies": {agent_player: strategy.id},
            "strategy_name": strategy.name,
            "agent_player_id": agent_player,
            "strategy_id": strategy.id,
        }
        session = create_session(
            session_id, compiled, players, extra_metadata=extra_meta
        )
        await session.start()

        agent_state["session_id"] = session_id
        agent_state["session"] = session
        agent_state["agent_player"] = agent_player

        # Subscribe to session for live game state updates
        def _on_game_change(_new_state):
            try:
                game_state_refreshable.refresh()
            except Exception:
                pass

        session.subscribe(_on_game_change)
        agent_state["game_listener"] = _on_game_change
        ui.context.client.on_disconnect(lambda: session.unsubscribe(_on_game_change))

        # Initial game state render
        game_state_refreshable.refresh()

        # Create bridge + provider + runner
        from server.app import mcp

        bridge = InProcessBridge(mcp, agent_player)

        provider_kwargs: dict[str, Any] = {}
        if api_key:
            provider_kwargs["api_key"] = api_key

        provider = create_provider(provider_type, model=model, **provider_kwargs)
        runner = AgentRunner(
            strategy=strategy,
            bridge=bridge,
            provider=provider,
            compiled=compiled,
            on_turn=lambda entry: _on_turn(
                entry,
                agent_state,
                turn_log_refreshable,
                status_badge_refreshable,
                game_state_refreshable,
            ),
        )

        agent_state["runner"] = runner

        # Join game via MCP — cleanup session if this fails
        try:
            await bridge.initialize()
            await bridge.call_tool(
                "join_game", {"session_id": session_id, "player_name": strategy.name}
            )
        except Exception:
            remove_session(session_id)
            raise

        # Start bot runner for non-agent players
        bot_ids = [p for p in players if p != agent_player]
        bot_runner = BotRunner(session, compiled, bot_ids)
        bot_runner.start()
        agent_state["bot_runner"] = bot_runner

        # Run in background task
        async def _run():
            try:
                await runner.run_game()
                agent_state["status"] = "finished"
            except asyncio.CancelledError:
                agent_state["status"] = "stopped"
            except Exception as exc:
                log.exception("Agent error")
                agent_state["status"] = "error"
                agent_state["turns"].append(
                    TurnEntry(
                        turn=len(agent_state["turns"]) + 1,
                        timestamp=0,
                        error=str(exc),
                    )
                )
            finally:
                try:
                    # Stop bot runner
                    br = agent_state.get("bot_runner")
                    if br:
                        br.stop()
                    # Unsubscribe listener before removing session
                    sess = agent_state.get("session")
                    listener = agent_state.get("game_listener")
                    if sess and listener:
                        sess.unsubscribe(listener)
                    sid = agent_state.get("session_id")
                    if sid:
                        remove_session(sid)
                except Exception:
                    pass
                _running_agents.pop(strategy.id, None)
                _prev_resources.pop(agent_state.get("session_id", ""), None)
                status_badge_refreshable.refresh()
                turn_log_refreshable.refresh()
                game_state_refreshable.refresh()
                performance_refreshable.refresh()

        task = asyncio.create_task(_run())
        agent_state["task"] = task
        _running_agents[strategy.id] = agent_state

    except Exception as exc:
        agent_state["status"] = "error"
        # Clean up partial state on start failure
        sid = agent_state.get("session_id")
        if sid:
            _prev_resources.pop(sid, None)
            try:
                from server.sessions import remove_session

                remove_session(sid)
            except Exception:
                pass
        status_badge_refreshable.refresh()
        ui.notify(f"Failed to start agent: {exc}", type="negative")
        log.exception("Failed to start agent")


def _stop_agent(agent_state, status_badge_refreshable) -> None:
    """Stop a running agent."""
    if agent_state["status"] not in ("running",):
        return
    bot_runner = agent_state.get("bot_runner")
    if bot_runner:
        bot_runner.stop()
    runner = agent_state.get("runner")
    if runner:
        runner.stop()
    task = agent_state.get("task")
    if task and not task.done():
        task.cancel()
    agent_state["status"] = "stopped"
    status_badge_refreshable.refresh()
    ui.notify("Agent stopped", type="warning")


async def _restart_agent(
    strategy,
    compiled,
    agent_state,
    provider_type,
    model,
    api_key,
    game_state_refreshable,
    turn_log_refreshable,
    status_badge_refreshable,
    performance_refreshable,
) -> None:
    """Stop current agent and start fresh."""
    _stop_agent(agent_state, status_badge_refreshable)
    # Brief pause for cleanup
    await asyncio.sleep(0.3)
    await _start_agent(
        strategy,
        compiled,
        agent_state,
        provider_type,
        model,
        api_key,
        game_state_refreshable,
        turn_log_refreshable,
        status_badge_refreshable,
        performance_refreshable,
    )


def _on_turn(
    entry,
    agent_state,
    turn_log_refreshable,
    status_badge_refreshable,
    game_state_refreshable,
):
    """Callback invoked by AgentRunner on each turn."""
    agent_state["turns"].append(entry)
    try:
        turn_log_refreshable.refresh()
        status_badge_refreshable.refresh()
        game_state_refreshable.refresh()
    except Exception:
        pass  # UI might be disconnected
