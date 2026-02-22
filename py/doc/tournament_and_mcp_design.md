# Python Engine: Tournament & MCP Design

## Design Philosophy

The Clojure engine has 2500+ lines of tournament code and 3000+ lines of MCP code.
We can achieve the same expressiveness with ~40% the code by leveraging Python's
strengths: async/await, dataclasses, type hints, and the existing engine patterns.

**Key insight**: The Clojure MCP and tournament systems are already cleanly separated
from the game engine. We don't need to replicate their internal complexity — we need
to replicate their **external behavior** with Pythonic idioms.

---

## Part 1: MCP Server

### 1.1 Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                    MCP Transport                         │
│              (stdio / SSE / streamable-http)             │
└──────────────────┬───────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────┐
│                  Stateful MCP Server                     │
│  ┌──────────┐  ┌──────────┐  ┌─────────────────┐        │
│  │  Agent   │  │  Tool    │  │  Schema          │        │
│  │  State   │  │  Router  │  │  Generator       │        │
│  │  Machine │  │          │  │  (from compiled)  │        │
│  └──────┬───┘  └────┬─────┘  └────────┬─────────┘        │
│         └───────────┼─────────────────┘                  │
│                     ▼                                    │
│  ┌──────────────────────────────────────────────────────┐│
│  │              GameSession (existing)                   ││
│  │  runtime.start_deal / cast_vote / advance_phase      ││
│  └──────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

### 1.2 Agent State Machine

Same 3 states as Clojure, but simpler — no separate MCP state module:

```python
@dataclass
class AgentState:
    agent_id: str
    state: Literal["lobby", "in_game", "in_tournament"] = "lobby"
    session_id: str | None = None
    player_id: str | None = None
    game_type: str | None = None
    tournament_id: str | None = None
    tournament_context: dict | None = None  # preserved during matches
    last_seen: float = field(default_factory=time.time)
```

Transitions:
```
LOBBY ──(join_game)──► IN_GAME ──(leave_game)──► LOBBY
  │                       │
  │                       │ (game ends)
  │                       ▼
  │                     LOBBY
  │
  ├──(register_tournament)──► IN_TOURNAMENT
  │                              │
  │                              ├──(match starts)──► IN_GAME
  │                              │                      │
  │                              │   (match ends)───────┘
  │                              │
  │                              ├──(tournament ends)──► LOBBY
  │                              └──(leave)──► LOBBY
```

### 1.3 Dynamic Tool Generation from CompiledGame

The critical innovation: **tools are generated from the compiled game definition**.
No per-game tool code needed.

```python
def generate_tools(compiled: CompiledGame, session_id: str) -> list[Tool]:
    tools = []
    
    # Deal tools
    for deal_id, deal in compiled.deals.items():
        schema = deal_to_schema(compiled.id, deal_id, deal)
        tools.append(Tool(
            name=f"{compiled.id}/{deal_id}",
            description=deal.doc or f"Execute {deal_id}",
            inputSchema=schema,
            _meta={"type": "deal", "deal_id": deal_id},
        ))
    
    # Vote tools
    for vote_id, vote in compiled.votes.items():
        tools.append(Tool(
            name=f"{compiled.id}/vote_{vote_id}",
            description=f"Cast vote in {vote_id}",
            inputSchema=vote_to_schema(compiled.id, vote_id, vote),
            _meta={"type": "vote", "vote_id": vote_id},
        ))
    
    # Channel tools
    for ch_id, channel in compiled.channels.items():
        tools.append(Tool(
            name=f"{compiled.id}/send_{ch_id}",
            description=f"Send message to {ch_id}: {channel.description}",
            inputSchema=channel_to_schema(ch_id, channel),
            _meta={"type": "channel", "channel_id": ch_id},
        ))
    
    # Universal tools (always available in-game)
    tools.extend([
        Tool(name="get_status", description="Get current game state"),
        Tool(name="get_history", description="Get recent events"),
        Tool(name="available_actions", description="What can I do now?"),
        Tool(name="advance_phase", description="Advance to next phase"),
        Tool(name="respond", description="Respond to pending deal",
             inputSchema=respond_schema()),
        Tool(name="leave_game", description="Leave current game"),
    ])
    
    return tools
```

### 1.4 Tool Filtering by Phase

Only show tools the agent can actually use right now:

```python
def filter_tools(tools: list[Tool], state: GameState, 
                 compiled: CompiledGame, player_id: str) -> list[Tool]:
    phase_def = get_current_phase(state, compiled)
    allowed_deals = set(phase_def.allows) if phase_def else set()
    
    filtered = []
    for tool in tools:
        meta = tool._meta or {}
        match meta.get("type"):
            case "deal":
                if meta["deal_id"] in allowed_deals:
                    filtered.append(tool)
            case "vote":
                if meta["vote_id"] in allowed_deals:
                    filtered.append(tool)
            case "channel":
                # Check phase_filter on channel
                ch = compiled.channels.get(meta["channel_id"])
                if not ch or not ch.phase_filter or state.phase in ch.phase_filter:
                    filtered.append(tool)
            case _:
                filtered.append(tool)  # universal tools always shown
    
    # Add respond tool only if there are pending deals for this player
    # (already handled by universal tools)
    
    return filtered
```

### 1.5 Party Classification (Generic)

Reuse the Clojure pattern — classify any custom party structure into canonical MCP params:

```python
def classify_parties(parties: dict[str, PartyDef]) -> PartyClassification:
    """Classify deal parties into canonical MCP structure.
    
    Returns mapping from custom names to canonical: 
      proposer, responder/responders, target
    """
    if len(parties) == 1:
        key = next(iter(parties))
        return PartyClassification(
            type="immediate",
            initiator=key,
            mapping={key: "actor"},
        )
    
    # Find initiator (no count, first by convention or 'proposer'/'actor'/'leader')
    # Find respondent (has count → multilateral, else bilateral)
    initiator = None
    respondent = None
    target = None
    
    for key, party in parties.items():
        if key in ("target",):
            target = key
        elif hasattr(party, 'count') and party.count:
            respondent = key  # multilateral
        elif initiator is None:
            initiator = key
        else:
            respondent = key
    
    is_multi = respondent and hasattr(parties.get(respondent, None), 'count')
    
    mapping = {}
    if initiator:
        mapping[initiator] = "actor"
    if respondent:
        mapping[respondent] = "responders" if is_multi else "responder"
    if target:
        mapping[target] = "target"
    
    return PartyClassification(
        type="multilateral" if is_multi else "bilateral" if respondent else "immediate",
        initiator=initiator,
        respondent=respondent,
        target=target,
        mapping=mapping,
    )
```

### 1.6 MCP Server Implementation

Use the official `mcp` Python SDK:

```python
from mcp.server import Server
from mcp.types import Tool, TextContent

class ParlamemeMCP:
    def __init__(self):
        self.server = Server("parlameme")
        self.agents: dict[str, AgentState] = {}
        self.games: dict[str, CompiledGame] = {}  # loaded games
        
        # Register handlers
        self.server.list_tools()(self._list_tools)
        self.server.call_tool()(self._call_tool)
    
    async def _list_tools(self) -> list[Tool]:
        agent = self._current_agent()
        match agent.state:
            case "lobby":
                return self._lobby_tools()
            case "in_game":
                session = get_session(agent.session_id)
                return filter_tools(
                    generate_tools(session.compiled, agent.session_id),
                    session.state, session.compiled, agent.player_id)
            case "in_tournament":
                return self._tournament_tools()
    
    async def _call_tool(self, name: str, arguments: dict) -> list[TextContent]:
        agent = self._current_agent()
        
        if "/" in name:
            # Game-specific tool
            return await self._handle_game_tool(agent, name, arguments)
        
        # Platform tool
        handler = self._platform_handlers.get(name)
        if handler:
            return await handler(agent, arguments)
        
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
```

### 1.7 Tool Execution Pipeline

```python
async def _handle_game_tool(self, agent: AgentState, 
                             name: str, args: dict) -> list[TextContent]:
    session = get_session(agent.session_id)
    if not session:
        return error("Not in a game session")
    
    game_id, tool_id = name.split("/", 1)
    compiled = session.compiled
    
    # Determine tool type from generated schema
    if tool_id in compiled.deals:
        return await self._execute_deal(session, agent, tool_id, args)
    elif tool_id.startswith("vote_"):
        vote_id = tool_id[5:]
        return await self._execute_vote(session, agent, vote_id, args)
    elif tool_id.startswith("send_"):
        channel_id = tool_id[5:]
        return await self._send_message(session, agent, channel_id, args)
    
    return error(f"Unknown game tool: {tool_id}")

async def _execute_deal(self, session: GameSession, agent: AgentState,
                         deal_id: str, args: dict) -> list[TextContent]:
    # Coerce arguments
    coerced = coerce_args(args, session.compiled.deals[deal_id])
    
    # Execute via session (thread-safe)
    result = await session.execute_deal(
        deal_id,
        actor_id=agent.player_id,
        target_id=coerced.get("target"),
        responder_id=coerced.get("responder"),
        params=coerced.get("params", {}),
    )
    
    if result["ok"]:
        # Generate status summary
        view = session.state.view_for(agent.player_id, session.compiled)
        return [TextContent(type="text", text=format_deal_result(result, view))]
    else:
        return error(result["error"]["message"])
```

### 1.8 Lobby Tools

```python
def _lobby_tools(self) -> list[Tool]:
    return [
        Tool(name="list_games", description="List available game types"),
        Tool(name="list_sessions", description="List open game sessions"),
        Tool(name="create_game", description="Create a new game session",
             inputSchema={
                 "type": "object",
                 "properties": {
                     "game_type": {"type": "string", "enum": list(self.games.keys())},
                     "session_id": {"type": "string"},
                 },
                 "required": ["game_type"],
             }),
        Tool(name="join_game", description="Join an existing game session",
             inputSchema={
                 "type": "object",
                 "properties": {
                     "session_id": {"type": "string"},
                 },
                 "required": ["session_id"],
             }),
        Tool(name="activate_game", description="Join game with invite token",
             inputSchema={
                 "type": "object",
                 "properties": {"token": {"type": "string"}},
                 "required": ["token"],
             }),
        # Tournament tools
        Tool(name="list_tournaments", description="List tournaments"),
        Tool(name="create_tournament", description="Create a tournament"),
        Tool(name="register_tournament", description="Register for tournament"),
        Tool(name="start_tournament", description="Start tournament (host only)"),
    ]
```

### 1.9 View Generation for MCP

Already exists: `GameState.view_for(observer_id, compiled)`.

For MCP, wrap with formatting:

```python
def format_status(view: dict, compiled: CompiledGame, player_id: str) -> str:
    """Format game state for AI agent consumption."""
    lines = []
    lines.append(f"## Game: {compiled.name}")
    lines.append(f"Phase: {view['phase']} | Round: {view['round']} | Status: {view['status']}")
    lines.append("")
    
    # Your entity
    me = view["entities"].get(player_id)
    if me:
        lines.append(f"### You ({player_id})")
        lines.append(f"Status: {'ACTIVE' if me['active'] else 'ELIMINATED'}")
        for res, val in me["resources"].items():
            lines.append(f"  {res}: {val}")
        for attr, val in me["attrs"].items():
            lines.append(f"  {attr}: {val}")
    
    # Other players
    lines.append("\n### Other Players")
    for eid, entity in view["entities"].items():
        if eid == player_id:
            continue
        status = "active" if entity["active"] else "eliminated"
        res_str = ", ".join(f"{k}={v}" for k, v in entity["resources"].items())
        lines.append(f"  {eid} ({status}): {res_str}")
    
    return "\n".join(lines)
```

### 1.10 File Structure

```
py/
├── mcp/
│   ├── __init__.py
│   ├── server.py          # ParlamemeMCP — main server class
│   ├── schema.py          # Tool schema generation from CompiledGame
│   ├── agents.py          # AgentState, state machine, registry
│   ├── tokens.py          # HMAC-SHA256 invite tokens
│   └── formatters.py      # Status/result formatting for AI
```

**Estimated LOC**: ~800 (vs Clojure's ~3000)

---

## Part 2: Tournament System

### 2.1 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Tournament Layer                        │
│                                                             │
│  ┌──────────┐  ┌────────────┐  ┌──────────────────┐        │
│  │ Tournament│  │ Tournament │  │   Match          │        │
│  │ DSL      │  │ Runtime    │  │   Manager         │        │
│  │          │  │ (state     │  │  (spawn/collect)  │        │
│  │          │  │  machine)  │  │                   │        │
│  └──────┬───┘  └─────┬──────┘  └────────┬──────────┘        │
│         │            │                  │                   │
│         ▼            ▼                  ▼                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              GameSession (existing)                   │   │
│  │     Each match = separate GameSession                │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Tournament State (Immutable)

```python
@attrs.define(frozen=True)
class TournamentState:
    tournament_id: str
    tournament_type: str  # "round_robin" | "single_elimination" | "double_elimination" | "swiss"
    status: str = "registration"  # registration | in_progress | completed | cancelled
    host: str = ""
    name: str = ""
    
    # Configuration
    game_type: str = ""           # which game to play (duel, mafia, etc.)
    min_participants: int = 2
    max_participants: int = 16
    rounds: int | None = None     # for swiss
    
    # Participants
    participants: tuple[str, ...] = ()
    
    # Matches
    matches: dict[str, Match] = attrs.Factory(dict)  # match_id → Match
    
    # Standings
    standings: dict[str, Standing] = attrs.Factory(dict)  # participant → Standing
    
    # Bracket (for elimination)
    bracket: dict | None = None
    
    # Result
    winner: str | None = None
    seed: int = 42


@attrs.define(frozen=True)
class Match:
    id: str
    participants: tuple[str, ...]
    round: int = 1
    stage: str = "main"           # "winners_bracket" | "losers_bracket" | "grand_final"
    status: str = "pending"       # pending | active | completed
    winner: str | None = None
    scores: dict[str, int] = attrs.Factory(dict)
    session_id: str | None = None  # link to game session


@attrs.define(frozen=True)
class Standing:
    participant: str
    points: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    goal_diff: int = 0
    buchholz: float = 0.0        # strength of schedule (swiss)
```

### 2.3 Match Generation Algorithms

All deterministic (seeded RNG):

```python
class MatchGenerator:
    """Generate matches for tournament formats. All methods are pure."""
    
    @staticmethod
    def round_robin(participants: list[str], seed: int) -> list[Match]:
        """Circle method: each plays everyone else."""
        from engine.runtime.rng import DeterministicRNG
        rng = DeterministicRNG(seed)
        shuffled, rng = rng.shuffle(list(participants))
        
        n = len(shuffled)
        if n % 2 == 1:
            shuffled.append("BYE")
            n += 1
        
        matches = []
        match_idx = 0
        for round_num in range(1, n):
            for i in range(n // 2):
                a = shuffled[i]
                b = shuffled[n - 1 - i]
                if a == "BYE" or b == "BYE":
                    continue
                matches.append(Match(
                    id=f"rr-{match_idx}",
                    participants=(a, b),
                    round=round_num,
                ))
                match_idx += 1
            # Rotate all but first
            shuffled = [shuffled[0]] + [shuffled[-1]] + shuffled[1:-1]
        
        return matches
    
    @staticmethod
    def single_elimination(participants: list[str], seed: int) -> list[Match]:
        """Standard bracket with seeding."""
        from engine.runtime.rng import DeterministicRNG
        rng = DeterministicRNG(seed)
        seeded, rng = rng.shuffle(list(participants))
        
        # Pad to next power of 2
        import math
        size = 2 ** math.ceil(math.log2(len(seeded)))
        while len(seeded) < size:
            seeded.append("BYE")
        
        # Generate first round matches
        matches = []
        for i in range(0, size, 2):
            a, b = seeded[i], seeded[i + 1]
            if a == "BYE" or b == "BYE":
                continue  # bye → auto-advance
            matches.append(Match(
                id=f"se-r1-{i // 2}",
                participants=(a, b),
                round=1,
                stage="main",
            ))
        
        return matches  # Later rounds generated on match completion
    
    @staticmethod
    def swiss_pairing(standings: dict[str, Standing], 
                      history: set[tuple[str, str]],
                      round_num: int) -> list[Match]:
        """Swiss: pair players with similar scores who haven't played."""
        sorted_players = sorted(
            standings.values(),
            key=lambda s: (-s.points, -s.buchholz),
        )
        
        paired = set()
        matches = []
        match_idx = 0
        
        for player in sorted_players:
            if player.participant in paired:
                continue
            # Find closest unpaired opponent they haven't played
            for opponent in sorted_players:
                if opponent.participant == player.participant:
                    continue
                if opponent.participant in paired:
                    continue
                pair = tuple(sorted([player.participant, opponent.participant]))
                if pair in history:
                    continue
                matches.append(Match(
                    id=f"sw-r{round_num}-{match_idx}",
                    participants=(player.participant, opponent.participant),
                    round=round_num,
                ))
                paired.add(player.participant)
                paired.add(opponent.participant)
                match_idx += 1
                break
        
        return matches
```

### 2.4 Tournament Runtime

```python
class TournamentRuntime:
    """Manages tournament lifecycle. Stateless — operates on TournamentState."""
    
    def __init__(self, game_loader: Callable[[str], CompiledGame]):
        self.game_loader = game_loader
    
    def create(self, tournament_id: str, tournament_type: str,
               host: str, game_type: str, **config) -> TournamentState:
        return TournamentState(
            tournament_id=tournament_id,
            tournament_type=tournament_type,
            host=host,
            game_type=game_type,
            **config,
        )
    
    def register(self, state: TournamentState, participant: str) -> TournamentState:
        if state.status != "registration":
            raise ValueError("Registration closed")
        if len(state.participants) >= state.max_participants:
            raise ValueError("Tournament full")
        if participant in state.participants:
            raise ValueError("Already registered")
        return attrs.evolve(state,
            participants=state.participants + (participant,),
            standings={**state.standings, 
                      participant: Standing(participant=participant)},
        )
    
    def start(self, state: TournamentState) -> TournamentState:
        if len(state.participants) < state.min_participants:
            raise ValueError(f"Need {state.min_participants}+ participants")
        
        # Generate matches based on format
        match state.tournament_type:
            case "round_robin":
                matches = MatchGenerator.round_robin(
                    list(state.participants), state.seed)
            case "single_elimination":
                matches = MatchGenerator.single_elimination(
                    list(state.participants), state.seed)
            case "swiss":
                matches = MatchGenerator.swiss_pairing(
                    state.standings, set(), 1)
            case _:
                raise ValueError(f"Unknown format: {state.tournament_type}")
        
        return attrs.evolve(state,
            status="in_progress",
            matches={m.id: m for m in matches},
        )
    
    def report_result(self, state: TournamentState, match_id: str,
                      winner: str, scores: dict[str, int] | None = None,
                      ) -> TournamentState:
        match = state.matches.get(match_id)
        if not match or match.status != "active":
            raise ValueError(f"Match {match_id} not active")
        
        # Update match
        updated_match = attrs.evolve(match, 
            status="completed", winner=winner, scores=scores or {})
        matches = {**state.matches, match_id: updated_match}
        
        # Update standings
        standings = dict(state.standings)
        loser = [p for p in match.participants if p != winner][0]
        w = standings[winner]
        l = standings[loser]
        standings[winner] = attrs.evolve(w, 
            points=w.points + 3, wins=w.wins + 1,
            goal_diff=w.goal_diff + (scores.get(winner, 1) - scores.get(loser, 0)) if scores else w.goal_diff + 1)
        standings[loser] = attrs.evolve(l,
            losses=l.losses + 1,
            goal_diff=l.goal_diff + (scores.get(loser, 0) - scores.get(winner, 1)) if scores else l.goal_diff - 1)
        
        state = attrs.evolve(state, matches=matches, standings=standings)
        
        # Generate next round matches if needed
        state = self._progress(state)
        
        # Check completion
        state = self._check_completion(state)
        
        return state
    
    def _progress(self, state: TournamentState) -> TournamentState:
        """Generate next round matches based on format."""
        match state.tournament_type:
            case "single_elimination":
                return self._progress_elimination(state)
            case "swiss":
                return self._progress_swiss(state)
            case _:
                return state  # round_robin has all matches from start
    
    def _progress_elimination(self, state: TournamentState) -> TournamentState:
        """Advance elimination bracket when a round completes."""
        # Group matches by round
        by_round: dict[int, list[Match]] = {}
        for m in state.matches.values():
            by_round.setdefault(m.round, []).append(m)
        
        max_round = max(by_round.keys())
        current_round = by_round[max_round]
        
        # Check if current round is complete
        if all(m.status == "completed" for m in current_round):
            winners = [m.winner for m in current_round if m.winner]
            if len(winners) >= 2:
                # Generate next round
                new_matches = {}
                for i in range(0, len(winners), 2):
                    if i + 1 < len(winners):
                        mid = f"se-r{max_round + 1}-{i // 2}"
                        new_matches[mid] = Match(
                            id=mid,
                            participants=(winners[i], winners[i + 1]),
                            round=max_round + 1,
                        )
                state = attrs.evolve(state, 
                    matches={**state.matches, **new_matches})
        
        return state
    
    def _progress_swiss(self, state: TournamentState) -> TournamentState:
        """Generate next swiss round when current completes."""
        max_rounds = state.rounds or int(len(state.participants) ** 0.5 * 2)
        
        # Get completed rounds
        rounds_completed = set()
        for m in state.matches.values():
            if m.status == "completed":
                rounds_completed.add(m.round)
        
        current_round = max(rounds_completed) if rounds_completed else 0
        
        # Check if all matches in current round are done
        current_matches = [m for m in state.matches.values() if m.round == current_round]
        if current_matches and all(m.status == "completed" for m in current_matches):
            if current_round < max_rounds:
                # Generate next round
                history = {
                    tuple(sorted(m.participants)) 
                    for m in state.matches.values()
                }
                new_matches = MatchGenerator.swiss_pairing(
                    state.standings, history, current_round + 1)
                state = attrs.evolve(state,
                    matches={**state.matches, **{m.id: m for m in new_matches}})
        
        return state
    
    def _check_completion(self, state: TournamentState) -> TournamentState:
        """Check if tournament is finished."""
        all_done = all(m.status == "completed" for m in state.matches.values())
        pending_rounds = self._has_pending_rounds(state)
        
        if all_done and not pending_rounds:
            # Determine winner
            sorted_standings = sorted(
                state.standings.values(),
                key=lambda s: (-s.points, -s.goal_diff, -s.wins),
            )
            winner = sorted_standings[0].participant if sorted_standings else None
            return attrs.evolve(state, status="completed", winner=winner)
        
        return state
    
    def _has_pending_rounds(self, state: TournamentState) -> bool:
        match state.tournament_type:
            case "single_elimination":
                completed = [m for m in state.matches.values() if m.status == "completed"]
                # Not done until final (1 winner remaining)
                max_round = max((m.round for m in state.matches.values()), default=0)
                max_round_matches = [m for m in state.matches.values() if m.round == max_round]
                return len(max_round_matches) > 1 or any(m.status != "completed" for m in max_round_matches)
            case "swiss":
                max_rounds = state.rounds or int(len(state.participants) ** 0.5 * 2)
                completed_rounds = {m.round for m in state.matches.values() if m.status == "completed"}
                return len(completed_rounds) < max_rounds
            case _:
                return False

    def get_pending_matches(self, state: TournamentState) -> list[Match]:
        return [m for m in state.matches.values() if m.status == "pending"]
    
    def get_standings(self, state: TournamentState) -> list[Standing]:
        return sorted(
            state.standings.values(),
            key=lambda s: (-s.points, -s.goal_diff, -s.wins),
        )
```

### 2.5 Tournament Session Manager

Async wrapper (like GameSession for games):

```python
class TournamentSession:
    """Manages one tournament. Thread-safe via asyncio.Lock."""
    
    def __init__(self, runtime: TournamentRuntime, state: TournamentState):
        self.runtime = runtime
        self._state = state
        self._lock = asyncio.Lock()
        self._match_sessions: dict[str, str] = {}  # match_id → session_id
        self._listeners: list[Any] = []
    
    @property
    def state(self) -> TournamentState:
        return self._state
    
    async def register(self, participant: str) -> TournamentState:
        async with self._lock:
            self._state = self.runtime.register(self._state, participant)
            await self._notify()
            return self._state
    
    async def start(self) -> TournamentState:
        async with self._lock:
            self._state = self.runtime.start(self._state)
            await self._notify()
            # Spawn available matches
            await self._spawn_matches()
            return self._state
    
    async def report_result(self, match_id: str, winner: str,
                            scores: dict | None = None) -> TournamentState:
        async with self._lock:
            self._state = self.runtime.report_result(
                self._state, match_id, winner, scores)
            await self._notify()
            # Spawn next matches if any
            await self._spawn_matches()
            return self._state
    
    async def _spawn_matches(self):
        """Create game sessions for pending matches."""
        for match in self.runtime.get_pending_matches(self._state):
            if match.id in self._match_sessions:
                continue
            # Create game session
            compiled = self.runtime.game_loader(self._state.game_type)
            session = create_session(
                f"t-{self._state.tournament_id}-{match.id}",
                compiled,
                list(match.participants),
                seed=hash(f"{self._state.seed}-{match.id}"),
            )
            await session.start()
            self._match_sessions[match.id] = session.session_id
            
            # Update match status to active
            updated = attrs.evolve(match, 
                status="active", session_id=session.session_id)
            self._state = attrs.evolve(self._state,
                matches={**self._state.matches, match.id: updated})
    
    def get_match_session(self, match_id: str) -> str | None:
        return self._match_sessions.get(match_id)
```

### 2.6 File Structure

```
py/
├── tournament/
│   ├── __init__.py
│   ├── state.py           # TournamentState, Match, Standing (frozen dataclasses)
│   ├── generator.py       # MatchGenerator (round_robin, single_elim, swiss)
│   ├── runtime.py         # TournamentRuntime (stateless pure functions)
│   └── sessions.py        # TournamentSession (async wrapper)
```

**Estimated LOC**: ~600 (vs Clojure's ~2500)

---

## Part 3: Integration Points

### 3.1 MCP ↔ Tournament

```python
# Tournament MCP tools (in mcp/server.py)

async def handle_register_tournament(self, agent, args):
    tid = args["tournament_id"]
    t_session = get_tournament(tid)
    await t_session.register(agent.agent_id)
    agent.state = "in_tournament"
    agent.tournament_id = tid
    return [TextContent(text=f"Registered for tournament {tid}")]

async def handle_join_match(self, agent, args):
    """Join a tournament match → transition to in_game."""
    t_session = get_tournament(agent.tournament_id)
    match_id = args["match_id"]
    session_id = t_session.get_match_session(match_id)
    
    # Transition agent to in-game with tournament context
    agent.session_id = session_id
    agent.state = "in_game"
    agent.tournament_context = {
        "tournament_id": agent.tournament_id,
        "match_id": match_id,
    }
    return [TextContent(text=f"Joined match {match_id}")]
```

### 3.2 Game Completion → Tournament Result

```python
# In GameSession (server/sessions.py)

async def _check_and_handle_completion(self, result: dict):
    """After each action, check if game ended and notify tournament."""
    victory = self.runtime.check_victory(self._state)
    if not victory:
        return
    
    self._state = self.runtime.end_game(self._state, victory)
    
    # If this is a tournament match, report result
    if self._tournament_callback:
        await self._tournament_callback(
            session_id=self.session_id,
            winner=victory.get("winner"),
            scores=victory.get("scores"),
        )
```

### 3.3 Archive & Replay

The engine already records all decisions in `GameState.decisions`.
Tournament adds a thin layer:

```python
@attrs.define(frozen=True)
class TournamentArchive:
    tournament_id: str
    tournament_type: str
    seed: int
    participants: tuple[str, ...]
    match_results: tuple[MatchResult, ...]  # ordered
    
    # Replay: regenerate bracket → verify results match
    def verify(self, runtime: TournamentRuntime) -> bool:
        state = runtime.create(self.tournament_id, self.tournament_type,
                              host="", game_type="")
        for p in self.participants:
            state = runtime.register(state, p)
        state = runtime.start(state)
        for result in self.match_results:
            state = runtime.report_result(
                state, result.match_id, result.winner, result.scores)
        return state.status == "completed"
```

---

## Part 4: HTTP API

FastAPI for REST, leveraging existing NiceGUI server:

```python
# In server/api.py

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

api = FastAPI(prefix="/api")

class CreateTournamentRequest(BaseModel):
    tournament_type: str
    game_type: str
    name: str = ""
    min_participants: int = 2
    max_participants: int = 16

@api.post("/tournaments")
async def create_tournament(req: CreateTournamentRequest):
    t_session = create_tournament_session(req)
    return {"tournament_id": t_session.state.tournament_id, "status": "registration"}

@api.get("/tournaments")
async def list_tournaments(status: str | None = None):
    sessions = list_tournament_sessions()
    if status:
        sessions = {k: v for k, v in sessions.items() if v.state.status == status}
    return {"tournaments": [summarize(s) for s in sessions.values()]}

@api.post("/tournaments/{tid}/register")
async def register(tid: str, participant_id: str):
    t_session = get_tournament(tid)
    if not t_session:
        raise HTTPException(404, "Tournament not found")
    await t_session.register(participant_id)
    return {"registered": True}

@api.post("/tournaments/{tid}/start")
async def start_tournament(tid: str):
    t_session = get_tournament(tid)
    if not t_session:
        raise HTTPException(404, "Tournament not found")
    await t_session.start()
    return {"status": "in_progress", "matches": len(t_session.state.matches)}
```

---

## Part 5: Implementation Plan

### Phase 1: MCP Core (~800 LOC)

```
1. mcp/agents.py       — AgentState, registry         (~80 LOC)
2. mcp/schema.py       — Tool generation from compiled (~200 LOC)
3. mcp/server.py       — MCP server with handlers     (~350 LOC)
4. mcp/tokens.py       — HMAC invite tokens            (~80 LOC)
5. mcp/formatters.py   — Status formatting for AI      (~90 LOC)
```

**Deliverable**: AI agents can discover games, join sessions, play any of the 7 games
via MCP protocol, with dynamic tool filtering per phase.

### Phase 2: Tournament Core (~600 LOC)

```
1. tournament/state.py     — Frozen dataclasses       (~100 LOC)
2. tournament/generator.py — Match algorithms          (~200 LOC)
3. tournament/runtime.py   — Tournament lifecycle      (~200 LOC)
4. tournament/sessions.py  — Async session manager     (~100 LOC)
```

**Deliverable**: Create, register, start, play, and complete tournaments with
round-robin, single-elimination, and Swiss formats.

### Phase 3: Integration (~300 LOC)

```
1. MCP tournament tools in server.py        (~100 LOC)
2. Game completion → tournament callback    (~50 LOC)
3. HTTP API routes                          (~100 LOC)
4. Tournament archive & replay             (~50 LOC)
```

**Deliverable**: Full MCP+Tournament integration. AI agents can create tournaments,
register, play matches, see standings.

### Phase 4: Tests (~500 LOC)

```
1. test_mcp_schema.py       — Tool generation tests
2. test_tournament.py        — Tournament lifecycle tests
3. test_mcp_integration.py   — E2E: agent plays game via MCP
```

---

## Key Differences from Clojure

| Aspect | Clojure | Python |
|--------|---------|--------|
| **State** | Atoms + CAS | asyncio.Lock |
| **Immutability** | Built-in (persistent maps) | attrs.frozen + evolve |
| **Effect dispatch** | defmulti | type-keyed registry |
| **MCP transport** | Custom HTTP handler | mcp Python SDK |
| **Tournament runtime** | Flow extension | Standalone (simpler) |
| **Schema gen** | Dynamic Malli→JSON | Compiled→dict (direct) |
| **Match spawning** | Lazy via callbacks | Async in session manager |
| **Total LOC** | ~5500 | ~1700 (estimated) |

The Python version achieves the same external behavior with ~30% of the code,
by leveraging the language's strengths and avoiding the complexity of Clojure's
generic flow/multimethod architecture.
