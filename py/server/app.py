"""NiceGUI application — game lobby, play interface, and MCP HTTP endpoint.

Architecture:
- Play page uses session.subscribe() for push updates (no polling)
- All state rendered through view_for() for visibility filtering
- Player identity via URL: /play/{session_id}/{player_id}
- MCP agent actions push to UI automatically via shared GameSession
- Components extracted to server/components/
- Pages extracted to server/pages/
"""

from __future__ import annotations

import asyncio
import datetime
import itertools
import json
import logging
from pathlib import Path
from typing import Any

from nicegui import app, ui

from engine.archive import load_archive
from engine.ledger import FileLedger
from engine.runtime.state import SPECTATOR_ID, CompiledGame, GameState, view_for
from games import REGISTRY as GAME_REGISTRY
from mcp.server import MCPServer
from server.components import (
    render_action_panel,
    render_change,
    render_chat_panel,
    render_entity_card,
    render_game_info,
    render_history,
    render_replay_transport,
)
from server.components.layout import page_layout
from server.components.ui_kit import (
    empty_state,
    game_hero_card,
    glass_card,
    section_header,
    status_chip,
)
from server.replay import ReplayController
from server.sessions import (
    GameSession,
    create_session,
    get_persistence,
    get_session,
    list_archives,
    list_sessions,
    recover_sessions,
    remove_session,
    set_ledger,
)
from server.theme import GAME_COLORS, GAME_ICONS, apply_theme

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCP server + store adapters
# ---------------------------------------------------------------------------


class _SessionStoreAdapter:
    def get(self, session_id: str):
        return get_session(session_id)

    def list_all(self):
        return list_sessions()

    def create(self, session_id, compiled, player_ids):
        return create_session(session_id, compiled, player_ids)

    def remove(self, session_id):
        remove_session(session_id)


class _TournamentStoreAdapter:
    def get(self, tournament_id: str):
        from tournament.sessions import get_tournament

        return get_tournament(tournament_id)

    def list_all(self):
        from tournament.sessions import list_tournaments

        return list_tournaments()

    def create(self, **kwargs):
        from tournament.sessions import create_tournament

        return create_tournament(**kwargs)

    def remove(self, tournament_id: str):
        from tournament.sessions import remove_tournament

        return remove_tournament(tournament_id)


_ledger: FileLedger | None = None


def get_ledger() -> FileLedger | None:
    return _ledger


mcp = MCPServer(
    sessions=_SessionStoreAdapter(),
    tournaments=_TournamentStoreAdapter(),
)

for game_id, compiled in GAME_REGISTRY.items():
    mcp.register_game(compiled)


# ---------------------------------------------------------------------------
# Game catalog (derived from registry)
# ---------------------------------------------------------------------------

_ICONS = {
    "auction": "gavel",
    "werewolf": "nights_stay",
    "parliament_arena": "account_balance",
}

GAMES: dict[str, dict[str, Any]] = {}
for _gid, _compiled in GAME_REGISTRY.items():
    GAMES[_gid] = {
        "compiled": _compiled,
        "name": _compiled.name,
        "icon": _ICONS.get(_gid, "casino"),
        "desc": (
            (_compiled.doc or "").split("\n")[0]
            if hasattr(_compiled, "doc") and _compiled.doc
            else _compiled.name
        ),
        "players": (_compiled.min_players, _compiled.max_players),
    }

_session_counter = itertools.count(1)


def _next_session_id() -> str:
    return f"game-{next(_session_counter)}"


# ---------------------------------------------------------------------------
# Register extra pages (analytics, tournaments)
# ---------------------------------------------------------------------------

from server.pages import agent_play as _agent_play_page
from server.pages import analytics as _analytics_page
from server.pages import strategies as _strategies_page
from server.pages import tournaments as _tournaments_page
from server.pages import workshop as _workshop_page

_analytics_page.register(GAMES)
_tournaments_page.register(GAMES)
_strategies_page.register(GAMES)
_workshop_page.register(GAMES)
_agent_play_page.register(GAMES)


# ---------------------------------------------------------------------------
# MCP HTTP endpoints — use APIRouter so NiceGUI SPA catch-all doesn't intercept
# ---------------------------------------------------------------------------

from fastapi import APIRouter
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse, Response
from starlette.responses import StreamingResponse

_api = APIRouter()


@_api.post("/mcp/agent/{agent_id}")
async def mcp_endpoint(agent_id: str, request: FastAPIRequest) -> JSONResponse:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            },
            status_code=400,
        )
    try:
        result = await mcp.handle_request(agent_id, body)
        if result is None:
            # Notification — no response per MCP spec; return 204.
            return Response(status_code=204)
        return JSONResponse(result)
    except (KeyError, ValueError, TypeError) as exc:
        log.warning("MCP request error for agent=%s: %s", agent_id, exc)
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32602, "message": str(exc)},
            }
        )
    except Exception:
        log.exception("MCP internal error for agent=%s", agent_id)
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32603, "message": "Internal server error"},
            },
            status_code=500,
        )


@_api.get("/mcp/agent/{agent_id}")
async def mcp_info(agent_id: str) -> JSONResponse:
    return JSONResponse(
        {
            "server": "parlameme",
            "version": "0.1.0",
            "agent_id": agent_id,
            "games": list(GAME_REGISTRY.keys()),
        }
    )


@_api.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "games": len(GAME_REGISTRY),
            "sessions": len(list_sessions()),
        }
    )


# ---------------------------------------------------------------------------
# MCP Streamable HTTP transport (spec 2025-03-26)
# Single endpoint: POST/GET/DELETE /mcp
# Session management via Mcp-Session-Id header
# ---------------------------------------------------------------------------

import uuid as _uuid

from mcp.agents import remove_agent as _remove_agent

# Session storage: mcp_session_id → agent_id
_mcp_sessions: dict[str, str] = {}

MCP_SESSION_STALE_TIMEOUT = 30 * 60  # 30 minutes


def _cleanup_mcp_sessions() -> int:
    """Remove stale MCP sessions (called from agent cleanup loop)."""
    import time

    now = time.time()
    from mcp.agents import get_agent

    stale = []
    for sid, agent_id in _mcp_sessions.items():
        agent = get_agent(agent_id)
        if agent is None or now - agent.last_seen > MCP_SESSION_STALE_TIMEOUT:
            stale.append(sid)
    for sid in stale:
        agent_id = _mcp_sessions.pop(sid, None)
        if agent_id:
            _remove_agent(agent_id)
    return len(stale)


def _wants_sse(request: FastAPIRequest) -> bool:
    """Check if client prefers SSE (text/event-stream) responses."""
    accept = request.headers.get("accept", "")
    return "text/event-stream" in accept


def _sse_response(data: dict | list, mcp_session_id: str | None = None) -> Response:
    """Wrap JSON-RPC result(s) in SSE format."""
    payload = json.dumps(data)

    async def generate():
        yield f"event: message\ndata: {payload}\n\n"

    headers = {}
    if mcp_session_id:
        headers["Mcp-Session-Id"] = mcp_session_id
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers=headers,
    )


async def _handle_single_mcp_message(
    body: dict, session_id: str | None, use_sse: bool = False
) -> Response:
    """Handle a single JSON-RPC message for Streamable HTTP transport."""
    method = body.get("method", "")
    is_notification = "id" not in body

    # Initialize — create new session
    if method == "initialize":
        agent_id = f"http-{_uuid.uuid4().hex[:12]}"
        new_session_id = str(_uuid.uuid4())
        _mcp_sessions[new_session_id] = agent_id
        result = await mcp.handle_request(agent_id, body)
        if use_sse:
            return _sse_response(result, new_session_id)
        resp = JSONResponse(result)
        resp.headers["Mcp-Session-Id"] = new_session_id
        return resp

    # All other requests require a valid session
    if not session_id or session_id not in _mcp_sessions:
        error_body = {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {"code": -32600, "message": "Missing or invalid session"},
        }
        if use_sse:
            return _sse_response(error_body, session_id)
        return JSONResponse(error_body, status_code=400)

    agent_id = _mcp_sessions[session_id]
    result = await mcp.handle_request(agent_id, body)

    # Notification or no response needed
    if is_notification or result is None:
        return Response(status_code=202)

    if use_sse:
        return _sse_response(result, session_id)
    resp = JSONResponse(result)
    resp.headers["Mcp-Session-Id"] = session_id
    return resp


@_api.post("/mcp")
async def mcp_streamable_http(request: FastAPIRequest) -> Response:
    """MCP Streamable HTTP endpoint (spec 2025-03-26)."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            },
            status_code=400,
        )

    session_id = request.headers.get("mcp-session-id")
    use_sse = _wants_sse(request)

    # Single message
    if isinstance(body, dict):
        return await _handle_single_mcp_message(body, session_id, use_sse)

    # Batch: array of JSON-RPC messages
    if isinstance(body, list) and body:
        responses = []
        resp_session_id = session_id

        # Process initialize first so subsequent messages use the new session
        msgs = [m for m in body if isinstance(m, dict)]
        init_msgs = [m for m in msgs if m.get("method") == "initialize"]
        other_msgs = [m for m in msgs if m.get("method") != "initialize"]
        ordered = init_msgs[:1] + other_msgs  # at most one initialize

        for msg in ordered:
            r = await _handle_single_mcp_message(msg, resp_session_id, use_sse=False)
            # Capture session ID from initialize response
            if msg.get("method") == "initialize" and "mcp-session-id" in r.headers:
                resp_session_id = r.headers["mcp-session-id"]
            # Collect non-202 responses (notifications don't produce responses)
            if r.status_code != 202:
                r_body = json.loads(r.body.decode())
                responses.append(r_body)
        if not responses:
            return Response(status_code=202)
        result = responses if len(responses) > 1 else responses[0]
        if use_sse:
            return _sse_response(result, resp_session_id)
        resp = JSONResponse(result)
        if resp_session_id:
            resp.headers["Mcp-Session-Id"] = resp_session_id
        return resp

    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid request"},
        },
        status_code=400,
    )


@_api.get("/mcp")
async def mcp_streamable_http_get(request: FastAPIRequest) -> Response:
    """Server-initiated SSE stream — not supported."""
    return Response(status_code=405)


@_api.delete("/mcp")
async def mcp_streamable_http_delete(request: FastAPIRequest) -> Response:
    """Terminate MCP session."""
    session_id = request.headers.get("mcp-session-id")
    if session_id and session_id in _mcp_sessions:
        agent_id = _mcp_sessions.pop(session_id)
        _remove_agent(agent_id)
    return Response(status_code=200)


@_api.get("/.well-known/mcp/server-card.json")
async def mcp_server_card() -> Response:
    """MCP server discovery card (emerging standard)."""
    return JSONResponse(
        {
            "version": "1.0",
            "protocolVersion": "2025-03-26",
            "serverInfo": {
                "name": "parlameme",
                "title": "Parlameme Game Engine",
                "version": "0.1.0",
            },
            "description": (
                "Multiplayer strategy games for AI agents — "
                "auction, exchange, werewolf, parliament."
            ),
            "transport": {"type": "streamable-http", "endpoint": "/mcp"},
            "capabilities": {"tools": {"dynamic": True}},
            "authentication": {"required": False},
            "games": {
                gid: {
                    "name": g.name,
                    "players": f"{g.min_players}-{g.max_players}",
                }
                for gid, g in GAME_REGISTRY.items()
            },
        }
    )


app.include_router(_api)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@ui.page("/")
def lobby_page():
    """Main lobby — create and join games."""
    with page_layout("Parlameme", back_to=None):
        # --- Available Games ---
        section_header("Available Games", icon="sports_esports")
        with ui.column().classes("w-full gap-3"):
            for game_id, info in GAMES.items():
                game_hero_card(
                    game_id,
                    info,
                    on_click=lambda gid=game_id: _create_game(gid),
                )

        ui.separator()

        # --- Active Sessions ---
        section_header("Active Sessions", icon="play_circle")

        @ui.refreshable
        def sessions_list():
            sessions = list_sessions()
            if not sessions:
                empty_state("No active games", icon="videogame_asset_off")
                return
            for sid, sess in sessions.items():
                game_icon = GAME_ICONS.get(
                    next(
                        (
                            gid
                            for gid, g in GAMES.items()
                            if g["compiled"] is sess.compiled
                        ),
                        "",
                    ),
                    "casino",
                )
                with glass_card(extra_classes="w-full"):
                    with ui.row().classes(
                        "items-center justify-between w-full flex-wrap gap-3"
                    ):
                        with ui.row().classes("items-center gap-3 flex-grow"):
                            ui.icon(game_icon).classes("text-xl text-gray-400")
                            with ui.column().classes("gap-0.5"):
                                ui.label(f"{sess.compiled.name}").classes(
                                    "font-display font-semibold"
                                )
                                ui.label(sid).classes("text-xs text-gray-400 font-mono")
                                players = ", ".join(sess.player_ids)
                                ui.label(players).classes("text-sm text-gray-500")
                        with ui.row().classes("items-center gap-2"):
                            status_chip(sess.state.phase, "secondary")
                            ui.label(f"R{sess.state.round}").classes(
                                "text-xs text-gray-400"
                            )
                    ui.separator().classes("my-2")
                    with ui.row().classes("gap-2 flex-wrap"):
                        for pid in sess.player_ids:
                            ui.button(
                                pid,
                                on_click=lambda s=sid, p=pid: ui.navigate.to(
                                    f"/play/{s}/{p}"
                                ),
                                icon="person",
                            ).props("unelevated no-caps dense color=primary").classes(
                                "rounded-lg"
                            )
                        ui.space()
                        ui.button(
                            icon="visibility",
                            on_click=lambda s=sid: ui.navigate.to(f"/spectate/{s}"),
                        ).props("flat round dense color=secondary").tooltip("Spectate")
                        ui.button(
                            icon="delete",
                            on_click=lambda s=sid: _delete_game(s),
                        ).props("flat round dense color=negative")

        sessions_list()
        ui.timer(3.0, sessions_list.refresh)

        # --- Completed Games ---
        ui.separator()
        section_header("Completed Games", icon="history")

        archives = list_archives()
        if not archives:
            empty_state("No completed games yet", icon="archive")
        else:
            for arch in archives[:20]:
                ts = datetime.datetime.fromtimestamp(arch["timestamp"])
                gid = arch["game_id"]
                icon = GAME_ICONS.get(gid, "casino")
                accent = GAME_COLORS.get(gid, "#7C3AED")
                with glass_card(extra_classes="w-full"):
                    with ui.row().classes(
                        "items-center justify-between w-full flex-wrap gap-2"
                    ):
                        with ui.row().classes("items-center gap-3 flex-grow"):
                            ui.icon(icon).classes("text-lg").style(f"color: {accent}")
                            with ui.column().classes("gap-0"):
                                ui.label(arch["session_id"]).classes(
                                    "text-sm font-semibold"
                                )
                                players = ", ".join(arch["players"][:5])
                                if len(arch["players"]) > 5:
                                    players += f" +{len(arch['players']) - 5}"
                                ui.label(players).classes("text-xs text-gray-400")
                        with ui.row().classes("items-center gap-3"):
                            ui.label(f"{arch['decisions']} decisions").classes(
                                "text-xs text-gray-400"
                            )
                            ui.label(f"{ts:%b %d, %H:%M}").classes(
                                "text-xs text-gray-300"
                            )
                            ui.button(
                                "Replay",
                                on_click=lambda a=arch: ui.navigate.to(
                                    f"/replay/{a['session_id']}"
                                ),
                                icon="replay",
                            ).props("unelevated no-caps dense").classes(
                                "rounded-lg"
                            ).style(f"background: {accent} !important")

    async def _create_game(game_id: str):
        info = GAMES[game_id]
        compiled = info["compiled"]
        sid = _next_session_id()
        min_p = info["players"][0]
        players = [f"player-{i + 1}" for i in range(min_p)]
        sess = create_session(sid, compiled, players)
        await sess.start()
        sessions_list.refresh()
        ui.notify(f"Created {info['name']}: {sid}", type="positive")

    def _delete_game(session_id: str):
        remove_session(session_id)
        sessions_list.refresh()
        ui.notify(f"Deleted {session_id}", type="warning")


@ui.page("/play/{session_id}/{player_id}")
def play_page(session_id: str, player_id: str):
    """Game play interface with push updates and visibility filtering."""
    apply_theme()
    session = get_session(session_id)

    if not session:
        apply_theme()
        ui.label(f"Session '{session_id}' not found").classes("text-xl text-red-500")
        ui.button("Back to Lobby", on_click=lambda: ui.navigate.to("/"))
        return

    if player_id not in session.state.entities:
        ui.label(f"Player '{player_id}' not in this game").classes(
            "text-xl text-red-500"
        )
        ui.button("Back to Lobby", on_click=lambda: ui.navigate.to("/"))
        return

    # --- Push update subscription ---
    def on_state_change(_new_state: GameState):
        game_view.refresh()

    session.subscribe(on_state_change)
    ui.context.client.on_disconnect(lambda: session.unsubscribe(on_state_change))

    # --- Header ---
    with ui.header().classes("items-center justify-between px-4"):
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat round dense color=dark"
            )
            ui.label(f"{session.compiled.name}").classes(
                "font-display text-lg font-semibold"
            )
            ui.label(f"/ {session_id}").classes("text-sm text-gray-400")
        ui.space()
        ui.badge(player_id).props("color=primary").classes("text-sm px-3 py-1")

    # --- Main game view ---
    _current_tab = {"value": "Actions"}  # FIX-13: preserve tab across refresh
    _prev_resources: dict[str, dict] = {}  # track for delta display

    @ui.refreshable
    def game_view():
        view = view_for(session.state, player_id, session.compiled)
        s = session.state

        # Detect pending actions for this player
        _has_pending = any(
            player_id in pd.responders and pd.responders[player_id] is None
            for pd in s.pending_deals.values()
        ) or any(
            player_id in pv.eligible and player_id not in pv.votes
            for pv in s.pending_votes.values()
        )

        # Browser tab title notification
        _title = f"(!) {session.compiled.name}" if _has_pending else session.compiled.name
        ui.run_javascript(f"document.title = {_title!r}")

        with ui.column().classes("w-full max-w-6xl mx-auto px-4 py-6 gap-4"):
            # Status bar
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                ui.badge(f"Round {view['round']}").props(
                    "color=primary rounded"
                ).classes("px-2")
                ui.badge(f"Phase: {view['phase']}").props(
                    "color=secondary rounded"
                ).classes("px-2")
                ui.badge(view["status"].upper()).props(
                    f"color={'positive' if view['status'] == 'active' else 'negative'} rounded"
                ).classes("px-2")
                if s.victory_result:
                    winner = s.victory_result.get("winner", "?")
                    cond = s.victory_result.get("condition", "?")
                    ui.badge(f"Winner: {winner} ({cond})").props(
                        "color=accent rounded"
                    ).classes("px-2")

            # Player cards (with resource deltas)
            with ui.row().classes("w-full gap-3 flex-wrap"):
                for eid, entity in view["entities"].items():
                    prev_res = _prev_resources.get(eid)
                    render_entity_card(
                        eid,
                        entity,
                        is_self=(eid == player_id),
                        compiled=session.compiled,
                        prev_resources=prev_res,
                    )

            # Update prev_resources for next refresh
            for eid, entity in view["entities"].items():
                _prev_resources[eid] = dict(entity.get("resources", {}))

            # Tabs: Actions / Chat / History / Rules
            with ui.tabs().classes("w-full").props("no-caps dense") as tabs:
                _atab = ui.tab("Actions", icon="bolt")
                if _has_pending:
                    _atab.props('alert="negative"')
                ui.tab("Chat", icon="chat")
                ui.tab("History", icon="history")
                ui.tab("Rules", icon="menu_book")
            tabs.value = _current_tab["value"]
            tabs.on_value_change(lambda e: _current_tab.update(value=e.value))

            with ui.tab_panels(tabs, value=_current_tab["value"]).classes("w-full"):
                with ui.tab_panel("Actions"):
                    render_action_panel(session, player_id, _handle_result)

                with ui.tab_panel("Chat"):
                    render_chat_panel(view, session, player_id)

                with ui.tab_panel("History"):
                    render_history(s)

                with ui.tab_panel("Rules"):
                    render_game_info(session.compiled, player_id, s)

    def _handle_result(result: dict):
        if result["ok"]:
            victory = result.get("victory")
            if victory:
                ui.notify(
                    f"Game Over! {victory['winner']} wins ({victory['condition']})",
                    type="positive",
                    timeout=10000,
                )
            else:
                parts = []
                if "outcome" in result:
                    parts.append(result["outcome"])
                if "instance_id" in result:
                    parts.append("awaiting response")
                if result.get("auto_completed"):
                    parts.append("vote completed")
                msg = " — ".join(parts) if parts else "Done"
                ui.notify(msg, type="positive", timeout=1500)
        else:
            error = result.get("error", {})
            msg = error.get("message", "Unknown error")
            code = error.get("code", "")
            _ERROR_TIPS = {
                "usage_limit": "Try a different action or advance phase",
                "insufficient_resources": "Check your resources",
                "guard_failed": "Precondition not met — see available actions",
                "deal_not_allowed": "Not available in this phase",
            }
            tip = _ERROR_TIPS.get(code)
            if tip:
                msg = f"{msg}\n{tip}"
            ui.notify(msg, type="negative", timeout=4000)

    game_view()


# Keep old URL working (redirect to first player)
@ui.page("/play/{session_id}")
def play_page_redirect(session_id: str):
    session = get_session(session_id)
    if session and session.player_ids:
        ui.navigate.to(f"/play/{session_id}/{session.player_ids[0]}")
    else:
        ui.navigate.to("/")


# ---------------------------------------------------------------------------
# Spectator page
# ---------------------------------------------------------------------------


@ui.page("/spectate/{session_id}")
def spectate_page(session_id: str):
    """Live spectator view — public-only, read-only, push updates."""
    apply_theme()
    session = get_session(session_id)

    if not session:
        ui.label(f"Session '{session_id}' not found").classes("text-xl text-red-500")
        ui.button("Back to Lobby", on_click=lambda: ui.navigate.to("/"))
        return

    # Push updates
    def on_state_change(_new_state: GameState):
        spectator_view.refresh()

    session.subscribe(on_state_change)
    ui.context.client.on_disconnect(lambda: session.unsubscribe(on_state_change))

    # Header
    with ui.header().classes("items-center justify-between px-4"):
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat round dense color=dark"
            )
            ui.icon("visibility").classes("text-lg text-gray-400")
            ui.label(f"Spectating: {session.compiled.name}").classes(
                "font-display text-lg font-semibold"
            )
            ui.label(f"/ {session_id}").classes("text-sm text-gray-400")

    _prev_resources: dict[str, dict] = {}

    @ui.refreshable
    def spectator_view():
        view = view_for(session.state, SPECTATOR_ID, session.compiled)
        s = session.state

        with ui.column().classes("w-full max-w-6xl mx-auto px-4 py-6 gap-4"):
            # Status bar
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                ui.badge(f"Round {view['round']}").props(
                    "color=primary rounded"
                ).classes("px-2")
                ui.badge(f"Phase: {view['phase']}").props(
                    "color=secondary rounded"
                ).classes("px-2")
                active_count = sum(1 for e in view["entities"].values() if e["active"])
                ui.badge(f"{active_count}/{len(view['entities'])} alive").props(
                    "color=positive rounded"
                ).classes("px-2")
                if s.victory_result:
                    winner = s.victory_result.get("winner", "?")
                    cond = s.victory_result.get("condition", "?")
                    ui.badge(f"Winner: {winner} ({cond})").props(
                        "color=accent rounded"
                    ).classes("px-2")

            # Player cards
            with ui.row().classes("w-full gap-3 flex-wrap"):
                for eid, entity in view["entities"].items():
                    prev_res = _prev_resources.get(eid)
                    render_entity_card(
                        eid,
                        entity,
                        is_self=False,
                        compiled=session.compiled,
                        prev_resources=prev_res,
                    )

            for eid, entity in view["entities"].items():
                _prev_resources[eid] = dict(entity.get("resources", {}))

            # Tabs: Chat / History / Rules
            with ui.tabs().classes("w-full").props("no-caps dense") as tabs:
                ui.tab("Chat", icon="chat")
                ui.tab("History", icon="history")
                ui.tab("Rules", icon="menu_book")

            with ui.tab_panels(tabs, value="Chat").classes("w-full"):
                with ui.tab_panel("Chat"):
                    # Spectator sees public messages only, no send
                    messages = view.get("messages", [])
                    with (
                        ui.scroll_area()
                        .classes("w-full h-56 rounded-xl p-3")
                        .style("background: #F8F8FA; border: 1px solid #E5E4EA")
                    ):
                        if not messages:
                            with ui.column().classes("items-center gap-2 py-8 w-full"):
                                ui.icon("chat_bubble_outline").classes(
                                    "text-2xl text-gray-300"
                                )
                                ui.label("No messages yet").classes(
                                    "text-sm text-gray-400"
                                )
                        for msg in messages:
                            ui.chat_message(
                                text=msg["content"],
                                name=msg["sender"],
                                sent=False,
                                stamp=f"R{msg['round']} {msg['phase']}",
                            )

                with ui.tab_panel("History"):
                    render_history(s)

                with ui.tab_panel("Rules"):
                    render_game_info(session.compiled)

    spectator_view()


# ---------------------------------------------------------------------------
# Replay page
# ---------------------------------------------------------------------------


@ui.page("/replay/{archive_id}")
def replay_page(archive_id: str):
    """Step-through replay of a completed game."""
    apply_theme()

    from server.sessions import ARCHIVE_DIR

    archive_path = ARCHIVE_DIR / f"{archive_id}.json"
    if not archive_path.exists():
        ui.label(f"Archive '{archive_id}' not found").classes("text-xl text-red-500")
        ui.button("Back to Lobby", on_click=lambda: ui.navigate.to("/"))
        return

    archive = load_archive(archive_path)
    compiled = GAME_REGISTRY.get(archive.game_id)
    if not compiled:
        ui.label(f"Unknown game type: {archive.game_id}").classes(
            "text-xl text-red-500"
        )
        ui.button("Back to Lobby", on_click=lambda: ui.navigate.to("/"))
        return

    ctrl = ReplayController(archive, compiled)

    # --- Header ---
    with ui.header().classes("items-center justify-between px-4"):
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat round dense color=dark"
            )
            ui.label(f"Replay: {compiled.name}").classes(
                "font-display text-lg font-semibold"
            )
            ui.label(f"/ {archive_id}").classes("text-sm text-gray-400")
        ui.space()
        ui.label(f"{', '.join(archive.players)}").classes("text-sm text-gray-400")

    def _go(step: int):
        ctrl.go_to(step)
        transport.refresh()
        decision_info.refresh()
        state_view.refresh()
        diff_view.refresh()

    def _set_observer(obs: str):
        observer["id"] = obs
        state_view.refresh()

    with ui.column().classes("w-full max-w-6xl mx-auto px-4 py-6 gap-4"):
        # --- Transport controls ---
        @ui.refreshable
        def transport():
            render_replay_transport(ctrl, _go)

        transport()

        # --- Decision info ---
        @ui.refreshable
        def decision_info():
            d = ctrl.decision
            if d:
                with glass_card(extra_classes="w-full"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("description").classes("text-sm text-gray-400")
                        ui.label("Decision").classes("font-semibold text-sm")
                    parts = [f"{k}: {v}" for k, v in d.items()]
                    ui.label(" | ".join(parts)).classes(
                        "text-xs font-mono text-gray-500"
                    )
            else:
                ui.label("Initial state (after setup)").classes("text-sm text-gray-400")

        decision_info()

        # --- View as selector ---
        observer_options = ["omniscient"] + list(archive.players)
        observer = {"id": "omniscient"}

        with ui.row().classes("items-center gap-2"):
            ui.label("View as:").classes("text-sm text-gray-500")
            ui.toggle(
                observer_options,
                value="omniscient",
                on_change=lambda e: _set_observer(e.value),
            ).props("dense no-caps rounded unelevated")

        # --- State + Diff split ---
        with ui.splitter(value=55).classes("w-full") as splitter:
            with splitter.before:

                @ui.refreshable
                def state_view():
                    s = ctrl.current
                    obs = observer["id"]

                    with ui.column().classes("w-full gap-2"):
                        with ui.row().classes("items-center gap-2"):
                            ui.badge(f"Round {s.round}").props(
                                "color=primary rounded"
                            ).classes("px-2")
                            ui.badge(f"Phase: {s.phase}").props(
                                "color=secondary rounded"
                            ).classes("px-2")
                            ui.badge(s.status.upper()).props(
                                f"color={'positive' if s.status == 'active' else 'negative'} rounded"
                            ).classes("px-2")

                        if obs == "omniscient":
                            for eid, entity in s.entities.items():
                                render_entity_card(
                                    eid,
                                    {
                                        "active": entity.active,
                                        "resources": dict(entity.resources),
                                        "attrs": dict(entity.attrs_),
                                        "groups": sorted(entity.groups),
                                    },
                                    is_self=False,
                                    compiled=compiled,
                                )
                        else:
                            view = view_for(s, obs, compiled)
                            for eid, entity in view["entities"].items():
                                render_entity_card(
                                    eid,
                                    entity,
                                    is_self=(eid == obs),
                                    compiled=compiled,
                                )

                state_view()

            with splitter.after:

                @ui.refreshable
                def diff_view():
                    changes = ctrl.diff()
                    if not changes:
                        ui.label("No changes (initial state)").classes(
                            "text-sm text-gray-400"
                        )
                        return

                    ui.label("Changes").classes("font-semibold text-sm")
                    for ch in changes:
                        render_change(ch)

                diff_view()

        # --- Event log ---
        ui.separator()
        section_header("All Decisions", icon="list")

        with ui.scroll_area().classes("w-full h-48"):
            for i, d in enumerate(archive.decisions):
                is_current = i == ctrl.step - 1
                cls = "font-bold text-primary" if is_current else "text-gray-400"
                dtype = d.get("type", "?")
                detail = " | ".join(f"{k}={v}" for k, v in d.items() if k != "type")
                with ui.row().classes("items-center gap-2 cursor-pointer"):
                    ui.label(f"{i + 1}.").classes(f"text-xs w-6 {cls}")
                    ui.badge(dtype).props("outline dense rounded")
                    label = ui.label(detail).classes(f"text-xs {cls}")
                    label.on("click", lambda e, step=i + 1: _go(step))


# ---------------------------------------------------------------------------
# Startup / shutdown hooks
# ---------------------------------------------------------------------------


@app.on_startup
async def _on_startup():
    global _ledger

    # PostgreSQL sync (fail-open — disabled if PG_DSN not set)
    import os

    from engine.pg import pg_sync

    pg_sync.connect(os.environ.get("PG_DSN"))

    # Warn loudly if token secret is not configured
    if not os.environ.get("GAME_TOKEN_SECRET"):
        log.warning(
            "GAME_TOKEN_SECRET not set — tokens will NOT survive server restarts. "
            "Set this env var before production deployment."
        )

    _ledger = FileLedger(Path("data/ledger.json"))
    mcp._ledger = _ledger
    set_ledger(_ledger)
    log.info("Ledger loaded: %d entries", len(_ledger))

    store = get_persistence()
    recovery = store.load()
    stale = recovery.get("stale", {})
    active = recovery.get("active", {})
    if stale:
        log.warning("Found %d stale sessions (>1h inactive), clearing", len(stale))
        for sid in stale:
            store.remove(sid)
    if active:
        n = recover_sessions(active, GAME_REGISTRY)
        log.info("Recovered %d/%d active sessions from previous run", n, len(active))
    # Tournament persistence
    from tournament.sessions import load_tournaments

    t_count = load_tournaments()
    if t_count:
        log.info("Recovered %d tournaments from previous run", t_count)

    # Background task: clean up stale agents every 5 minutes
    from mcp.agents import cleanup_stale

    async def _agent_cleanup_loop():
        while True:
            await asyncio.sleep(300)  # 5 minutes
            removed = cleanup_stale()
            removed_sessions = _cleanup_mcp_sessions()
            if removed or removed_sessions:
                log.info(
                    "Cleaned up %d stale agents, %d stale MCP sessions",
                    removed,
                    removed_sessions,
                )

    asyncio.ensure_future(_agent_cleanup_loop())

    log.info(
        "Parlameme started — %d games registered, port 8080",
        len(GAME_REGISTRY),
    )


@app.on_shutdown
async def _on_shutdown():
    """Graceful shutdown — archive any in-progress games, persist state."""
    sessions = list_sessions()
    for sid, sess in sessions.items():
        if sess.state.status == "ended" and sess.archive is None:
            sess._maybe_archive()
            log.info("Archived on shutdown: %s", sid)
    get_persistence().flush()

    # Tournament persistence
    from tournament.sessions import flush_tournaments

    flush_tournaments()

    log.info("Parlameme shutdown complete — %d sessions active", len(sessions))


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def main():
    import os

    port = int(os.environ.get("PORT", "8080"))
    log.info("Starting Parlameme on port %d", port)
    ui.run(title="Parlameme", port=port, reload=False, storage_secret="parlameme-dev")
