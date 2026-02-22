# Directus + PostgreSQL Integration Design

## Architecture Overview

```
                      Source of Truth
                    ┌──────────────────┐
                    │ JSON Archives    │  seed + decisions
                    │ Hash-chain Ledger│  append-only
                    │ Python Code      │  game definitions
                    └────────┬─────────┘
                             │ writes on game events
                             ▼
┌──────────────────────────────────────────────────────────┐
│                     PostgreSQL                            │
│                                                          │
│  Schema: public (shared)                                 │
│  ┌──────────┐ ┌──────────┐ ┌───────┐ ┌──────────────┐  │
│  │ players  │ │ archives │ │ratings│ │ tournaments  │  │
│  │ (auth)   │ │ (index)  │ │(cache)│ │ (matches)    │  │
│  └──────────┘ └──────────┘ └───────┘ └──────────────┘  │
│  ┌──────────┐ ┌──────────┐ ┌───────────────────────┐   │
│  │ ledger   │ │ sessions │ │ directus_* (system)   │   │
│  │ (mirror) │ │ (active) │ │ (managed by Directus) │   │
│  └──────────┘ └──────────┘ └───────────────────────┘   │
└────────────────────────┬─────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
   ┌────────────┐ ┌──────────┐  ┌──────────────┐
   │  Directus  │ │  Game    │  │  Analytics   │
   │  Admin UI  │ │  Engine  │  │  Dashboard   │
   │  :8055     │ │  :8080   │  │  (NiceGUI)   │
   │            │ │          │  │              │
   │  REST API  │ │  JDBC    │  │  REST from   │
   │  Auth/JWT  │ │  writes  │  │  Directus    │
   └────────────┘ └──────────┘  └──────────────┘
```

## Key Principle: Dual-Write, Single Source

JSON files remain the **canonical source of truth**:
- Archives: deterministic replay (blockchain-friendly)
- Ledger: hash-chain integrity (tamper-proof)
- Game code: compiled game definitions

PostgreSQL is a **read-optimized mirror** (projection):
- Queryable index over archives
- Cached ratings and stats
- Player profiles and auth
- Tournament state persistence

On every game event, the engine writes to **both** JSON and PostgreSQL.
If PostgreSQL dies, we lose nothing — rebuild from JSON files.
If JSON files are lost, PostgreSQL has a copy of the data.

## PostgreSQL Schema

### Table: `players`

Player profiles and authentication. Managed by **Directus auth** — each
player is a `directus_users` entry with game-specific fields.

```sql
-- Directus manages directus_users table natively.
-- We extend it with a custom collection for game profiles:

CREATE TABLE player_profiles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    directus_user   UUID REFERENCES directus_users(id) ON DELETE CASCADE,
    display_name    VARCHAR(64) NOT NULL,
    -- Ratings (Glicko-2)
    rating_mu       FLOAT NOT NULL DEFAULT 1500.0,
    rating_rd       FLOAT NOT NULL DEFAULT 350.0,
    rating_vol      FLOAT NOT NULL DEFAULT 0.06,
    rating_tier     VARCHAR(20) NOT NULL DEFAULT 'Novice',
    -- Aggregates (updated after each game)
    games_played    INT NOT NULL DEFAULT 0,
    wins            INT NOT NULL DEFAULT 0,
    losses          INT NOT NULL DEFAULT 0,
    win_rate        FLOAT NOT NULL DEFAULT 0.0,
    best_streak     INT NOT NULL DEFAULT 0,
    -- Ledger balance (cache — true balance is in hash-chain)
    balance         INT NOT NULL DEFAULT 0,
    -- Metadata
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_game_at    TIMESTAMPTZ,
    UNIQUE(display_name)
);

CREATE INDEX idx_profiles_rating ON player_profiles(rating_mu DESC);
CREATE INDEX idx_profiles_user ON player_profiles(directus_user);
```

### Table: `game_archives`

Index over JSON archive files. NOT the archive itself — just metadata
for querying. The actual archive is in `data/archives/*.json`.

```sql
CREATE TABLE game_archives (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(128) NOT NULL UNIQUE,
    game_type       VARCHAR(64) NOT NULL,
    rules_hash      VARCHAR(32) NOT NULL,
    seed            BIGINT NOT NULL,
    -- Participants (JSONB array of player display_names)
    players         JSONB NOT NULL DEFAULT '[]',
    player_count    SMALLINT NOT NULL DEFAULT 0,
    -- Outcome
    winner          VARCHAR(64),
    scores          JSONB,              -- {player: score}
    victory_condition VARCHAR(64),
    -- Stats
    decision_count  INT NOT NULL DEFAULT 0,
    round_count     INT NOT NULL DEFAULT 0,
    duration_secs   FLOAT,              -- wall-clock if available
    -- Timestamps
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Full archive JSON (optional — for API access without filesystem)
    archive_json    JSONB,
    -- Chronicle summary (first/last events)
    chronicle_summary JSONB
);

CREATE INDEX idx_archives_game ON game_archives(game_type);
CREATE INDEX idx_archives_winner ON game_archives(winner);
CREATE INDEX idx_archives_time ON game_archives(completed_at DESC);
CREATE INDEX idx_archives_players ON game_archives USING GIN(players);
```

### Table: `ledger_entries`

Mirror of the hash-chain ledger. NOT the source of truth (JSON file is).
Useful for SQL queries on transaction history.

```sql
CREATE TABLE ledger_entries (
    seq             INT PRIMARY KEY,
    type            VARCHAR(32) NOT NULL,   -- deposit, withdraw, game_credit, etc.
    player          VARCHAR(64) NOT NULL,
    amount          INT NOT NULL,
    ref             VARCHAR(256) DEFAULT '',
    prev_hash       VARCHAR(64) NOT NULL,
    content_hash    VARCHAR(64) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ledger_player ON ledger_entries(player);
CREATE INDEX idx_ledger_type ON ledger_entries(type);
```

### Table: `tournaments`

Persistent tournament state (currently in-memory only — lost on restart).

```sql
CREATE TABLE tournaments (
    id                  VARCHAR(128) PRIMARY KEY,
    tournament_type     VARCHAR(32) NOT NULL,   -- round_robin, single_elimination, swiss
    status              VARCHAR(20) NOT NULL DEFAULT 'registration',
    host                VARCHAR(64) NOT NULL,
    name                VARCHAR(256) NOT NULL,
    game_type           VARCHAR(64) NOT NULL,
    -- Config
    min_participants    SMALLINT NOT NULL DEFAULT 2,
    max_participants    SMALLINT NOT NULL DEFAULT 16,
    match_size          SMALLINT NOT NULL DEFAULT 2,
    seed                BIGINT NOT NULL DEFAULT 42,
    -- Participants (JSONB array)
    participants        JSONB NOT NULL DEFAULT '[]',
    -- Result
    winner              VARCHAR(64),
    standings           JSONB,          -- {player: {points, wins, losses, ...}}
    -- Timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ
);

CREATE TABLE tournament_matches (
    id                  VARCHAR(128) PRIMARY KEY,
    tournament_id       VARCHAR(128) NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    participants        JSONB NOT NULL DEFAULT '[]',
    round               SMALLINT NOT NULL DEFAULT 1,
    stage               VARCHAR(32) NOT NULL DEFAULT 'main',
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',
    winner              VARCHAR(64),
    scores              JSONB,
    session_id          VARCHAR(128),   -- links to game session
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX idx_matches_tournament ON tournament_matches(tournament_id);
CREATE INDEX idx_matches_status ON tournament_matches(status);
```

### Table: `active_sessions`

Replace `data/sessions.json` — survives restart, queryable.

```sql
CREATE TABLE active_sessions (
    session_id      VARCHAR(128) PRIMARY KEY,
    game_type       VARCHAR(64) NOT NULL,
    players         JSONB NOT NULL DEFAULT '[]',
    seed            BIGINT NOT NULL DEFAULT 42,
    -- For recovery: full decision list
    decisions       JSONB NOT NULL DEFAULT '[]',
    -- Status tracking
    current_phase   VARCHAR(64),
    current_round   SMALLINT DEFAULT 1,
    status          VARCHAR(20) NOT NULL DEFAULT 'active',
    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_sessions_status ON active_sessions(status);
CREATE INDEX idx_sessions_activity ON active_sessions(last_activity);
```

## Directus Configuration

### Roles

| Role | Purpose | App Access | Permissions |
|------|---------|------------|-------------|
| **Admin** | Platform admin | Yes | Full CRUD on all collections |
| **Player** | Authenticated player | Yes | Read own profile, Read archives, Read tournaments |
| **Spectator** | Public/anonymous | No | Read-only on public data (archives, tournaments, leaderboard) |
| **Engine** | Service account | No | Full CRUD (used by Python engine via static token) |

### Directus Auth for Players

Directus provides built-in auth with JWT tokens:

```
POST /auth/login
  {email, password} → {access_token, refresh_token}

POST /auth/refresh
  {refresh_token} → {access_token, refresh_token}

POST /users
  {email, password, display_name} → creates player
```

The game engine validates JWT tokens from Directus:
1. Player logs in via Directus → gets JWT
2. Player connects to game engine with JWT in header
3. Engine validates JWT against Directus (or shared secret)
4. Engine maps `directus_user.id` → `player_profiles.display_name`

### Engine Service Account

The Python engine uses a **static token** to write to PostgreSQL via Directus API
(or direct JDBC — see below).

```env
DIRECTUS_ENGINE_TOKEN=<long-random-static-token>
```

## Integration Strategy: Direct JDBC vs Directus API

### Option A: Direct psycopg (Recommended)

Engine writes directly to PostgreSQL. Fastest, no middleware overhead.

```python
# py/engine/pg.py — PostgreSQL sync module
import psycopg  # or psycopg[binary]

class PgSync:
    """Dual-write to PostgreSQL alongside JSON files.
    
    Fail-open: if PG is down, log warning and continue.
    JSON files remain source of truth.
    """
    
    def __init__(self, dsn: str | None = None):
        self._dsn = dsn  # None = disabled
        self._conn = None
    
    def sync_archive(self, archive: Archive) -> None:
        """Write archive index to game_archives table."""
        ...
    
    def sync_ledger_entry(self, entry: LedgerEntry) -> None:
        """Mirror ledger entry to ledger_entries table."""
        ...
    
    def update_player_stats(self, player_id: str, stats: dict) -> None:
        """Update player_profiles with latest ratings/stats."""
        ...
    
    def sync_session(self, session_id: str, data: dict) -> None:
        """Upsert active session for recovery."""
        ...
```

### Option B: Directus REST API

Engine writes via HTTP. Simpler, respects Directus permissions, but slower.

```python
# 60ms per request vs 2ms for direct SQL
resp = httpx.post(
    "http://localhost:8055/items/game_archives",
    json=archive_data,
    headers={"Authorization": f"Bearer {TOKEN}"},
)
```

### Recommendation: Option A (Direct psycopg)

- Game events are latency-sensitive (votes, deals)
- Directus API adds 30-60ms per call
- Engine already has direct filesystem access
- Directus reads from same PostgreSQL — sees writes immediately
- Fail-open: if PG down, JSON files still work

Directus API is used by:
- Frontend analytics dashboard
- Player authentication
- Admin operations

## Docker Compose

```yaml
services:
  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_DB: parlameme
      POSTGRES_USER: parlameme
      POSTGRES_PASSWORD: ${PG_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./sql/init.sql:/docker-entrypoint-initdb.d/01-init.sql
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U parlameme"]
      interval: 5s
      timeout: 3s
      retries: 5

  directus:
    image: directus/directus:11
    environment:
      SECRET: ${DIRECTUS_SECRET}
      ADMIN_EMAIL: ${DIRECTUS_ADMIN_EMAIL}
      ADMIN_PASSWORD: ${DIRECTUS_ADMIN_PASSWORD}
      DB_CLIENT: pg
      DB_HOST: postgres
      DB_PORT: "5432"
      DB_DATABASE: parlameme
      DB_USER: parlameme
      DB_PASSWORD: ${PG_PASSWORD}
      WEBSOCKETS_ENABLED: "true"
      CACHE_ENABLED: "true"
      CACHE_AUTO_PURGE: "true"
      CACHE_TTL: "5m"
    ports:
      - "8055:8055"
    volumes:
      - directus_uploads:/directus/uploads
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  pgdata:
  directus_uploads:
```

## Python Integration Module

### Dependency

```toml
# pyproject.toml
dependencies = [
    ...
    "psycopg[binary]>=3.2",
]
```

### Module: `py/engine/pg.py`

```python
"""PostgreSQL sync — dual-write mirror of JSON persistence.

Fail-open design: if PG is unavailable, log and continue.
JSON files remain source of truth.

Usage:
    from engine.pg import pg_sync
    
    # On startup (optional — disabled if PG_DSN not set)
    pg_sync.connect(os.environ.get("PG_DSN"))
    
    # After archiving a game
    pg_sync.sync_archive(archive)
    
    # After ledger append
    pg_sync.sync_ledger_entry(entry)
    
    # After rating update
    pg_sync.update_ratings(ratings_dict)
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)

try:
    import psycopg
except ImportError:
    psycopg = None  # type: ignore


class PgSync:
    """Dual-write mirror to PostgreSQL. Fail-open."""

    def __init__(self):
        self._dsn: str | None = None
        self._pool = None

    def connect(self, dsn: str | None) -> None:
        if not dsn:
            log.info("PG_DSN not set — PostgreSQL sync disabled")
            return
        if psycopg is None:
            log.warning("psycopg not installed — PostgreSQL sync disabled")
            return
        self._dsn = dsn
        log.info("PostgreSQL sync enabled: %s", dsn.split("@")[-1])

    @property
    def enabled(self) -> bool:
        return self._dsn is not None and psycopg is not None

    @contextmanager
    def _conn(self):
        if not self.enabled:
            yield None
            return
        try:
            with psycopg.connect(self._dsn) as conn:
                yield conn
        except Exception:
            log.exception("PostgreSQL connection failed")
            yield None

    def sync_archive(self, archive_dict: dict[str, Any]) -> None:
        with self._conn() as conn:
            if conn is None:
                return
            meta = archive_dict.get("metadata", {})
            conn.execute(
                """
                INSERT INTO game_archives
                    (session_id, game_type, rules_hash, seed,
                     players, player_count, winner, scores,
                     victory_condition, decision_count, archive_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO NOTHING
                """,
                (
                    meta.get("session_id", ""),
                    archive_dict.get("game_id", ""),
                    archive_dict.get("rules_hash", ""),
                    archive_dict.get("seed", 0),
                    json.dumps(archive_dict.get("players", [])),
                    len(archive_dict.get("players", [])),
                    meta.get("winner"),
                    json.dumps(meta.get("scores")) if meta.get("scores") else None,
                    meta.get("condition"),
                    len(archive_dict.get("decisions", [])),
                    json.dumps(archive_dict),
                ),
            )
            conn.commit()

    def sync_ledger_entry(self, entry) -> None:
        with self._conn() as conn:
            if conn is None:
                return
            conn.execute(
                """
                INSERT INTO ledger_entries
                    (seq, type, player, amount, ref, prev_hash, content_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (seq) DO NOTHING
                """,
                (entry.seq, entry.type, entry.player, entry.amount,
                 entry.ref, entry.prev_hash, entry.content_hash),
            )
            conn.commit()

    def update_player_stats(
        self, player_id: str, stats: dict[str, Any], rating: Any
    ) -> None:
        with self._conn() as conn:
            if conn is None:
                return
            conn.execute(
                """
                INSERT INTO player_profiles
                    (display_name, rating_mu, rating_rd, rating_vol, rating_tier,
                     games_played, wins, losses, win_rate, best_streak)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (display_name) DO UPDATE SET
                    rating_mu = EXCLUDED.rating_mu,
                    rating_rd = EXCLUDED.rating_rd,
                    rating_vol = EXCLUDED.rating_vol,
                    rating_tier = EXCLUDED.rating_tier,
                    games_played = EXCLUDED.games_played,
                    wins = EXCLUDED.wins,
                    losses = EXCLUDED.losses,
                    win_rate = EXCLUDED.win_rate,
                    best_streak = EXCLUDED.best_streak,
                    last_game_at = NOW()
                """,
                (
                    player_id,
                    round(rating.mu, 1),
                    round(rating.rd, 1),
                    rating.vol,
                    stats.get("tier", "Novice"),
                    stats.get("games", 0),
                    stats.get("wins", 0),
                    stats.get("losses", 0),
                    stats.get("win_rate", 0.0),
                    stats.get("best_streak", 0),
                ),
            )
            conn.commit()


# Module-level singleton
pg_sync = PgSync()
```

### Integration Points (hooks in existing code)

```python
# 1. server/sessions.py — after _maybe_archive()
from engine.pg import pg_sync
pg_sync.sync_archive(archive_to_dict(self._archive))

# 2. engine/ledger.py — after FileLedger.append()
from engine.pg import pg_sync
pg_sync.sync_ledger_entry(entry)

# 3. server/analytics.py — after _recompute()
from engine.pg import pg_sync
for pid, stats in self._player_stats.items():
    rating = self._ratings.get(pid, Rating())
    pg_sync.update_player_stats(pid, stats, rating)

# 4. server/app.py — on startup
from engine.pg import pg_sync
pg_sync.connect(os.environ.get("PG_DSN"))
```

## Migration from JSON → PostgreSQL

### Backfill Script

```python
"""Backfill PostgreSQL from existing JSON archives."""

from engine.archive import load_archive
from engine.pg import pg_sync
from server.analytics import load_all_archives

def backfill():
    pg_sync.connect("postgresql://parlameme:secret@localhost/parlameme")
    
    # 1. Archives
    for archive_dict in load_all_archives():
        pg_sync.sync_archive(archive_dict)
    
    # 2. Ledger
    from engine.ledger import FileLedger
    ledger = FileLedger()
    for entry in ledger.entries():
        pg_sync.sync_ledger_entry(entry)
    
    # 3. Player stats (recompute)
    from server.analytics import _cache
    _cache._recompute()
    # ... sync each player
```

## Directus Flows (Automation)

### 1. New Player Welcome Flow

```
Trigger: items.create on directus_users (action)
Operations:
  1. Create player_profiles entry with defaults
  2. Send welcome email (optional)
```

### 2. Leaderboard Refresh (CRON)

```
Trigger: Schedule (every hour)
Operations:
  1. SELECT display_name, rating_mu, games_played
     FROM player_profiles
     ORDER BY rating_mu DESC LIMIT 100
  2. Cache result in Directus cache
```

### 3. Game Completion Webhook

```
Trigger: items.create on game_archives (action)
Operations:
  1. Read archive data
  2. POST webhook to Discord/Slack with game summary
```

## API Endpoints (via Directus)

### Public (no auth)
- `GET /items/game_archives?sort=-completed_at&limit=20` — Recent games
- `GET /items/player_profiles?sort=-rating_mu&limit=50` — Leaderboard
- `GET /items/tournaments?filter[status][_eq]=registration` — Open tournaments

### Player (JWT required)
- `GET /users/me` — My profile
- `PATCH /users/me` — Update display name
- `GET /items/game_archives?filter[players][_contains]="myname"` — My games

### Engine (static token)
- `POST /items/game_archives` — Index new archive
- `PATCH /items/player_profiles/:id` — Update stats
- `POST /items/tournaments` — Create tournament

## Environment Variables

```env
# PostgreSQL
PG_DSN=postgresql://parlameme:${PG_PASSWORD}@localhost:5432/parlameme
PG_PASSWORD=change-me-in-production

# Directus
DIRECTUS_SECRET=random-256-bit-secret
DIRECTUS_ADMIN_EMAIL=admin@parlameme.io
DIRECTUS_ADMIN_PASSWORD=change-me
DIRECTUS_ENGINE_TOKEN=engine-static-token-for-api

# Game Engine (existing)
GAME_TOKEN_SECRET=hmac-secret-for-mcp-invites
```

## Rollout Plan

### Phase 1: PostgreSQL + Schema (Day 1)
- Docker Compose with PostgreSQL 17
- Run `sql/init.sql` to create tables
- Add `psycopg[binary]` to dependencies

### Phase 2: PgSync Module (Day 1)
- Implement `py/engine/pg.py`
- Hook into sessions.py, ledger.py, analytics.py
- Backfill existing archives
- Test: game → archive → PG row

### Phase 3: Directus Setup (Day 2)
- Add Directus to Docker Compose
- Let Directus introspect existing tables
- Configure roles: Admin, Player, Spectator, Engine
- Verify REST API works for all collections

### Phase 4: Player Auth (Day 2-3)
- Configure Directus auth (email/password)
- Create `player_profiles` on registration
- Integrate JWT validation in game engine
- Map Directus user → player_id in MCP

### Phase 5: Analytics Migration (Day 3)
- Point analytics dashboard to Directus REST API
- Remove in-memory StatsCache (or keep as hot cache)
- Add tournament persistence via PG tables

### Phase 6: Production Hardening
- Connection pooling (pgbouncer or psycopg pool)
- Backup strategy (pg_dump + JSON archive backup)
- Monitoring: Directus health + PG metrics
- Rate limiting on public API endpoints

## Subsystem Interaction Map

How each game engine subsystem interacts with PostgreSQL and Directus.

### 1. Game Sessions → Archives → Directus

```
Player action (deal/vote/message)
    → GameSession.execute_deal() / cast_vote()
        → GameRuntime mutates state (under async lock)
        → state.record_decision() → decisions tuple grows
        → check_victory() → if won:
            → create_archive(compiled, state, metadata)
            → save_archive() → data/archives/<sid>.json     [FILE]
            → pg_sync.sync_archive() → game_archives        [PG]
            → save_chronicle() → data/chronicles/<sid>.jsonl [FILE]
            → invalidate_cache() → triggers stats recompute

Directus sees:
    game_archives table (auto-introspected)
    → Admin UI: browse/filter/sort games
    → REST: GET /items/game_archives?sort=-completed_at
    → JSONB: full archive in archive_json column
    → GIN index: search by player name
```

### 2. Analytics → Player Profiles → Directus

```
StatsCache._recompute() (every 5 min or on invalidate)
    → load_all_archives() from JSON files
    → _compute_player_stats() → per-player aggregates
    → compute_ratings() → Glicko-2 ratings
    → pg_sync.bulk_update_player_stats()
        → UPSERT player_profiles for each player [PG]

Directus sees:
    player_profiles table
    → Admin UI: player management, rating adjustments
    → REST: GET /items/player_profiles?sort=-rating_mu
    → Views: leaderboard (pre-sorted, filtered)
    
    Also: game_type_summary view
    → REST: computed stats per game type
```

### 3. Ledger → Ledger Entries → Directus

```
FileLedger.append(type, player, amount, ref)
    → MemoryLedger creates hash-chained entry
    → _save() → data/ledger.json                [FILE]
    → pg_sync.sync_ledger_entry(entry)           [PG]

Directus sees:
    ledger_entries table
    → Admin UI: audit trail, transaction history
    → REST: GET /items/ledger_entries?filter[player][_eq]=alice
    → Read-only: engine writes, Directus only reads
    
    player_profiles.balance (cache)
    → Updated when stats recompute
```

### 4. Tournaments → Tournament Tables → Directus

```
TournamentSession state changes (register, start, report_result)
    → pg_sync.sync_tournament(state)
        → UPSERT tournaments table               [PG]
        → UPSERT tournament_matches per match     [PG]
    
    Match start → spawns GameSession
        → (follows flow #1 above)
    
    Match end → report_result()
        → updates standings
        → may complete tournament

Directus sees:
    tournaments table
    → Admin UI: manage tournament lifecycle
    → REST: GET /items/tournaments?filter[status][_eq]=registration
    
    tournament_matches table (FK to tournaments)
    → Admin UI: match details, scores
    → REST: GET /items/tournament_matches?filter[tournament_id][_eq]=<id>
    
    Related game archives:
    → session_id format: t-<tournament_id>-<match_id>
    → Queryable: GET /items/game_archives?filter[session_id][_starts_with]=t-<tid>
```

### 5. Replay → Archives → Directus

```
Replay request (Web UI or /replay command)
    → load_archive() from JSON file              [FILE]
    → replay(archive, compiled) → GameState
    → step-through with state diffs

Directus provides alternative access:
    → GET /items/game_archives/<id>?fields=archive_json
    → Full archive available via REST (no filesystem needed)
    → External tools can replay from Directus API
    
Chronicle (human-readable narrative):
    → Generated from archive on game end
    → Stored as JSONL: data/chronicles/<sid>.jsonl
    → NOT in PostgreSQL (derived data, regenerable)
    → Could be added as JSONB column if needed
```

### 6. MCP Agents → All Subsystems → Directus

```
MCP Agent request (POST /mcp/agent/<id>)
    → MCPServer routes to tool handler
    → Tool creates/joins session, executes actions
    → Each action follows flow #1 (game → archive → PG)
    
Future: Agent auth via Directus JWT
    → Agent registers as Directus user
    → Gets JWT token
    → MCP server validates JWT
    → Maps directus_user → player_id
    
Current: Token-based invite system (HMAC)
    → Independent of Directus auth
    → Migration path: HMAC invites → Directus JWT
```

### 7. Session Recovery → Active Sessions → Directus

```
Server startup:
    → pg_sync.connect() from PG_DSN
    → SessionStore.load() from data/sessions.json   [FILE]
    → recover_sessions() replays decisions
    
Future with PG:
    → Read from active_sessions table instead of JSON
    → pg_sync.upsert_session() on every state change
    → pg_sync.remove_session() on game end
    
Directus sees:
    active_sessions table
    → Admin UI: monitor running games
    → REST: GET /items/active_sessions?filter[status][_eq]=active
```

### Summary: Data Flow Matrix

| Event | JSON File | PostgreSQL Table | Directus API |
|-------|-----------|-----------------|--------------|
| Game ends | `archives/<sid>.json` | `game_archives` | `GET /items/game_archives` |
| Ledger append | `ledger.json` | `ledger_entries` | `GET /items/ledger_entries` |
| Stats recompute | (in-memory) | `player_profiles` | `GET /items/player_profiles` |
| Tournament change | (in-memory) | `tournaments` + `tournament_matches` | `GET /items/tournaments` |
| Session activity | `sessions.json` | `active_sessions` | `GET /items/active_sessions` |
| Chronicle | `chronicles/<sid>.jsonl` | (not synced) | (not available) |

### Claude Code Commands

| Command | What it queries | Sources |
|---------|----------------|---------|
| `/directus status` | Docker, PG health, table counts | Docker, psql, curl |
| `/directus query` | Any SQL | psql |
| `/analytics leaderboard` | Player ratings | PG view or Python |
| `/analytics player <name>` | Player detail | Python analytics |
| `/analytics head2head` | Matchup stats | Python analytics |
| `/tournament list` | Active tournaments | Python or PG |
| `/tournament status <id>` | Matches, standings | Python state |
| `/replay list` | Recent archives | Python or PG |
| `/replay show <id>` | Full archive JSON | File |
| `/replay verify <id>` | Integrity check | Python replay |
| `/replay chronicle <id>` | Narrative | JSONL file |
