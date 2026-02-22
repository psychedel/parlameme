# Analytics Gap Analysis Report

## Overview

This report compares the analytics capabilities available through three channels:
1. **MCP** (Model Context Protocol) — AI agent tools via JSON-RPC
2. **HTTP API** — REST endpoints at `/api/history/`, `/api/platform/`, `/api/tournaments/`
3. **Browser UI** — `history.html` ClojureScript SPA

Data was gathered after playing 4 game types across these channels:
- **Duel Tournament** (grand-cup): 4 MCP agents, round-robin, 6 matches
- **Auction**: 2 MCP + 1 Playwright player, 6 rounds
- **Election Race**: 2 MCP + 2 Playwright players, 4 rounds
- **Resistance**: 3 MCP + 2 Playwright players, 5 missions

### Game Results

| Game | Winner | Score | Players |
|------|--------|-------|---------|
| Duel Tournament | agent-epsilon | 7 pts (2W 1L) | alpha 5, gamma 4, beta 0 |
| Auction | host-agent-alpha | 650 pts | delta 225, beta 195 |
| Election Race | host-agent-alpha | 35.95 pts | delta 32.75, gamma 26.15, epsilon-ui 24.35 |
| Resistance | Resistance team | 3-2 missions | alpha+gamma+epsilon-ui vs beta+delta |

---

## Feature Comparison Matrix

| Feature | MCP Tools | HTTP API | Browser UI | Notes |
|---------|-----------|----------|------------|-------|
| **Game History** | `my_game_history` | `GET /api/history/flows` | Sessions tab | All three work. MCP scoped to agent; API/UI show all. |
| **Player Stats** | `my_stats` | `GET /api/history/players/:id/stats` | Leaderboard tab | MCP: own stats only. API: any player. UI: global leaderboard. |
| **Glicko Rating** | In `my_stats` | In leaderboard response | Rating badges in UI | All channels show rating + RD. UI adds tier labels (Master, Advanced, etc). |
| **Game Replay (archive)** | `get_game_replay` | `GET /api/archive/full/:id` | - | MCP returns full archive. API returns archive+events. UI has no archive download. |
| **State at Event N** | - | `GET /api/history/flows/:id/state-at/:n` | Replay slider (broken) | HTTP API has it. UI has slider but state-at endpoint fails on some games. MCP lacks this entirely. |
| **Event Timeline** | `recent_events` (in-game only) | `GET /api/history/flows/:id/events` | Timeline tab | MCP only while in game. API/UI show full history. UI adds search, filters by type/category/actor, group-by-round. |
| **Tournament Standings** | `get_standings` | `GET /api/tournaments/:id` | Tournaments tab | All three work. UI adds round-robin matrix visualization. |
| **Tournament Analytics** | - | `GET /api/history/tournaments/:id/analytics` | Tournament > Analytics tab | Rating impact, upsets. Not available via MCP. |
| **Platform Stats** | - | `GET /api/platform/stats`, `GET /api/history/stats` | Dashboard > Overview | Total games, players, completion rates. Not available via MCP. |
| **Activity Trends** | - | `GET /api/history/analytics/activity` | Dashboard > Activity tab | Games/day, players/day charts. Not available via MCP. |
| **Game Type Breakdown** | - | In `/api/history/stats` response | Dashboard > Game Types tab | Per-type counts, completion rates. Not available via MCP. |
| **Game Balance** | - | `GET /api/history/analytics/game-balance/:type` | - | First-player win rate, avg game length. API only. |
| **Deal Analytics** | - | Derivable from events | Session > Deals tab | Acceptance rate ring chart, deal type bar chart. UI-exclusive visualization. |
| **Vote Analytics** | - | Derivable from events | Session > Votes tab | Vote breakdowns. UI-exclusive visualization. |
| **Resource Graphs** | - | Derivable from state-at | Session > Resources tab | Redirects to Replay tab. Partially implemented. |
| **Head-to-Head** | - | `GET /api/history/players/h2h` (broken) | Head-to-Head tab | UI works well: rating comparison, win series chart, style comparison, by-game-type. API endpoint exists but has param validation issues. MCP lacks this. |
| **Session Compare** | - | - | Compare tab (broken) | Side-by-side session comparison. UI exists but has keyword serialization bug (`:session-id` instead of `session-id` in API calls). |
| **Leaderboard** | - | `GET /api/platform/leaderboard` | Leaderboard tab | API returns sorted list. UI adds game-type filtering, min-games threshold, sort-by options (rating/win-rate/games/streak). |
| **Public Replays** | `list_public_replays` | `GET /api/archive/list` | - | MCP and API both list archives. UI doesn't have a dedicated archive browser. |
| **Player Analytics** | - | `GET /api/history/players/:id/analytics` | - | Detailed per-player analytics. API only. |
| **Merkle Verification** | - | `GET /api/archive/flow/:id/verify` | - | On-chain verification. API only. |
| **Escrow Balance** | `escrow_balance` (in-game) | `GET /api/escrow/balance/:player` | - | MCP: own balance. API: any player. |
| **OpenAPI Docs** | - | `GET /openapi.json`, `/api-docs` | - | Swagger UI for API exploration. |

---

## Channel Strengths

### MCP (Agent-Facing)
- **Best for**: In-game decision making, agent-scoped queries
- **Unique**: Dynamic tool schema generation based on current game phase
- **Strengths**: 31 lobby tools, contextual game tools, invite/activation flow
- **Weaknesses**: No cross-player analytics, no platform-wide stats, no historical deep-dives

### HTTP API (Developer/Integration-Facing)
- **Best for**: Programmatic access, data integration, verification
- **Unique**: Merkle verification, archive anchoring, escrow operations, game balance analytics
- **Strengths**: ~67 endpoints, comprehensive coverage, OpenAPI docs
- **Weaknesses**: Some endpoints have parameter validation bugs (h2h), no WebSocket for real-time updates via API

### Browser UI (Human-Facing)
- **Best for**: Visual exploration, pattern recognition, comparative analysis
- **Unique**: Head-to-head with win series charts, deal type visualizations, round-robin matrix, rating tier badges
- **Strengths**: 6 main tabs with sub-views, filtering/sorting, CSS charts (no JS chart libraries)
- **Weaknesses**: Compare tab broken (keyword serialization), Replay slider fails on some games, Resources tab underdeveloped, no archive download/export

---

## Key Gaps

### 1. MCP Missing Platform Analytics
MCP agents cannot query platform-wide statistics, activity trends, game balance, or head-to-head comparisons. An agent can only see its own stats and the games it participated in. This limits agent ability to reason about meta-strategy.

**Recommendation**: Add MCP tools: `platform_stats`, `player_head_to_head`, `game_balance_report`.

### 2. State Replay Inconsistent
- HTTP API has `state-at/:n` but it fails on some games (returns "Action not allowed in phase" errors during replay)
- UI Replay slider exists but depends on the same broken endpoint
- MCP has no state-at-point capability at all

**Root Cause**: The replay endpoint tries to re-execute events from the archive. Games completed via `complete-game-and-broadcast!` (manual completion) may have archives that don't replay cleanly because the archive contains events that were executed in a different order or with state manipulations.

**Recommendation**: Fix deterministic replay for all completion paths; add `get_state_at_event` MCP tool.

### 3. Compare & H2H Bugs
- Compare tab sends keyword-prefixed IDs (`:session-id`) to the API instead of plain strings
- HTTP H2H endpoint (`/api/history/players/h2h`) has parameter validation issues (returns "Invalid parameters: nil")
- Browser H2H works correctly (likely uses a different code path or different parameter format)

**Recommendation**: Fix CLJS `name` call on flow-id keywords in compare view; fix API h2h param coercion.

### 4. Expression Evaluator Inconsistency
During gameplay, several bugs surfaced due to two different expression evaluators:
- `flow/expr.cljc` — foundation layer, no dot notation support
- `v3/runtime/expr.cljc` — game layer, has dot notation

The `[:each]` effect handler (in `flow/effects.cljc`) uses the foundation evaluator, but game definitions (auction, resistance) use dot notation expressions in their `[:each]` filters. This caused:
- Auction finale crash: `(< :p.credit 500)` — `:p.credit` resolves to keyword instead of number
- Resistance propose-team NPE: `(and (alive?) :is-leader)` — bare keyword evaluation failure

**Recommendation**: Unify expression evaluators or ensure `[:each]` uses the v3 evaluator when running v3 games.

### 5. No Export/Download
None of the three channels offer a way to export analytics data in a standard format (CSV, JSON download). The UI has no "export" button, the API returns Transit format only (not JSON), and MCP returns Transit-encoded responses.

**Recommendation**: Add JSON content negotiation to API endpoints; add export buttons to UI.

---

## Bugs Found During Testing

| Bug | Location | Severity | Status |
|-----|----------|----------|--------|
| MCP `target` param stripped from deal args | `mcp/server.clj:534` | High | **Fixed** |
| `[:each]` effect uses wrong expression evaluator | `flow/effects.cljc:555` | High | Open |
| Auction UI crash on `[:game :highest-bidder]` entity | CLJS rendering | Medium | Open |
| Election UI "Doesn't support name:" on deal accept | CLJS rendering | Medium | Open |
| Resistance `propose-team` NPE on bare keyword expr | `flow/expr.cljc:169` | High | Open |
| Compare tab keyword serialization in API calls | `history/views.cljs` | Low | Open |
| H2H API param validation failure | `server.clj` history routes | Low | Open |
| Replay state-at fails on manually completed games | `archive/store.clj` | Medium | Open |
| Tournament Players column shows 0 | UI data binding | Low | Open |
| Win rate shows 0% for all game types in Dashboard | Stats computation | Low | Open |

---

## Summary

The three analytics channels serve distinct audiences well but have significant gaps between them:

- **MCP** is narrowly scoped to agent self-service — good for in-game decisions but blind to the broader ecosystem
- **HTTP API** is the most complete data source (~67 endpoints) but has some broken endpoints and returns Transit only
- **Browser UI** has the richest visualizations (H2H charts, tournament matrices, deal breakdowns) but several features are broken or incomplete

The most impactful improvements would be:
1. Fix the expression evaluator inconsistency (affects gameplay, not just analytics)
2. Add platform analytics to MCP (enables smarter AI agents)
3. Fix Compare and Replay in the browser UI (they exist but don't work reliably)
4. Add JSON content negotiation to the API (enables third-party integrations)
