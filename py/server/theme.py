"""Light minimalist design system — single source of truth for all visual constants.

Every @ui.page should call apply_theme() at the top.
"""

from nicegui import ui

# ---------------------------------------------------------------------------
# Google Fonts
# ---------------------------------------------------------------------------

FONTS_HTML = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
"""

# ---------------------------------------------------------------------------
# CSS theme — injected via ui.add_css() with @layer for clean cascade
# ---------------------------------------------------------------------------

THEME_CSS = """
@layer overrides {
    /* --- Typography --- */
    body, .q-field__native, .q-btn, .q-tab__label,
    .q-table, .q-item, .q-badge {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
        color: #1A1A2E;
    }
    .font-display {
        font-family: 'Space Grotesk', sans-serif !important;
    }
    .font-mono, code, pre {
        font-family: 'JetBrains Mono', monospace !important;
    }

    /* --- Light card surfaces --- */
    .glass {
        background: #FFFFFF !important;
        border: 1px solid #E5E4EA !important;
        border-radius: 12px !important;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.02);
        transition: all 0.2s ease;
    }
    .glass:hover {
        border-color: #D1D0D7 !important;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
    }
    .glass-static {
        background: #FFFFFF !important;
        border: 1px solid #E5E4EA !important;
        border-radius: 12px !important;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.02);
    }

    /* --- Header --- */
    .q-header {
        background: rgba(255, 255, 255, 0.92) !important;
        backdrop-filter: blur(12px) saturate(180%);
        -webkit-backdrop-filter: blur(12px) saturate(180%);
        border-bottom: 1px solid #E5E4EA;
        color: #1A1A2E !important;
    }

    /* --- Left drawer --- */
    .q-drawer {
        background: #FFFFFF !important;
        border-right: 1px solid #E5E4EA;
    }

    /* --- Footer (mobile nav) --- */
    .q-footer {
        background: rgba(255, 255, 255, 0.92) !important;
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border-top: 1px solid #E5E4EA;
    }

    /* --- Tabs --- */
    .q-tab {
        text-transform: none !important;
        font-weight: 500;
        letter-spacing: 0;
    }
    .q-tab-panels {
        background: transparent !important;
    }
    .q-tab-panel {
        padding: 16px 0 !important;
    }

    /* --- Tables --- */
    .q-table {
        background: #FFFFFF !important;
        border-radius: 12px !important;
        border: 1px solid #E5E4EA;
    }
    .q-table thead th {
        font-weight: 600;
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #6B7280;
        border-bottom: 1px solid #E5E4EA !important;
    }
    .q-table tbody td {
        border-bottom: 1px solid #F1F0F5 !important;
    }
    .q-table tbody tr:hover {
        background: #F8F8FA !important;
    }

    /* --- Inputs --- */
    .q-field--outlined .q-field__control {
        border-radius: 8px !important;
    }

    /* --- Scrollbar --- */
    ::-webkit-scrollbar {
        width: 6px;
        height: 6px;
    }
    ::-webkit-scrollbar-track {
        background: transparent;
    }
    ::-webkit-scrollbar-thumb {
        background: rgba(0, 0, 0, 0.1);
        border-radius: 3px;
    }
    ::-webkit-scrollbar-thumb:hover {
        background: rgba(0, 0, 0, 0.18);
    }

    /* --- Badge refinements --- */
    .q-badge {
        font-weight: 500;
        letter-spacing: 0.01em;
    }

    /* --- Separator --- */
    .q-separator {
        background: #E5E4EA !important;
    }

    /* --- Dialog --- */
    .q-dialog__inner > .q-card {
        background: #FFFFFF !important;
        border: 1px solid #E5E4EA;
        border-radius: 16px !important;
    }

    /* --- Glow utilities --- */
    .glow-primary {
        box-shadow: 0 0 0 1px rgba(109, 40, 217, 0.15),
                    0 4px 12px rgba(109, 40, 217, 0.08);
    }
    .glow-self {
        box-shadow: 0 0 0 2px rgba(109, 40, 217, 0.2),
                    0 2px 8px rgba(109, 40, 217, 0.08);
    }
}
"""

# ---------------------------------------------------------------------------
# Game icons
# ---------------------------------------------------------------------------

GAME_ICONS = {
    "auction": "gavel",
    "werewolf": "nights_stay",
    "parliament_arena": "account_balance",
    "exchange": "trending_up",
}

# Game accent colors for hero cards
GAME_COLORS = {
    "auction": "#D97706",  # amber-600
    "werewolf": "#7C3AED",  # violet-600
    "parliament_arena": "#0891B2",  # cyan-600
    "exchange": "#16A34A",  # green-600
}


def apply_theme() -> None:
    """Apply the light minimalist theme to the current page. Call at the top of every @ui.page."""
    ui.dark_mode(False)
    ui.colors(
        primary="#6D28D9",
        secondary="#0891B2",
        accent="#D97706",
        positive="#16A34A",
        negative="#DC2626",
    )
    ui.add_head_html(FONTS_HTML)
    ui.add_css(THEME_CSS)
