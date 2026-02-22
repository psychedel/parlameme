"""Shared page layout shell — light minimalist design system.

Provides consistent header, sidebar nav (desktop), bottom nav (mobile),
and main content container across all pages.
"""

from __future__ import annotations

from contextlib import contextmanager

from nicegui import ui

from server.theme import apply_theme

# Navigation items
_NAV = [
    ("Home", "home", "/"),
    ("Tournaments", "emoji_events", "/tournaments"),
    ("Analytics", "analytics", "/analytics"),
    ("Workshop", "smart_toy", "/strategies"),
]


@contextmanager
def page_layout(
    title: str,
    *,
    back_to: str | None = "/",
    show_nav: bool = True,
):
    """Shared page layout with header, optional nav, and content area.

    Usage::

        with page_layout("Analytics"):
            ui.label("Content goes here")
    """
    apply_theme()

    # --- Header ---
    with ui.header().classes("items-center justify-between px-4"):
        with ui.row().classes("items-center gap-2"):
            if show_nav:
                ui.button(icon="menu", on_click=lambda: drawer.toggle()).props(
                    "flat round dense color=dark"
                ).classes("lg:hidden")
            if back_to:
                ui.button(
                    icon="arrow_back",
                    on_click=lambda: ui.navigate.to(back_to),
                ).props("flat round dense color=dark")
            ui.label(title).classes(
                "font-display text-lg font-semibold tracking-tight text-gray-900"
            )
        # Desktop nav (hidden on mobile)
        if show_nav:
            with ui.row().classes("gap-1 hidden lg:flex"):
                for label, icon, path in _NAV:
                    ui.button(
                        label,
                        icon=icon,
                        on_click=lambda p=path: ui.navigate.to(p),
                    ).props("flat no-caps color=dark dense")

    # --- Sidebar drawer (auto-show on large screens) ---
    drawer = None
    if show_nav:
        drawer = ui.left_drawer(value=None).classes(
            "!bg-white border-r border-gray-200 pt-4"
        )
        with drawer:
            # Logo
            ui.label("Parlameme").classes(
                "font-display text-xl font-bold px-4 pb-4 tracking-tight text-gray-900"
            )
            ui.separator().classes("mb-2")
            # Nav links
            for label, icon, path in _NAV:
                with (
                    ui.row()
                    .classes(
                        "items-center gap-3 px-4 py-2.5 mx-2 rounded-lg "
                        "cursor-pointer hover:bg-gray-50 transition-all duration-200"
                    )
                    .on("click", lambda p=path: ui.navigate.to(p))
                ):
                    ui.icon(icon).classes("text-lg text-gray-400")
                    ui.label(label).classes("text-sm font-medium text-gray-600")

    # --- Mobile bottom nav ---
    with ui.footer().classes("lg:hidden justify-around py-1"):
        for label, icon, path in _NAV:
            ui.button(
                icon=icon,
                on_click=lambda p=path: ui.navigate.to(p),
            ).props("flat round dense color=dark").classes("text-gray-500")

    # --- Main content area ---
    with ui.column().classes("w-full max-w-6xl mx-auto px-4 py-6 gap-6"):
        yield


@contextmanager
def page_layout_clean(
    title: str,
    *,
    back_to: str | None = "/",
):
    """Minimal layout without nav — for full-width pages like workshop."""
    apply_theme()

    with ui.header().classes("items-center justify-between px-4"):
        with ui.row().classes("items-center gap-2"):
            if back_to:
                ui.button(
                    icon="arrow_back",
                    on_click=lambda: ui.navigate.to(back_to),
                ).props("flat round dense color=dark")
            ui.label(title).classes(
                "font-display text-lg font-semibold tracking-tight text-gray-900"
            )
        yield "header_right"  # Caller can add buttons after this
