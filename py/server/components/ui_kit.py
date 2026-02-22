"""Reusable styled UI components — light minimalist design system."""

from __future__ import annotations

from typing import Any, Callable

from nicegui import ui

from server.theme import GAME_COLORS, GAME_ICONS


def glass_card(*, extra_classes: str = "") -> ui.card:
    """Light surface card. Use as context manager."""
    return ui.card().classes(f"glass {extra_classes}").props("flat")


def glass_card_static(*, extra_classes: str = "") -> ui.card:
    """Light surface card without hover effect."""
    return ui.card().classes(f"glass-static {extra_classes}").props("flat")


def section_header(text: str, *, icon: str | None = None) -> None:
    """Section title with optional icon."""
    with ui.row().classes("items-center gap-2"):
        if icon:
            ui.icon(icon).classes("text-lg text-gray-400")
        ui.label(text).classes("font-display text-xl font-semibold tracking-tight")


def empty_state(text: str, *, icon: str = "inbox", sub: str = "") -> None:
    """Centered empty state placeholder."""
    with ui.column().classes("items-center gap-3 py-12 w-full"):
        ui.icon(icon).classes("text-4xl text-gray-300")
        ui.label(text).classes("text-base text-gray-400")
        if sub:
            ui.label(sub).classes("text-sm text-gray-300")


def stat_widget(
    value: str | int | float,
    label: str,
    icon: str,
    *,
    color: str = "primary",
) -> None:
    """Metric card — icon, large value, small label."""
    with glass_card(extra_classes="items-center py-4 px-6 min-w-[140px]"):
        ui.icon(icon).classes(f"text-2xl text-{color}")
        ui.label(str(value)).classes("font-display text-3xl font-bold tracking-tight")
        ui.label(label).classes("text-xs text-gray-500 uppercase tracking-wider")


def status_chip(text: str, color: str) -> None:
    """Rounded status pill."""
    ui.badge(text.upper()).props(f"color={color} rounded").classes(
        "text-xs font-semibold px-2"
    )


def game_hero_card(
    game_id: str,
    info: dict[str, Any],
    on_click: Callable,
) -> None:
    """Hero card for a game — used in lobby."""
    icon = GAME_ICONS.get(game_id, "casino")
    accent = GAME_COLORS.get(game_id, "#6D28D9")
    min_p, max_p = info["players"]

    with glass_card(extra_classes="w-full").style(f"border-left: 3px solid {accent}"):
        with ui.row().classes("items-center gap-4 w-full"):
            # Icon
            with (
                ui.element("div")
                .classes("rounded-xl p-3 flex items-center justify-center")
                .style(f"background: {accent}15")
            ):
                ui.icon(icon).classes("text-3xl").style(f"color: {accent}")
            # Info
            with ui.column().classes("flex-grow gap-0.5"):
                ui.label(info["name"]).classes(
                    "font-display text-lg font-semibold tracking-tight"
                )
                ui.label(info["desc"]).classes("text-sm text-gray-500")
                ui.label(f"{min_p}\u2013{max_p} players").classes(
                    "text-xs text-gray-400"
                )
            # CTA
            ui.button("Create", on_click=on_click, icon="add").props(
                "unelevated no-caps"
            ).classes("rounded-lg").style(f"background: {accent} !important")


def nav_item(
    text: str,
    icon: str,
    to: str,
    *,
    active: bool = False,
) -> None:
    """Sidebar navigation item."""
    bg = "bg-gray-100" if active else "hover:bg-gray-50"
    text_cls = "text-gray-900" if active else "text-gray-500 hover:text-gray-700"
    with (
        ui.row()
        .classes(
            f"items-center gap-3 px-3 py-2 rounded-lg cursor-pointer {bg} transition-all duration-200"
        )
        .on("click", lambda: ui.navigate.to(to))
    ):
        ui.icon(icon).classes(f"text-lg {text_cls}")
        ui.label(text).classes(f"text-sm font-medium {text_cls}")
