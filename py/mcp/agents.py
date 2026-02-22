"""Agent state machine and registry for MCP server."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class TournamentContext:
    """Preserved tournament info while agent plays a tournament match."""

    tournament_id: str
    match_id: str


@dataclass
class AgentState:
    """Tracks an agent's current state in the platform."""

    agent_id: str
    state: Literal["lobby", "in_game", "in_tournament", "spectating"] = "lobby"
    session_id: str | None = None
    player_id: str | None = None
    game_type: str | None = None
    tournament_id: str | None = None
    tournament_context: TournamentContext | None = None
    last_seen: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.last_seen = time.time()

    def to_lobby(self) -> None:
        self.state = "lobby"
        self.session_id = None
        self.player_id = None
        self.game_type = None
        self.tournament_id = None
        self.tournament_context = None

    def to_game(self, session_id: str, player_id: str, game_type: str) -> None:
        self.state = "in_game"
        self.session_id = session_id
        self.player_id = player_id
        self.game_type = game_type

    def to_spectating(self, session_id: str) -> None:
        self.state = "spectating"
        self.session_id = session_id
        self.player_id = None
        self.game_type = None
        self.tournament_id = None
        self.tournament_context = None

    def to_tournament(self, tournament_id: str) -> None:
        self.state = "in_tournament"
        self.tournament_id = tournament_id

    def to_game_from_tournament(
        self, session_id: str, player_id: str, game_type: str, match_id: str
    ) -> None:
        self.tournament_context = TournamentContext(
            tournament_id=self.tournament_id or "",
            match_id=match_id,
        )
        self.to_game(session_id, player_id, game_type)

    def back_to_tournament(self) -> None:
        ctx = self.tournament_context
        if ctx:
            self.state = "in_tournament"
            self.tournament_id = ctx.tournament_id
            self.session_id = None
            self.player_id = None
            self.game_type = None
            self.tournament_context = None
        else:
            self.to_lobby()


# ---------------------------------------------------------------------------
# Global agent registry
# ---------------------------------------------------------------------------

_agents: dict[str, AgentState] = {}

STALE_TIMEOUT = 30 * 60  # 30 minutes


def register_agent(agent_id: str) -> AgentState:
    if agent_id not in _agents:
        _agents[agent_id] = AgentState(agent_id=agent_id)
    agent = _agents[agent_id]
    agent.touch()
    return agent


def get_agent(agent_id: str) -> AgentState | None:
    agent = _agents.get(agent_id)
    if agent:
        agent.touch()
    return agent


def remove_agent(agent_id: str) -> None:
    _agents.pop(agent_id, None)


def list_agents() -> dict[str, AgentState]:
    return dict(_agents)


def cleanup_stale() -> int:
    now = time.time()
    stale = [aid for aid, a in _agents.items() if now - a.last_seen > STALE_TIMEOUT]
    for aid in stale:
        del _agents[aid]
    return len(stale)


def reset_all() -> None:
    _agents.clear()
