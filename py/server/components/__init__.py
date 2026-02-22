"""Reusable NiceGUI UI components."""

from server.components.action_panel import render_action_panel
from server.components.chat_panel import render_chat_panel
from server.components.entity_card import render_entity_card
from server.components.game_info import render_game_info
from server.components.history import render_history
from server.components.layout import page_layout, page_layout_clean
from server.components.replay_controls import render_change, render_replay_transport
from server.components.ui_kit import (
    empty_state,
    game_hero_card,
    glass_card,
    glass_card_static,
    nav_item,
    section_header,
    stat_widget,
    status_chip,
)

__all__ = [
    "render_entity_card",
    "render_action_panel",
    "render_chat_panel",
    "render_game_info",
    "render_history",
    "render_replay_transport",
    "render_change",
    "page_layout",
    "page_layout_clean",
    "glass_card",
    "glass_card_static",
    "section_header",
    "empty_state",
    "stat_widget",
    "status_chip",
    "game_hero_card",
    "nav_item",
]
