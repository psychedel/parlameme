# Tournament Layer

The tournament layer manages competitive play across multiple game sessions. It supports three formats (round robin, single elimination, swiss), multi-player match sizes, automatic result reporting from game sessions, match-level timeouts, persistence for server restarts, and full MCP integration for AI agents.

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│  MCP Server (tournament tools)                                         │
│  list/create/register/start/join_match/wait_for_match/leave            │
└───────────────────────────┬────────────────────────────────────────────┘
                            │ delegates to
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│  TournamentSession (async, locked)                                     │
│  One instance per active tournament                                    │
│                                                                        │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────┐   │
│  │  register/start   │  │  report_result   │  │  report_draw       │   │
│  │  unregister       │  │  report_draw     │  │  (timeout-forced)  │   │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬───────────┘   │
│           └─────────────────────┼──────────────────────┘               │
│                                 ▼                                      │
│  ┌────────────────────────────────────────────────────┐                │
│  │  TournamentRuntime (pure, stateless)               │                │
│  │  All methods: state in → Result out                │                │
│  │  create · register · start · report · progress     │                │
│  └───────────────────┬────────────────────────────────┘                │
│                      │                                                 │
│         ┌────────────┼────────────────┐                                │
│         ▼            ▼                ▼                                 │
│    MatchGenerator  TournamentState  TournamentStore                    │
│    (deterministic)  (frozen)         (JSON persistence)                │
│                                                                        │
│  Side effects:                                                         │
│  ┌──────────────────┐  ┌─────────────────┐  ┌──────────────────────┐  │
│  │  _spawn_matches   │  │  Auto-report    │  │  Match timeouts     │  │
│  │  → GameSession    │  │  (listener)     │  │  (asyncio.Task)     │  │
│  └──────────────────┘  └─────────────────┘  └──────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
                            │
                            ▼ on completion
                    ┌───────────────────┐
                    │  TournamentArchive │
                    │  + Chronicle       │
                    └───────────────────┘
```

## File Layout

| File | Purpose | Lines |
|------|---------|-------|
| `tournament/state.py` | Frozen dataclasses: Match, Standing, TournamentState | ~65 |
| `tournament/runtime.py` | Pure stateless runtime: lifecycle, progression, completion | ~430 |
| `tournament/sessions.py` | Async session: locking, spawn, auto-report, timeout, global registry | ~355 |
| `tournament/generator.py` | Deterministic match generation: round_robin, SE, swiss | ~265 |
| `tournament/persistence.py` | JSON serialization with debounced writes | ~185 |
| `tournament/archive.py` | Tournament archive + chronicle generation | ~205 |
| `tests/test_tournament.py` | 60+ tests covering all formats, edge cases, timeouts | ~1170 |

---

## State — Frozen Dataclasses

All tournament state is immutable via `@attrs.frozen`. Mutations return new instances through `attrs.evolve()`.

### TournamentState

```python
@attrs.frozen
class TournamentState:
    tournament_id: str
    tournament_type: str      # round_robin | single_elimination | swiss
    status: str = "registration"  # registration | in_progress | completed | cancelled
    host: str = ""
    name: str = ""
    game_type: str = ""       # references a key in games.REGISTRY
    min_participants: int = 2
    max_participants: int = 16
    match_size: int = 2       # players per match (from game's min_players)
    rounds: int | None = None # for swiss (None = auto-calculated)
    participants: tuple[str, ...] = ()
    matches: dict[str, Match] = {}
    standings: dict[str, Standing] = {}
    winner: str | None = None
    seed: int = 42
```

**Status lifecycle:**

```
registration ──(start)──► in_progress ──(all matches done)──► completed
     │                         │
     └──(cancel)───────────────┴──────────────────────────► cancelled
```

### Match

```python
@attrs.frozen
class Match:
    id: str                          # e.g. "rr-0", "se-r1-2", "sw-r2-1"
    participants: tuple[str, ...] = ()
    round: int = 1
    stage: str = "main"              # main | winners | losers | grand_final
    status: str = "pending"          # pending | active | completed
    winner: str | None = None
    scores: dict[str, int] = {}
    session_id: str | None = None    # links to the GameSession (SSOT)
```

`session_id` is the **single source of truth** for the match-to-game-session mapping. The previous `_match_sessions` dict was eliminated in the Layer 5 refactoring.

### Standing

```python
@attrs.frozen
class Standing:
    participant: str
    points: int = 0          # 3 for win, 1 for draw, 0 for loss
    wins: int = 0
    losses: int = 0
    draws: int = 0
    goal_diff: int = 0       # winner_score - loser_score per match
    buchholz: float = 0.0    # strength of schedule (sum of opponents' points)
```

**Sorting key:** `(-points, -goal_diff, -wins)` for standings. Buchholz is used as a secondary tiebreaker in swiss pairing.

---

## TournamentRuntime — Pure Logic

`TournamentRuntime` is stateless. Every method takes a `TournamentState` and returns a `Result` (`Ok | Err` dict). No I/O, no side effects, no locks.

### Lifecycle Methods

| Method | Input | Output | Description |
|--------|-------|--------|-------------|
| `create(...)` | config params | `TournamentState` | Create initial state (registration phase) |
| `register(state, participant)` | state + player ID | `Result` | Add participant (fails if full/duplicate/started) |
| `unregister(state, participant)` | state + player ID | `Result` | Remove participant (registration phase only) |
| `start(state)` | state | `Result` | Generate initial matches, transition to in_progress |
| `report_result(state, match_id, winner, scores?)` | state + result | `Result` | Record win, update standings, progress bracket |
| `report_draw(state, match_id, scores?)` | state + match_id | `Result` | Record draw (1 point each), progress bracket |

### Result Type

All mutating methods return the standard `Result` dict:

```python
# Success
{"ok": True, "state": new_tournament_state}

# Failure
{"ok": False, "error": {"code": "match_not_found", "message": "..."}}
```

Error codes are defined in `engine/errors.py`:

| Code | When |
|------|------|
| `registration_closed` | register/unregister after tournament started |
| `tournament_full` | register when max_participants reached |
| `already_registered` | duplicate registration |
| `not_registered` | unregister someone not in tournament |
| `tournament_started` | start when already in_progress |
| `match_not_found` | report result for unknown match_id |
| `match_completed` | report result for already-completed match |
| `winner_not_in_match` | winner not in match participants |

### Internal Pipeline

After every `report_result` / `report_draw`, three steps run in sequence:

```
report_result(state, match_id, winner)
    │
    ├─► _update_buchholz(state)   # recompute strength-of-schedule
    ├─► _progress(state)           # generate next round if current complete
    └─► _check_completion(state)   # mark tournament completed if finished
```

### Completion Detection

`is_completed(state)` is a **pure query** (no mutation) that returns the winner if the tournament is finished:

- **Round Robin:** all matches completed → top-ranked by standings
- **Single Elimination:** final match (highest round, single match) completed → that match's winner
- **Swiss:** all rounds up to max_rounds completed → top-ranked by standings

`_check_completion(state)` calls `is_completed()` and evolves state to `status="completed"` with the winner set.

---

## MatchGenerator — Deterministic Algorithms

All methods are **static, pure, and deterministic** (seeded RNG). This ensures replay compatibility: same seed + same participants = same bracket.

### Round Robin

```python
MatchGenerator.round_robin(participants, seed, match_size=2)
```

- **match_size=2:** Circle method. `n` players → `n*(n-1)/2` matches across `n-1` rounds. Odd player count: virtual `__BYE__` added, matches with bye skipped.
- **match_size>2:** All `C(n, match_size)` combinations, shuffled by RNG.

### Single Elimination

```python
MatchGenerator.single_elimination(participants, seed, match_size=2)
```

Only generates **first round**. Subsequent rounds are generated by `_progress_elimination()` as rounds complete.

- **match_size=2:** Standard bracket. Pad to next power of 2; pair outside-in (1st vs last seed). Players without opponents get byes.
- **match_size>2:** Pod-based. Split into groups of `match_size`. Remainder forms a smaller pod (≥2) or single leftover gets a bye.

**Bye handling:** `get_bye_advances(participants, seed, match_size)` returns players who auto-advance. The runtime injects bye players into the next round alongside round winners.

```
Round 1: [a,b] [c,d] [e has bye] [f has bye]
             │       │
             ▼       ▼
Round 2: [winner_ab, winner_cd] [e, f]
                    │                │
                    ▼                ▼
Round 3 (final): [winner, winner]
```

### Swiss

```python
MatchGenerator.swiss_pairing(standings, history, round_num, match_size=2)
```

- Sort by `(-points, -buchholz)`.
- **match_size=2:** Pair adjacent players, avoiding rematches from `history` set. Fallback to any unpaired player if no fresh opponent available.
- **match_size>2:** Group adjacent players into pods of `match_size`.

Swiss round count: `state.rounds` if specified, otherwise `max(3, int(sqrt(n) * 2))`.

---

## TournamentSession — Async Orchestration

`TournamentSession` wraps the pure runtime with real-world concerns: locking, game session spawning, auto-reporting, timeouts, persistence, and notifications.

### Concurrency Model

All state mutations go through `async with self._lock:`. This guarantees:

- No concurrent state modifications
- Auto-report callbacks (which call `report_result`) wait for the lock
- Match timeout handlers wait for the lock

**Notification is outside the lock** to prevent deadlocks:

```python
async def report_result(self, match_id, winner, ...):
    async with self._lock:
        # ... mutate state ...
        _store.save(self._state)
    await self._notify()  # outside lock — listeners may call back into us
```

### Match Spawning

When a tournament starts or a new round is generated, `_spawn_matches()` creates `GameSession` instances for each pending match:

```
pending match → GameSession(session_id, compiled, participants, seed)
                    │
                    ├─► session.start()
                    ├─► _subscribe_auto_report(session, match_id)
                    ├─► _start_match_timeout(match_id)
                    └─► mark match as "active" with session_id
```

Session IDs follow the pattern: `t-{tournament_id}-{match_id}` (e.g. `t-cup-2026-se-r1-0`).

### Auto-Report

Each spawned game session gets a subscriber that watches for game completion:

```python
def _subscribe_auto_report(self, game_session, match_id, compiled):
    reported = False  # guard against double-report

    async def _on_game_state_change(game_state):
        nonlocal reported
        if reported or game_state.status != "ended":
            return
        reported = True
        winner = game_state.victory_result.get("winner")
        await self.report_result(match_id, winner, ...)
```

Key properties:
- **Double-report guard:** `reported` flag ensures at most one report per match.
- **No-winner fallback:** If game ends without a winner (timeout), the callback logs a warning and skips reporting. The match timeout handler will force a draw instead.
- **Error tolerance:** `ValueError` from `report_result` (already completed) is caught and logged.

### Match Timeouts

Each active match gets a timeout task (`MATCH_TIMEOUT = 1800s` = 30 minutes):

```python
def _start_match_timeout(self, match_id, compiled):
    async def _timeout():
        await asyncio.sleep(self.MATCH_TIMEOUT)
        if match.status != "completed":
            await self.report_draw(match_id, compiled=compiled)
    self._match_timeouts[match_id] = asyncio.ensure_future(_timeout())
```

When a match completes normally (via `report_result` or `report_draw`), the timeout task is cancelled:

```python
def _cancel_match_timeout(self, match_id):
    task = self._match_timeouts.pop(match_id, None)
    if task and not task.done():
        task.cancel()
```

### Tournament Completion

When the last match finishes and `_check_completion()` detects a winner:

1. `_save_tournament_archive()` creates and persists a `TournamentArchive` + chronicle
2. Completion callbacks fire (e.g. Glicko-2 rating updates, PG sync)
3. Persistence store is updated

### Global Registry

Module-level functions manage the tournament registry:

```python
create_tournament(tid, type, host, game, **kw) → TournamentSession
get_tournament(tid) → TournamentSession | None
list_tournaments() → dict[str, TournamentSession]
remove_tournament(tid)
load_tournaments() → int        # startup recovery
flush_tournaments()              # shutdown persistence
reset_all()                      # testing
```

`load_tournaments()` is called on server startup, `flush_tournaments()` on shutdown.

---

## Persistence — JSON Recovery

`TournamentStore` serializes all tournament state to a single JSON file (`data/tournaments.json`). This survives server restarts but does NOT restore active game sessions or timeout tasks.

### Serialization

```python
_state_to_dict(state: TournamentState) → dict    # all fields, flat
_dict_to_state(d: dict) → TournamentState         # reconstruct with defaults
```

Every field is serialized: participants, matches (with session_id, scores), standings (with buchholz), winner, seed.

### Debounced Writes

Rapid state changes (e.g. multiple matches reporting in quick succession) coalesce into a single write:

```python
SAVE_DELAY = 2.0  # seconds

def _schedule_save(self):
    self._dirty = True
    if no pending save task:
        create task → sleep(2s) → _write()

def flush(self):     # synchronous, call on shutdown
    if self._dirty:
        self._write()
```

---

## Archive — Tournament History

When a tournament completes, `TournamentArchive` captures the minimal representation:

```python
@attrs.frozen
class TournamentArchive:
    version: int = 1
    tournament_id: str
    tournament_type: str
    game_type: str
    host: str
    name: str
    participants: tuple[str, ...]
    matches: tuple[dict, ...]       # sorted by (round, id)
    standings: dict[str, dict]
    winner: str | None
    seed: int
    timestamp: float
    match_archives: tuple[str, ...]  # session_ids → link to game archives
```

Archives are saved to `data/archives/tournaments/{tournament_id}.json`.

### Chronicle

`generate_tournament_chronicle(state)` produces a list of structured events for narrative/replay:

```
[header] → [match, match, ...] → [end]
```

Each match event includes round, participants, winner, scores, and session_id. The end event includes final standings sorted by rank. Chronicles are saved as JSONL to `data/archives/tournaments/{tournament_id}-chronicle.jsonl`.

---

## MCP Integration — Agent Tools

### Agent State Machine

Tournament participation extends the MCP agent state machine:

```
LOBBY ──(create/register)──► IN_TOURNAMENT ──(join_match)──► IN_GAME
                                    │                           │
                                    │                    (leave_game)
                                    │                           │
                                    │            ◄──(back_to_tournament)──┘
                                    │
                              (leave_tournament)
                                    │
                                    ▼
                                  LOBBY
```

`TournamentContext` (frozen dataclass) preserves the tournament_id and match_id while the agent is in a game. When the agent calls `leave_game`, `back_to_tournament()` restores the tournament state.

### Tool Availability

**In-tournament tools:**

| Tool | Description |
|------|-------------|
| `get_tournament_status` | Tournament name, format, status, participant count, winner |
| `get_standings` | Ranked standings with points, W/L/D |
| `get_my_matches` | Agent's matches with readiness signals |
| `join_match` | Enter an active match's game session |
| `report_match_result` | Manual result reporting (match_id + winner) |
| `leave_tournament` | Unregister (if registration) and return to lobby |
| `wait_for_match` | Long-poll until a match is ready (max 60s) |
| `my_status` | Current agent state |

**Lobby tools for tournament creation:**

| Tool | Description |
|------|-------------|
| `list_tournaments` | All active tournaments with status |
| `create_tournament` | Create + auto-register. Params: type, game, name |
| `register_tournament` | Join existing tournament by ID |
| `start_tournament` | Host-only: begin the tournament |

### wait_for_match — Long Poll

Instead of polling `get_my_matches`, agents use `wait_for_match`:

```python
async def _tool_wait_for_match(self, agent, args):
    # Return immediately if a match is already active
    for m in ts.state.matches.values():
        if m.status == "active" and agent.agent_id in m.participants:
            return self._tool_get_my_matches(agent)

    # Subscribe and block until state changes
    event = asyncio.Event()
    ts.subscribe(lambda state: event.set())
    await asyncio.wait_for(event.wait(), timeout=60)
    return self._tool_get_my_matches(agent)
```

This reduces agent API calls from O(n) polling to 1 call per state change.

### Agent Flow: Full Tournament

```
1. create_tournament(type="round_robin", game="auction")
   → agent moves to IN_TOURNAMENT
2. Other agents: register_tournament(tournament_id=...)
3. Host: start_tournament()
   → matches spawned, game sessions created
4. wait_for_match()
   → returns when match is READY
5. join_match(match_id="se-r1-0")
   → agent moves to IN_GAME (with TournamentContext preserved)
6. Play the game (act, wait_for_turn, ...)
7. Game ends → auto-report fires → tournament standings updated
8. leave_game
   → agent returns to IN_TOURNAMENT
9. wait_for_match() for next round
10. Repeat until tournament completes
```

---

## Scoring System

### Points

| Result | Points |
|--------|--------|
| Win | 3 |
| Draw | 1 |
| Loss | 0 |

### Goal Difference

Per match: `goal_diff += winner_score - loser_score` for the winner, inverse for each loser. Multi-player matches: winner's diff = `w_score - avg(loser_scores)`, each loser's diff = `-(w_score - their_score)`.

### Buchholz (Strength of Schedule)

Recomputed after every result: `buchholz = sum(opponent.points for opponent in all_completed_matches)`.

Used as secondary sort key in swiss pairing: `(-points, -buchholz)`.

**Example:** After round 1 with 4 players where A beats B and C beats D:
- A's buchholz = B's points = 0 (beat the loser)
- B's buchholz = A's points = 3 (lost to the winner)

---

## Edge Cases and Design Decisions

### Draws in Single Elimination

A draw means **no one advances** from that match. If this leaves fewer than 2 players for the next round, the bracket ends. This is intentional: the match timeout (30 minutes) is the pressure mechanism to avoid stalemates.

### Bye Handling in SE

Players who don't fit into a full bracket receive byes:

```
6 players, match_size=2:
  Round 1: 2 matches (4 players) + 2 byes
  Round 2: 2 winners + 2 bye players = 4 players = 2 matches
  Round 3: final
```

Bye players are injected into the next round by `_progress_elimination()`, which checks `get_bye_advances()` against the set of players who have already appeared in any match.

### Multi-Player Match Sizes

When `match_size > 2` (common for games like Auction with `min_players=3`):

- **Round Robin:** generates `C(n, match_size)` combinations
- **Single Elimination:** pod-based brackets (groups of `match_size`)
- **Swiss:** groups adjacent-ranked players into pods

### Persistence Limitations

The JSON persistence stores tournament state but does **not** store:
- Active `asyncio.Task` references (timeout timers)
- Game session objects
- Subscriber callbacks

On server restart, matches that were active become orphaned. A future improvement could reconnect active matches or force-draw abandoned ones.

---

## Testing

60+ tests organized by concern:

| Test Class | Count | Focus |
|------------|-------|-------|
| `TestRoundRobin` | 8 | Generator: match count, all pairs, determinism, odd players |
| `TestSingleElimination` | 6 | Generator: brackets, power-of-2, next round |
| `TestSwiss` | 3 | Generator: first round, rematch avoidance, score-based pairing |
| `TestTournamentRuntime` | 10 | Lifecycle: create, register, unregister, start, edge cases |
| `TestRoundRobinCompletion` | 2 | Full RR tournament, standings update |
| `TestSingleEliminationProgression` | 3 | Round advancement, final completion, bye advances (6 players) |
| `TestSwissProgression` | 3 | Next round generation, max rounds completion, Buchholz tiebreaker |
| `TestEdgeCases` | 4 | Unknown match, wrong winner, double report, 2-player tournament |
| `TestAutoReport` | 7 | Game→tournament auto-report, full completion, double-report guard, phase timeout, match timeout |
| `TestDrawSupport` | 3 | Draw points, SE draw behavior, draw on completed match |
| `TestMultiPlayerMatchSize` | 7 | SE/Swiss/RR with match_size=3/4, bye advances |
| `TestTournamentPersistence` | 3 | Round-trip save/load, results persistence, remove |
| `TestMatchTimeoutCleanup` | 3 | Cancel on report, nonexistent match, already-done task |

### Key Test Patterns

```python
# Unwrap Result helper
def _ok(result):
    assert result["ok"], f"Expected ok, got: {result}"
    return result["state"]

# Async fixture cleanup
@pytest.fixture(autouse=True)
def _clean(self):
    reset_tournaments()
    yield
    reset_tournaments()

# Instant timeout via patching
async def instant_sleep(duration):
    await real_sleep(0)
with patch("server.sessions.asyncio.sleep", side_effect=instant_sleep):
    ...
```
