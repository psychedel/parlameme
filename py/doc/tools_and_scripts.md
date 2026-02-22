# Tools, Scripts, and Entry Point

Utility layer: hunter-based tracing for game engine observation, operational scripts, and the application entry point.

## File Layout

| File | Purpose | Lines |
|------|---------|-------|
| `tools/trace.py` | Hunter tracing presets for real-time engine observation | ~406 |
| `scripts/analyze_game.py` | Game schema analyzer and validator | ~989 |
| `scripts/backfill_pg.py` | PostgreSQL sync backfill from JSON archives | ~80 |
| `scripts/play_tournament.py` | CLI tournament runner (agents vs bots) | ~617 |
| `main.py` | Application entry point (logging + NiceGUI startup) | ~26 |

---

## tools/trace.py — Hunter Tracing Presets

Real-time engine observation using the [hunter](https://python-hunter.readthedocs.io/) tracing library. Provides 7 presets that hook into engine internals at function-call granularity.

### Usage

```bash
# CLI mode — run preset against a game
uv run python -m tools.trace deals --game auction --players 3 --seed 42
uv run python -m tools.trace effects --depth 5
uv run python -m tools.trace phases --advance 3
uv run python -m tools.trace mcp
uv run python -m tools.trace flow --output /tmp/flow.jsonl
uv run python -m tools.trace errors
uv run python -m tools.trace snap --game auction --players 3

# Programmatic mode — attach to running code
from tools.trace import trace_deals, snapshot
t = trace_deals(); ...; t.stop()
```

### Presets

| Preset | Traces | Use case |
|--------|--------|----------|
| `deals` | `start_deal`, `respond_to_deal`, `_resolve_outcome`, `start_vote`, `cast_vote`, `_tally_votes`, `apply_effects` | Debug deal lifecycle |
| `effects` | All functions in `engine.runtime.effects` up to configurable depth | Debug effect application chain |
| `phases` | `advance_phase`, `_advance_one`, `_run_entry_effects`, `_should_skip`, `_cleanup_phase`, `run_setup`, `check_victory` | Debug phase transitions and cascading |
| `mcp` | 18 MCP handler functions (`handle_request`, `_tool_act`, `_exec_deal`, etc.) | Debug MCP request flow |
| `flow` | Structured JSONL output of all engine events with timestamps and key args | Post-analysis and visualization |
| `errors` | `ErrorSnooper` on `engine.*` + `mcp.*` modules with backlog | Find swallowed exceptions |
| `snap` | No tracing — one-shot god-view state dump | Quick state inspection |

### FlowLogger

The `flow` preset writes structured JSONL records for post-analysis:

```json
{"ts": 1708529400.123, "fn": "start_deal", "mod": "engine.runtime.core", "depth": 3, "deal_id": "sealed_bid", "actor_id": "alice"}
```

Tracked function arguments (safe subset): `deal_id`, `vote_id`, `player_id`, `actor_id`, `option`, `response`, `phase`, `speech_act_id`, `ch_id`.

### snapshot()

God-view state dump with no visibility filtering. Outputs markdown-formatted text showing:
- Phase, round, status
- All game variables
- All entities (resources, attrs, groups, active/dead)
- Pending deals and votes (with cast votes)
- Relations and groups

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--game/-g` | auction | Game to trace |
| `--players/-n` | game's min_players | Player count |
| `--seed/-s` | 42 | RNG seed |
| `--advance/-a` | 1 | Phases to advance after setup |
| `--depth` | varies | Hunter depth limit |
| `--output/-o` | stderr | Output file |

---

## scripts/analyze_game.py — Game Schema Analyzer

Comprehensive game schema analyzer that validates and documents compiled games. Generates reports on:
- Phase structure and allowed actions per phase
- Deal parameter schemas and outcome trees
- Vote mechanics and resolution
- Role definitions and distributions
- Resource economy (sources and sinks)
- Victory condition expressions
- Channel configurations

Used for development documentation and game design validation.

---

## scripts/backfill_pg.py — PostgreSQL Backfill

Idempotent script that reads JSON archives from `data/archives/` and inserts them into PostgreSQL tables (requires `PG_DSN` environment variable). Re-runnable — skips already-inserted records.

```bash
cd py && PG_DSN=postgresql://parlameme:parlameme-dev@localhost:5432/parlameme uv run python scripts/backfill_pg.py
```

---

## scripts/play_tournament.py — CLI Tournament Runner

Runs a complete tournament from the command line. Creates a tournament, registers players (mix of AI agents and bots), plays all matches, and reports results. Used for testing tournament flows and benchmarking strategies without the web UI.

---

## main.py — Entry Point

Minimal entry point that configures logging and starts the NiceGUI server.

```python
def setup_logging():
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    # Quieten uvicorn.access and nicegui loggers
    ...

if __name__ == "__main__":
    setup_logging()
    from server.app import main
    main()
```

### Logging Configuration

- Level from `LOG_LEVEL` env var (default: `INFO`)
- Format: `%(asctime)s %(levelname)-8s %(name)-30s %(message)s`
- `uvicorn.access` and `nicegui` set to `WARNING` to reduce noise
- Output to stderr

### Startup

`server.app.main()` handles:
- NiceGUI app initialization
- Route registration (game, replay, analytics, tournaments, workshop, agent play)
- MCP server creation
- Tournament recovery (`load_tournaments()`)
- Session persistence recovery
- Port from `PORT` env var (default: 8080)
