-- Parlameme PostgreSQL schema
-- Run via: docker-entrypoint-initdb.d/01-init.sql (auto on first start)
-- Or manually: psql -U parlameme -d parlameme -f sql/init.sql

-- ============================================================
-- Player profiles (game-specific data alongside Directus auth)
-- ============================================================

CREATE TABLE IF NOT EXISTS player_profiles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    directus_user   UUID,               -- FK added after Directus creates its tables
    display_name    VARCHAR(64) NOT NULL,
    -- Glicko-2 ratings
    rating_mu       FLOAT NOT NULL DEFAULT 1500.0,
    rating_rd       FLOAT NOT NULL DEFAULT 350.0,
    rating_vol      FLOAT NOT NULL DEFAULT 0.06,
    rating_tier     VARCHAR(20) NOT NULL DEFAULT 'Novice',
    -- Aggregate stats (updated after each game)
    games_played    INT NOT NULL DEFAULT 0,
    wins            INT NOT NULL DEFAULT 0,
    losses          INT NOT NULL DEFAULT 0,
    win_rate        FLOAT NOT NULL DEFAULT 0.0,
    best_streak     INT NOT NULL DEFAULT 0,
    -- Ledger balance cache (true balance is in hash-chain JSON)
    balance         INT NOT NULL DEFAULT 0,
    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_game_at    TIMESTAMPTZ,
    UNIQUE(display_name)
);

CREATE INDEX IF NOT EXISTS idx_profiles_rating ON player_profiles(rating_mu DESC);

-- ============================================================
-- Game archives (index over JSON archive files)
-- ============================================================

CREATE TABLE IF NOT EXISTS game_archives (
    id                  SERIAL PRIMARY KEY,
    session_id          VARCHAR(128) NOT NULL UNIQUE,
    game_type           VARCHAR(64) NOT NULL,
    rules_hash          VARCHAR(32) NOT NULL,
    seed                BIGINT NOT NULL,
    -- Participants
    players             JSONB NOT NULL DEFAULT '[]',
    player_count        SMALLINT NOT NULL DEFAULT 0,
    -- Outcome
    winner              VARCHAR(64),
    scores              JSONB,
    victory_condition   VARCHAR(64),
    -- Stats
    decision_count      INT NOT NULL DEFAULT 0,
    round_count         INT NOT NULL DEFAULT 0,
    -- Full archive (optional — for API without filesystem)
    archive_json        JSONB,
    -- Timestamps
    completed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_archives_game ON game_archives(game_type);
CREATE INDEX IF NOT EXISTS idx_archives_winner ON game_archives(winner);
CREATE INDEX IF NOT EXISTS idx_archives_time ON game_archives(completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_archives_players ON game_archives USING GIN(players);

-- ============================================================
-- Ledger entries (mirror of hash-chain JSON)
-- ============================================================

CREATE TABLE IF NOT EXISTS ledger_entries (
    seq             INT PRIMARY KEY,
    type            VARCHAR(32) NOT NULL,
    player          VARCHAR(64) NOT NULL,
    amount          INT NOT NULL,
    ref             VARCHAR(256) DEFAULT '',
    prev_hash       VARCHAR(64) NOT NULL,
    content_hash    VARCHAR(64) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ledger_player ON ledger_entries(player);
CREATE INDEX IF NOT EXISTS idx_ledger_type ON ledger_entries(type);

-- ============================================================
-- Tournaments (persistent — replaces in-memory store)
-- ============================================================

CREATE TABLE IF NOT EXISTS tournaments (
    id                  VARCHAR(128) PRIMARY KEY,
    tournament_type     VARCHAR(32) NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'registration',
    host                VARCHAR(64) NOT NULL,
    name                VARCHAR(256) NOT NULL,
    game_type           VARCHAR(64) NOT NULL,
    -- Config
    min_participants    SMALLINT NOT NULL DEFAULT 2,
    max_participants    SMALLINT NOT NULL DEFAULT 16,
    match_size          SMALLINT NOT NULL DEFAULT 2,
    seed                BIGINT NOT NULL DEFAULT 42,
    -- Participants
    participants        JSONB NOT NULL DEFAULT '[]',
    -- Result
    winner              VARCHAR(64),
    standings           JSONB,
    -- Timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS tournament_matches (
    id                  VARCHAR(128) PRIMARY KEY,
    tournament_id       VARCHAR(128) NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    participants        JSONB NOT NULL DEFAULT '[]',
    round               SMALLINT NOT NULL DEFAULT 1,
    stage               VARCHAR(32) NOT NULL DEFAULT 'main',
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',
    winner              VARCHAR(64),
    scores              JSONB,
    session_id          VARCHAR(128),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_matches_tournament ON tournament_matches(tournament_id);
CREATE INDEX IF NOT EXISTS idx_matches_status ON tournament_matches(status);

-- ============================================================
-- Active sessions (replaces data/sessions.json)
-- ============================================================

CREATE TABLE IF NOT EXISTS active_sessions (
    session_id      VARCHAR(128) PRIMARY KEY,
    game_type       VARCHAR(64) NOT NULL,
    players         JSONB NOT NULL DEFAULT '[]',
    seed            BIGINT NOT NULL DEFAULT 42,
    decisions       JSONB NOT NULL DEFAULT '[]',
    current_phase   VARCHAR(64),
    current_round   SMALLINT DEFAULT 1,
    status          VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON active_sessions(status);

-- ============================================================
-- Useful views for Directus / analytics
-- ============================================================

CREATE OR REPLACE VIEW leaderboard AS
SELECT
    display_name,
    rating_mu AS rating,
    rating_tier AS tier,
    games_played,
    wins,
    losses,
    win_rate,
    best_streak
FROM player_profiles
WHERE games_played > 0
ORDER BY rating_mu DESC;

CREATE OR REPLACE VIEW recent_games AS
SELECT
    ga.session_id,
    ga.game_type,
    ga.players,
    ga.player_count,
    ga.winner,
    ga.victory_condition,
    ga.decision_count,
    ga.completed_at
FROM game_archives ga
ORDER BY ga.completed_at DESC
LIMIT 50;

CREATE OR REPLACE VIEW game_type_summary AS
SELECT
    game_type,
    COUNT(*) AS games_played,
    COUNT(DISTINCT winner) AS unique_winners,
    AVG(decision_count)::NUMERIC(6,1) AS avg_decisions,
    AVG(player_count)::NUMERIC(3,1) AS avg_players
FROM game_archives
GROUP BY game_type
ORDER BY games_played DESC;
