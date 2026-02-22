"""Analytics page — platform stats, player leaderboard, game balance."""

from __future__ import annotations

from nicegui import ui

from server.analytics import game_type_stats, head_to_head, platform_stats, player_stats
from server.components.layout import page_layout
from server.components.ui_kit import (
    empty_state,
    glass_card,
    section_header,
    stat_widget,
)
from server.theme import GAME_COLORS, GAME_ICONS


def register(games: dict):
    """Register the /analytics page."""

    @ui.page("/analytics")
    def analytics_page():
        with page_layout("Analytics"):
            _render_platform_overview()

            with ui.tabs().classes("w-full").props("no-caps dense") as tabs:
                ui.tab("Leaderboard", icon="leaderboard")
                ui.tab("Game Types", icon="casino")
                ui.tab("Head to Head", icon="compare_arrows")

            with ui.tab_panels(tabs, value="Leaderboard").classes("w-full"):
                with ui.tab_panel("Leaderboard"):
                    _render_leaderboard()

                with ui.tab_panel("Game Types"):
                    _render_game_types(games)

                with ui.tab_panel("Head to Head"):
                    _render_head_to_head()


def _render_platform_overview():
    """Platform-wide statistics cards."""
    stats = platform_stats()

    section_header("Platform Overview", icon="analytics")
    with ui.row().classes("w-full gap-4 flex-wrap"):
        stat_widget(stats["total_games"], "Total Games", "sports_esports")
        stat_widget(
            stats["unique_players"], "Unique Players", "people", color="secondary"
        )
        stat_widget(
            stats["total_decisions"], "Total Decisions", "touch_app", color="accent"
        )
        stat_widget(
            stats["avg_decisions_per_game"], "Avg/Game", "analytics", color="positive"
        )

    # Games by type bar
    by_type = stats.get("games_by_type", {})
    if by_type:
        with glass_card(extra_classes="w-full"):
            ui.label("Games by Type").classes("font-display font-semibold text-sm mb-3")
            max_count = max(by_type.values()) if by_type else 1
            for gid, count in by_type.items():
                icon = GAME_ICONS.get(gid, "casino")
                color = GAME_COLORS.get(gid, "#6D28D9")
                pct = count / max_count if max_count else 0
                with ui.row().classes("items-center gap-3 w-full"):
                    ui.icon(icon).classes("text-lg").style(f"color: {color}")
                    ui.label(gid).classes("w-32 text-sm text-gray-500")
                    with (
                        ui.element("div")
                        .classes("flex-grow h-2 rounded-full overflow-hidden")
                        .style("background: #F1F0F5")
                    ):
                        ui.element("div").classes("h-full rounded-full").style(
                            f"width: {pct * 100:.0f}%; background: {color}"
                        )
                    ui.label(str(count)).classes(
                        "text-sm w-8 text-right text-gray-500 font-mono"
                    )


def _render_leaderboard():
    """Player leaderboard table with ratings and streaks."""
    players = player_stats()

    if not players:
        empty_state("No games played yet", icon="leaderboard")
        return

    _MEDALS = {
        1: ("emoji_events", "#FFD700"),
        2: ("emoji_events", "#C0C0C0"),
        3: ("emoji_events", "#CD7F32"),
    }

    columns = [
        {"name": "rank", "label": "#", "field": "rank", "align": "center"},
        {"name": "player", "label": "Player", "field": "player_id"},
        {"name": "rating", "label": "Rating", "field": "rating", "align": "center"},
        {"name": "tier", "label": "Tier", "field": "tier", "align": "center"},
        {"name": "games", "label": "Games", "field": "games", "align": "center"},
        {"name": "wins", "label": "Wins", "field": "wins", "align": "center"},
        {"name": "win_rate", "label": "Win %", "field": "win_rate", "align": "center"},
        {
            "name": "streak",
            "label": "Streak",
            "field": "streak_display",
            "align": "center",
        },
        {
            "name": "form",
            "label": "Recent Form",
            "field": "form_display",
            "align": "center",
        },
    ]

    rows = []
    for i, p in enumerate(players, 1):
        streak = p.get("streak", {})
        streak_type = streak.get("type")
        streak_count = streak.get("count", 0)
        if streak_type == "W":
            streak_display = f"W{streak_count}"
        elif streak_type == "L":
            streak_display = f"L{streak_count}"
        else:
            streak_display = "-"

        form = p.get("recent_form", [])
        form_display = "".join(form[-10:]) if form else "-"

        rows.append(
            {
                "rank": i,
                **p,
                "streak_display": streak_display,
                "form_display": form_display,
            }
        )

    ui.table(columns=columns, rows=rows, row_key="player_id").classes("w-full").props(
        "dense flat"
    )


def _render_game_types(games: dict):
    """Per-game-type statistics."""
    gt_stats = game_type_stats()

    if not gt_stats:
        empty_state("No games played yet", icon="casino")
        return

    for gt in gt_stats:
        gid = gt["game_id"]
        info = games.get(gid, {})
        icon = GAME_ICONS.get(gid, "casino")
        color = GAME_COLORS.get(gid, "#6D28D9")

        with glass_card(extra_classes="w-full"):
            with ui.row().classes("items-center gap-3"):
                with (
                    ui.element("div")
                    .classes("rounded-lg p-2 flex items-center justify-center")
                    .style(f"background: {color}15")
                ):
                    ui.icon(icon).classes("text-xl").style(f"color: {color}")
                ui.label(info.get("name", gid) if info else gid).classes(
                    "font-display text-lg font-semibold"
                )

            with ui.row().classes("gap-6 mt-2"):
                for label, val in [
                    ("Games", gt["games_played"]),
                    ("Players", gt["unique_players"]),
                    ("Avg decisions", gt["avg_decisions"]),
                    ("Avg rounds", gt["avg_rounds"]),
                ]:
                    with ui.column().classes("items-center"):
                        ui.label(str(val)).classes("font-display text-lg font-bold")
                        ui.label(label).classes("text-xs text-gray-400")

            breakdown = gt.get("decision_breakdown", {})
            if breakdown:
                with ui.row().classes("gap-1.5 flex-wrap mt-3"):
                    for dtype, count in breakdown.items():
                        ui.badge(f"{dtype}: {count}").props("outline rounded").classes(
                            "text-xs text-gray-500 px-2"
                        )


def _render_head_to_head():
    """Head-to-head comparison between two players."""
    section_header("Compare Players", icon="compare_arrows")

    with ui.row().classes("items-end gap-4"):
        player_a = (
            ui.input("Player A", placeholder="player-1")
            .props("outlined dense")
            .classes("w-40")
        )
        player_b = (
            ui.input("Player B", placeholder="player-2")
            .props("outlined dense")
            .classes("w-40")
        )

        async def compare():
            a = player_a.value
            b = player_b.value
            if not a or not b:
                ui.notify("Enter both player IDs", type="warning")
                return
            h2h_result.refresh()

        ui.button("Compare", on_click=compare, icon="compare_arrows").props(
            "unelevated no-caps color=primary"
        ).classes("rounded-lg")

    @ui.refreshable
    def h2h_result():
        a = player_a.value
        b = player_b.value
        if not a or not b:
            return

        result = head_to_head(a, b)
        if result["total_games"] == 0:
            empty_state(f"No shared games between {a} and {b}", icon="person_search")
            return

        with glass_card(extra_classes="w-full mt-4"):
            with ui.row().classes("items-center justify-around w-full"):
                with ui.column().classes("items-center"):
                    ui.label(a).classes("font-display text-lg font-semibold")
                    ui.label(str(result["a_wins"])).classes(
                        "font-display text-4xl font-bold text-green-600"
                    )
                    ui.label("wins").classes("text-xs text-gray-400")
                ui.label("vs").classes("font-display text-xl text-gray-300")
                with ui.column().classes("items-center"):
                    ui.label(b).classes("font-display text-lg font-semibold")
                    ui.label(str(result["b_wins"])).classes(
                        "font-display text-4xl font-bold text-green-600"
                    )
                    ui.label("wins").classes("text-xs text-gray-400")

            if result["draws"]:
                ui.label(f"{result['draws']} draws").classes(
                    "text-center text-sm text-gray-500"
                )

            ui.label(f"Total games: {result['total_games']}").classes(
                "text-center text-xs text-gray-400"
            )

            if result["games"]:
                ui.separator()
                for g in result["games"]:
                    winner = g["winner"] or "draw"
                    ui.label(
                        f"{g['game_id']} ({g['session_id']}): "
                        f"winner={winner}, {g['decisions']} decisions"
                    ).classes("text-xs text-gray-400")

    h2h_result()
