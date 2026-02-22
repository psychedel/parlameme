"""Tournament page — create, browse, and manage tournaments."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from games import REGISTRY as GAME_REGISTRY
from server.components.layout import page_layout
from server.components.ui_kit import (
    empty_state,
    glass_card,
    section_header,
    status_chip,
)
from server.theme import GAME_COLORS, GAME_ICONS, apply_theme
from tournament.sessions import (
    TournamentSession,
    create_tournament,
    get_tournament,
    list_tournaments,
    remove_tournament,
)


def register(games: dict):
    """Register tournament pages."""

    @ui.page("/tournaments")
    def tournaments_page():
        with page_layout("Tournaments"):
            _render_create_form(games, on_created=lambda: tourney_list.refresh())

            @ui.refreshable
            def tourney_list():
                _render_tournament_list()

            tourney_list()
            ui.timer(5.0, tourney_list.refresh)

    @ui.page("/tournaments/{tournament_id}")
    def tournament_detail_page(tournament_id: str):
        apply_theme()
        session = get_tournament(tournament_id)

        if not session:
            with page_layout("Tournament Not Found", back_to="/tournaments"):
                empty_state(
                    f"Tournament '{tournament_id}' not found",
                    icon="search_off",
                )
            return

        with ui.header().classes("items-center justify-between px-4"):
            with ui.row().classes("items-center gap-2"):
                ui.button(
                    icon="arrow_back",
                    on_click=lambda: ui.navigate.to("/tournaments"),
                ).props("flat round dense color=dark")
                ui.label(session.state.name).classes(
                    "font-display text-lg font-semibold tracking-tight"
                )
            with ui.row().classes("items-center gap-2"):
                ui.badge(session.state.tournament_type).props(
                    "rounded outline"
                ).classes("text-xs px-2")
                _status_color = {
                    "registration": "accent",
                    "in_progress": "positive",
                    "completed": "warning",
                    "cancelled": "negative",
                }.get(session.state.status, "grey")
                ui.badge(session.state.status.upper()).props(
                    f"color={_status_color} rounded"
                ).classes("text-xs px-2")

        # Push updates
        def on_change(_new_state):
            detail_view.refresh()

        session.subscribe(on_change)
        ui.context.client.on_disconnect(lambda: session.unsubscribe(on_change))

        @ui.refreshable
        def detail_view():
            _render_tournament_detail(session, games)

        with ui.column().classes("w-full max-w-5xl mx-auto px-4 py-6 gap-4"):
            detail_view()


def _render_create_form(games: dict, on_created):
    """Form to create a new tournament."""
    section_header("Create Tournament", icon="add_circle")

    with glass_card(extra_classes="w-full"):
        with ui.row().classes("w-full gap-4 flex-wrap items-end"):
            t_id = (
                ui.input("Tournament ID", placeholder="cup-1")
                .props("outlined dense")
                .classes("w-40")
            )
            t_name = (
                ui.input("Name", placeholder="My Cup")
                .props("outlined dense")
                .classes("w-48")
            )
            t_type = (
                ui.select(
                    ["round_robin", "single_elimination", "swiss"],
                    value="round_robin",
                    label="Format",
                )
                .props("outlined dense")
                .classes("w-44")
            )
            t_game = (
                ui.select(
                    list(games.keys()),
                    value=list(games.keys())[0] if games else None,
                    label="Game",
                )
                .props("outlined dense")
                .classes("w-36")
            )
            t_max = (
                ui.number("Max Players", value=8, min=2, max=64)
                .props("outlined dense")
                .classes("w-28")
            )

            async def _create():
                tid = t_id.value
                if not tid:
                    ui.notify("Enter tournament ID", type="warning")
                    return
                if get_tournament(tid):
                    ui.notify(f"Tournament '{tid}' already exists", type="negative")
                    return
                try:
                    create_tournament(
                        tournament_id=tid,
                        tournament_type=t_type.value,
                        host="ui-host",
                        game_type=t_game.value,
                        name=t_name.value or f"{t_type.value} tournament",
                        max_participants=int(t_max.value),
                    )
                    ui.notify(f"Created tournament: {tid}", type="positive")
                    on_created()
                except Exception as exc:
                    ui.notify(str(exc), type="negative")

            ui.button("Create", on_click=_create, icon="add").props(
                "unelevated no-caps color=primary"
            ).classes("rounded-lg")


def _render_tournament_list():
    """List all tournaments."""
    section_header("Tournaments", icon="emoji_events")

    tournaments = list_tournaments()
    if not tournaments:
        empty_state("No tournaments yet", icon="emoji_events", sub="Create one above")
        return

    for tid, tsess in tournaments.items():
        ts = tsess.state
        status_color = {
            "registration": "accent",
            "in_progress": "positive",
            "completed": "warning",
            "cancelled": "negative",
        }.get(ts.status, "grey")
        game_icon = GAME_ICONS.get(ts.game_type, "casino")
        game_color = GAME_COLORS.get(ts.game_type, "#6D28D9")

        with glass_card(extra_classes="w-full"):
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-3"):
                    with (
                        ui.element("div")
                        .classes("rounded-lg p-2 flex items-center justify-center")
                        .style(f"background: {game_color}15")
                    ):
                        ui.icon(game_icon).classes("text-lg").style(
                            f"color: {game_color}"
                        )
                    with ui.column().classes("gap-0.5"):
                        ui.label(ts.name).classes(
                            "font-display font-semibold tracking-tight"
                        )
                        ui.label(
                            f"{ts.tournament_type} \u00b7 {ts.game_type} \u00b7 "
                            f"{len(ts.participants)}/{ts.max_participants} players"
                        ).classes("text-xs text-gray-400")
                        if ts.participants:
                            ui.label(", ".join(ts.participants)).classes(
                                "text-xs text-gray-300"
                            )
                with ui.row().classes("items-center gap-2"):
                    status_chip(ts.status, status_color)
                    ui.button(
                        "Details",
                        on_click=lambda t=tid: ui.navigate.to(f"/tournaments/{t}"),
                    ).props("unelevated no-caps dense color=primary").classes(
                        "rounded-lg"
                    )
                    ui.button(
                        icon="delete",
                        on_click=lambda t=tid: _delete(t),
                    ).props("flat round dense color=negative")

    def _delete(tid: str):
        remove_tournament(tid)
        ui.notify(f"Deleted: {tid}", type="warning")


def _render_tournament_detail(session: TournamentSession, games: dict):
    """Render full tournament detail view."""
    ts = session.state
    compiled = GAME_REGISTRY.get(ts.game_type)

    # --- Info ---
    with glass_card(extra_classes="w-full"):
        with ui.row().classes("gap-6 flex-wrap"):
            for label, val in [
                ("ID", ts.tournament_id),
                ("Host", ts.host),
                ("Game", ts.game_type),
            ]:
                with ui.column().classes("gap-0"):
                    ui.label(label).classes(
                        "text-xs text-gray-400 uppercase tracking-wider"
                    )
                    ui.label(str(val)).classes("text-sm font-medium")
            if ts.winner:
                with ui.column().classes("gap-0"):
                    ui.label("Winner").classes(
                        "text-xs text-gray-400 uppercase tracking-wider"
                    )
                    ui.label(ts.winner).classes("text-sm font-semibold text-green-600")

    # --- Actions ---
    if ts.status == "registration":
        with glass_card(extra_classes="w-full"):
            ui.label("Registration").classes("font-display font-semibold text-sm")
            with ui.row().classes("items-end gap-3 mt-2"):
                p_input = (
                    ui.input("Player ID", placeholder="alice")
                    .props("outlined dense")
                    .classes("w-40")
                )

                async def _register():
                    pid = p_input.value
                    if not pid:
                        return
                    try:
                        await session.register(pid)
                        p_input.value = ""
                        ui.notify(f"Registered: {pid}", type="positive")
                    except Exception as exc:
                        ui.notify(str(exc), type="negative")

                ui.button("Register", on_click=_register).props(
                    "unelevated no-caps dense"
                ).classes("rounded-lg")

                async def _start():
                    try:
                        await session.start(compiled)
                        ui.notify("Tournament started!", type="positive")
                    except Exception as exc:
                        ui.notify(str(exc), type="negative")

                ui.button("Start Tournament", on_click=_start).props(
                    "unelevated no-caps dense color=positive"
                ).classes("rounded-lg")

    # --- Standings ---
    section_header("Standings", icon="leaderboard")

    if ts.standings:
        from tournament.runtime import TournamentRuntime

        rt = TournamentRuntime()
        standings = rt.get_standings_sorted(ts)

        columns = [
            {"name": "rank", "label": "#", "field": "rank", "align": "center"},
            {"name": "player", "label": "Player", "field": "participant"},
            {"name": "pts", "label": "Pts", "field": "points", "align": "center"},
            {"name": "w", "label": "W", "field": "wins", "align": "center"},
            {"name": "l", "label": "L", "field": "losses", "align": "center"},
            {"name": "gd", "label": "GD", "field": "goal_diff", "align": "center"},
        ]
        rows = []
        for i, st in enumerate(standings, 1):
            rows.append(
                {
                    "rank": i,
                    "participant": st.participant,
                    "points": st.points,
                    "wins": st.wins,
                    "losses": st.losses,
                    "goal_diff": st.goal_diff,
                }
            )
        ui.table(columns=columns, rows=rows, row_key="participant").classes(
            "w-full"
        ).props("dense flat")
    else:
        empty_state("No standings yet", icon="leaderboard")

    # --- Matches ---
    if ts.tournament_type == "single_elimination" and ts.matches:
        _render_bracket(ts, session, compiled)
    else:
        _render_matches_by_round(ts, session, compiled)


def _render_bracket(ts, session, compiled):
    """Render single-elimination bracket as a visual tree."""
    section_header("Bracket", icon="account_tree")

    # Group matches by round
    rounds: dict[int, list] = {}
    for mid, match in ts.matches.items():
        rounds.setdefault(match.round, []).append((mid, match))
    for r in rounds:
        rounds[r].sort(key=lambda x: x[0])

    if not rounds:
        empty_state("No matches yet", icon="sports")
        return

    # Bracket layout: horizontal columns per round
    with ui.row().classes("w-full gap-4 overflow-x-auto items-stretch"):
        for rnd in sorted(rounds.keys()):
            matches = rounds[rnd]
            with ui.column().classes("gap-3 min-w-48"):
                ui.label(f"Round {rnd}").classes(
                    "text-xs text-gray-400 uppercase tracking-wider font-semibold text-center"
                )
                for mid, match in matches:
                    _render_bracket_match(mid, match, session, compiled)

    # Final winner
    if ts.winner:
        with ui.row().classes("items-center justify-center gap-2 mt-4"):
            ui.icon("emoji_events").classes("text-2xl text-amber-600")
            ui.label(f"Champion: {ts.winner}").classes(
                "font-display text-lg font-semibold text-green-600"
            )


def _render_bracket_match(mid, match, session, compiled):
    """Render a single match in the bracket."""
    border_color = {
        "pending": "#E5E4EA",
        "active": "#6D28D9",
        "completed": "#16A34A",
    }.get(match.status, "#E5E4EA")

    with (
        ui.card()
        .classes("w-full")
        .props("flat")
        .style(
            f"border: 1px solid {border_color}; border-left: 3px solid {border_color}"
        )
    ):
        for p in match.participants:
            is_winner = match.winner == p
            cls = "text-sm font-semibold text-green-600" if is_winner else "text-sm"
            with ui.row().classes("items-center gap-1"):
                if is_winner:
                    ui.icon("check").classes("text-xs text-green-600")
                ui.label(p).classes(cls)
        # Status + actions
        with ui.row().classes("items-center gap-1 mt-1"):
            ui.badge(match.status).props(
                f"rounded {'color=positive' if match.status == 'completed' else 'outline'}"
            ).classes("text-xs px-1")
            if match.status == "active" and match.session_id:
                ui.button(
                    "Spectate",
                    on_click=lambda sid=match.session_id: ui.navigate.to(
                        f"/spectate/{sid}"
                    ),
                    icon="visibility",
                ).props("flat no-caps dense color=secondary").classes("text-xs")
            if match.status == "active":
                for p in match.participants:

                    async def _report(m=mid, w=p):
                        try:
                            await session.report_result(m, w, compiled=compiled)
                            ui.notify(f"{w} wins!", type="positive")
                        except Exception as exc:
                            ui.notify(str(exc), type="negative")

                    ui.button(f"{p}", on_click=_report, icon="emoji_events").props(
                        "flat no-caps dense color=positive"
                    ).classes("text-xs")


def _render_matches_by_round(ts, session, compiled):
    """Render matches grouped by round (for round_robin and swiss)."""
    section_header("Matches", icon="sports")

    if not ts.matches:
        empty_state("No matches yet", icon="sports")
        return

    # Group by round
    rounds: dict[int, list] = {}
    for mid, match in ts.matches.items():
        rounds.setdefault(match.round, []).append((mid, match))

    for rnd in sorted(rounds.keys()):
        matches = rounds[rnd]
        ui.label(f"Round {rnd}").classes(
            "text-xs text-gray-400 uppercase tracking-wider font-semibold mt-3"
        )
        for mid, match in sorted(matches, key=lambda x: x[0]):
            status_color = {
                "pending": "grey-7",
                "active": "primary",
                "completed": "positive",
            }.get(match.status, "grey-7")

            with glass_card(extra_classes="w-full"):
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.column().classes("gap-0.5"):
                        with ui.row().classes("items-center gap-1"):
                            for pi, p in enumerate(match.participants):
                                if p == match.winner:
                                    ui.label(p).classes(
                                        "font-display font-bold text-green-600"
                                    )
                                else:
                                    ui.label(p).classes("font-display font-semibold")
                                if pi < len(match.participants) - 1:
                                    ui.label("vs").classes(
                                        "text-xs text-gray-400 mx-1"
                                    )
                        ui.label(mid).classes("text-xs text-gray-400")
                    with ui.row().classes("items-center gap-2 flex-wrap"):
                        status_chip(match.status, status_color)
                        if match.winner:
                            ui.badge(f"Winner: {match.winner}").props(
                                "color=positive rounded"
                            ).classes("text-xs px-2")
                        if match.status == "active" and match.session_id:
                            ui.button(
                                "Spectate",
                                on_click=lambda sid=match.session_id: ui.navigate.to(
                                    f"/spectate/{sid}"
                                ),
                                icon="visibility",
                            ).props("unelevated no-caps dense color=secondary").classes(
                                "rounded-lg"
                            )
                        if match.status == "active":
                            for p in match.participants:

                                async def _report(m=mid, w=p):
                                    try:
                                        await session.report_result(
                                            m, w, compiled=compiled
                                        )
                                        ui.notify(f"{w} wins!", type="positive")
                                    except Exception as exc:
                                        ui.notify(str(exc), type="negative")

                                ui.button(
                                    f"{p} wins",
                                    on_click=_report,
                                ).props(
                                    "unelevated no-caps dense outline color=positive"
                                ).classes("rounded-lg")
