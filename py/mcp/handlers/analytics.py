"""Analytics MCP tools — stats, leaderboard, head-to-head, game balance.

Wraps existing ``server.analytics`` cache with MCP-friendly formatting.
Available in ALL agent states (global tools).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.schema import Tool
from server.analytics import (
    game_type_stats,
    head_to_head,
    platform_stats,
    player_stats,
)

if TYPE_CHECKING:
    from mcp.agents import AgentState

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="my_stats",
        description="Your stats: games, wins, rating, tier, streak, recent form",
        _meta={"type": "global"},
    ),
    Tool(
        name="platform_stats",
        description="Platform overview: total games, players, game types",
        _meta={"type": "global"},
    ),
    Tool(
        name="player_head_to_head",
        description="Your record vs a specific player",
        inputSchema={
            "type": "object",
            "properties": {
                "opponent": {"type": "string", "description": "Opponent player ID"},
            },
            "required": ["opponent"],
        },
        _meta={"type": "global"},
    ),
    Tool(
        name="game_balance_report",
        description="Game type analytics: games played, avg rounds, decision patterns",
        inputSchema={
            "type": "object",
            "properties": {
                "game_type": {"type": "string", "description": "Game type ID"},
            },
            "required": ["game_type"],
        },
        _meta={"type": "global"},
    ),
    Tool(
        name="opponent_profile",
        description=(
            "Analyze opponent play patterns: deals, votes, responses, "
            "win rate from past games"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "player_id": {
                    "type": "string",
                    "description": "Opponent player ID to analyze",
                },
            },
            "required": ["player_id"],
        },
        _meta={"type": "global"},
    ),
    Tool(
        name="leaderboard",
        description="Top players by Glicko-2 rating with tiers",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Number of players",
                },
            },
        },
        _meta={"type": "global"},
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_my_stats(server: Any, agent: AgentState, args: dict) -> dict:
    stats = player_stats(agent.agent_id)
    if not stats:
        return _content("No stats yet. Play some games first!")

    s = stats[0]
    streak = s.get("streak", {})
    streak_str = (
        f"{streak.get('type', '-')}{streak.get('count', 0)}"
        if streak.get("type")
        else "-"
    )
    form = "".join(s.get("recent_form", [])) or "-"

    lines = [
        f"## Your Stats ({agent.agent_id})",
        f"Rating: {s['rating']} ({s['tier']}) | RD: {s['rd']}",
        f"Games: {s['games']} | Wins: {s['wins']} | Win rate: {s['win_rate']}%",
        f"Streak: {streak_str} | Best win streak: {s.get('best_streak', 0)}",
        f"Recent form: {form}",
    ]
    if s.get("game_types"):
        gt = ", ".join(f"{k}: {v}" for k, v in s["game_types"].items())
        lines.append(f"Games by type: {gt}")
    return _content("\n".join(lines))


async def handle_platform_stats(server: Any, agent: AgentState, args: dict) -> dict:
    p = platform_stats()
    lines = [
        "## Platform Stats",
        f"Total games: {p['total_games']}",
        f"Unique players: {p['unique_players']}",
        f"Total decisions: {p['total_decisions']}",
        f"Avg decisions/game: {p['avg_decisions_per_game']}",
    ]
    if p.get("games_by_type"):
        lines.append("Games by type:")
        for gid, count in p["games_by_type"].items():
            lines.append(f"  {gid}: {count}")
    return _content("\n".join(lines))


async def handle_head_to_head(server: Any, agent: AgentState, args: dict) -> dict:
    opponent = args.get("opponent", "")
    if not opponent:
        return _error("Provide an opponent player ID.")

    h2h = head_to_head(agent.agent_id, opponent)
    if h2h["total_games"] == 0:
        return _content(f"No games played against {opponent}.")

    lines = [
        f"## Head to Head: {agent.agent_id} vs {opponent}",
        f"Games: {h2h['total_games']}",
        f"{agent.agent_id}: {h2h['a_wins']} wins (rating {h2h['a_rating']})",
        f"{opponent}: {h2h['b_wins']} wins (rating {h2h['b_rating']})",
        f"Draws: {h2h['draws']}",
    ]
    return _content("\n".join(lines))


async def handle_game_balance(server: Any, agent: AgentState, args: dict) -> dict:
    game_type = args.get("game_type", "")
    if not game_type:
        return _error("Provide a game_type.")

    all_stats = game_type_stats()
    match = next((s for s in all_stats if s["game_id"] == game_type), None)
    if not match:
        return _content(f"No data for game type '{game_type}'.")

    lines = [
        f"## Game Balance: {game_type}",
        f"Games played: {match['games_played']}",
        f"Unique players: {match['unique_players']}",
        f"Avg decisions/game: {match['avg_decisions']}",
        f"Avg rounds/game: {match['avg_rounds']}",
    ]
    if match.get("decision_breakdown"):
        lines.append("Decision types:")
        for dtype, count in match["decision_breakdown"].items():
            lines.append(f"  {dtype}: {count}")
    return _content("\n".join(lines))


async def handle_leaderboard(server: Any, agent: AgentState, args: dict) -> dict:
    limit = args.get("limit", 10)
    stats = player_stats()[:limit]
    if not stats:
        return _content("No players ranked yet.")

    lines = ["## Leaderboard"]
    for i, s in enumerate(stats, 1):
        streak = s.get("streak", {})
        streak_str = (
            f"{streak.get('type', '-')}{streak.get('count', 0)}"
            if streak.get("type")
            else ""
        )
        lines.append(
            f"{i}. **{s['player_id']}** — {s['rating']} ({s['tier']}) "
            f"| {s['games']}G {s['wins']}W {s['win_rate']}% {streak_str}"
        )
    return _content("\n".join(lines))


async def handle_opponent_profile(server: Any, agent: AgentState, args: dict) -> dict:
    """Analyze an opponent's decision patterns from archived games."""
    target = args.get("player_id", "")
    if not target:
        return _error("Provide a player_id.")
    if target == agent.agent_id:
        return _error("Use my_stats for your own profile.")

    from server.sessions import list_archives

    all_archives = list_archives()

    # Filter shared games (both players participated)
    shared = [
        a
        for a in all_archives
        if target in a.get("players", [])
        and agent.agent_id in a.get("players", [])
    ]
    if not shared:
        target_games = [
            a for a in all_archives if target in a.get("players", [])
        ]
        if not target_games:
            return _content(f"No games found for player '{target}'.")
        shared = target_games[-20:]
        context = f"(no shared games; analyzing {len(shared)} of their games)"
    else:
        shared = shared[-20:]
        context = f"({len(shared)} shared games)"

    profile = _build_opponent_profile(shared, target, agent.agent_id)
    lines = [
        f"## Opponent Profile: {target} {context}",
        f"Games analyzed: {profile['games']}",
        f"Win rate: {profile['win_rate']}%",
        f"Win rate vs you: {profile['win_rate_vs']}%",
    ]
    if profile["deal_freq"]:
        lines.append("\n### Deal Patterns")
        for deal_id, count in sorted(
            profile["deal_freq"].items(), key=lambda x: -x[1]
        )[:10]:
            lines.append(f"  {deal_id}: {count}x")
    if profile["response_freq"]:
        lines.append("\n### Response Patterns")
        for resp, count in sorted(
            profile["response_freq"].items(), key=lambda x: -x[1]
        ):
            lines.append(f"  {resp}: {count}x")
    if profile["vote_freq"]:
        lines.append("\n### Vote Patterns")
        for info, count in sorted(
            profile["vote_freq"].items(), key=lambda x: -x[1]
        )[:10]:
            lines.append(f"  {info}: {count}x")
    lines.append(f"\nAvg decisions/game: {profile['avg_decisions']}")
    return _content("\n".join(lines))


def _build_opponent_profile(
    archives: list[dict], target: str, me: str
) -> dict:
    """Extract decision patterns from archives."""
    import json
    from pathlib import Path

    games = len(archives)
    wins, wins_vs = 0, 0
    games_with_me = 0
    deal_freq: dict[str, int] = {}
    response_freq: dict[str, int] = {}
    vote_freq: dict[str, int] = {}
    total_decisions = 0

    for a in archives:
        winner = a.get("metadata", {}).get("winner")
        if winner == target:
            wins += 1
        if me in a.get("players", []):
            games_with_me += 1
            if winner == target:
                wins_vs += 1

        path = Path("data/archives") / f"{a['session_id']}.json"
        if not path.exists():
            continue
        try:
            full = json.loads(path.read_text())
        except Exception:
            continue

        for d in full.get("decisions", []):
            # Handle respond decisions (responder is the target)
            if d.get("type") == "respond" and d.get("responder") == target:
                resp = d.get("response", "?")
                response_freq[resp] = response_freq.get(resp, 0) + 1
                total_decisions += 1
                continue
            # Check if target is the actor
            actor = (
                d.get("proposer")
                or d.get("actor")
                or d.get("voter")
                or d.get("sender")
                or ""
            )
            if actor != target:
                continue
            total_decisions += 1
            dtype = d.get("type", "")
            if dtype == "deal":
                did = d.get("deal", "?")
                deal_freq[did] = deal_freq.get(did, 0) + 1
            elif dtype == "vote":
                option = d.get("option", "?")
                vote_freq[option] = vote_freq.get(option, 0) + 1

    return {
        "games": games,
        "win_rate": round(wins / games * 100, 1) if games else 0,
        "win_rate_vs": (
            round(wins_vs / games_with_me * 100, 1) if games_with_me else 0
        ),
        "deal_freq": deal_freq,
        "response_freq": response_freq,
        "vote_freq": vote_freq,
        "avg_decisions": round(total_decisions / games, 1) if games else 0,
    }


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------

HANDLERS: dict[str, Any] = {
    "my_stats": handle_my_stats,
    "platform_stats": handle_platform_stats,
    "player_head_to_head": handle_head_to_head,
    "game_balance_report": handle_game_balance,
    "leaderboard": handle_leaderboard,
    "opponent_profile": handle_opponent_profile,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True}
