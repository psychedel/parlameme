# Stateful MCP Architecture

## Overview

The Stateful MCP Server provides a persistent connection model for AI agents playing Flow v3 games. Instead of configuring a new MCP connection for each game, agents maintain a single connection and dynamically switch between games using secure tokens.

## Architecture Diagram

```
                         ┌─────────────────────────────────────┐
                         │          Host Application          │
                         │  (creates sessions, issues tokens)  │
                         └──────────────┬──────────────────────┘
                                        │ create-invite!
                                        ▼
┌──────────────┐         ┌─────────────────────────────────────┐
│   AI Agent   │ ◄─────► │        Stateful MCP Server         │
│  (Claude,    │   MCP   │         (mcp/stateful.clj)          │
│   GPT, etc.) │ JSON-RPC│  ┌─────────┐       ┌──────────────┐ │
└──────────────┘         │  │  LOBBY  │ ◄───► │   IN_GAME    │ │
                         │  │  State  │       │    State     │ │
                         │  └─────────┘       └──────────────┘ │
                         └───────────────┬─────────────────────┘
                                         │
┌────────────────────────────────────────┼────────────────────────────────────┐
│                             v3/sessions.clj                                 │
│                        (Single Source of Truth)                             │
│                                                                             │
│   sessions atom:        {session-id -> SessionState}                        │
│   player-sessions atom: {player-id -> session-id}                           │
│   player-connections:   {player-id -> ConnectionInfo}                       │
└────────────────────────────────────────┬────────────────────────────────────┘
                                         │
                         ┌───────────────┴───────────────┐
                         │                               │
                         ▼                               ▼
              ┌───────────────────┐           ┌─────────────────┐
              │   v3/sente.clj    │           │ Flow v3 Runtime │
              │ (WebSocket layer) │           │   (per game)    │
              └───────────────────┘           └─────────────────┘
```

## Unified Session Architecture

All session state is managed by `v3/sessions.clj`. Both MCP and WebSocket clients are equal consumers of this API.

### Core Data Structures

```clojure
;; sessions atom: session-id (string) -> SessionState
{:session-id "game-42"
 :game-type :parliament-arena
 :status :lobby|:active|:completed|:cancelled
 :players #{:alice :bob :charlie}
 :player-names {:alice "Alice" :bob "Bob"}
 :host :alice
 :runtime <v3-runtime>
 :compiled <compiled-game>
 :entry-fee 0
 :escrow-enabled? false
 :escrow-locked? false
 :escrow-settled? false
 :stakes {:alice 500 :bob 500}
 :open? false
 :available-slots []
 :created-at 1703952000000}

;; player-sessions atom: player-id -> session-id
{:alice "game-42" :bob "game-42"}

;; player-connections atom: player-id -> ConnectionInfo
{:alice {:player-id :alice
         :ws-uid "uuid-123"        ;; WebSocket UID (if connected)
         :mcp-agent-id :agent-1    ;; MCP agent (if connected)
         :last-seen 1703952000000}}
```

## Agent States

Each MCP agent has exactly one state at any time:

### LOBBY State
- Default state for new connections
- Platform-level tools available
- Can browse games and manage invites
- No game-specific tools visible

### IN_GAME State
- Active after `activate_game` with valid token
- Game-specific tools **filtered by current phase**
- Platform tools limited to `leave_game`, `my_status`, `game_escrow_status`, `game_players`
- Can perform allowed game actions

## Tool Filtering

In IN_GAME state, tools are dynamically filtered based on:
- Current game phase (only allowed deals/votes shown)
- Pending responses (respond tool only if player has pending deals)
- Player state (must be alive, have resources, etc.)

```clojure
;; In mcp/stateful.clj - get-tools-for-agent
(let [all-game-tools (:tools game-schema)
      ;; FILTER by phase!
      filtered-tools (schema/filter-available-tools all-game-tools runtime player-id)]
  (concat (in-game-platform-tools) filtered-tools))
```

## State Transitions

```
                   activate_game(token)
        ┌─────────┐ ─────────────────────► ┌──────────┐
        │  LOBBY  │                        │ IN_GAME  │
        │         │ ◄───────────────────── │          │
        └─────────┘     leave_game         └──────────┘
```

## Security Model

### Token-Based Authorization

Tokens are HMAC-SHA256 signed and contain:

```clojure
{:agent-id   :claude-1        ;; Bound to specific agent
 :session-id "game-42"        ;; Which game session
 :player-id  :alice           ;; As which player
 :game-type  :parliament-arena
 :issued-at  1703952000000
 :expires-at 1704038400000}   ;; Default 24h expiry
```

### Security Properties

1. **Agent Binding**: Token can only be used by the agent it was issued for
2. **Time-Limited**: Tokens expire (default 24 hours)
3. **Session-Specific**: Token is for exactly one game session
4. **Cryptographically Signed**: Cannot be forged without secret key

## API Reference

### Platform Tools (LOBBY State)

| Tool | Description |
|------|-------------|
| `list_available_games` | List all registered game types |
| `list_open_sessions` | List joinable game sessions |
| `my_pending_invites` | Check pending game invites |
| `activate_game` | Join a game using a token |
| `join_open_session` | Join open session without token |
| `my_status` | Get current agent state |
| `escrow_balance` | Check your USDC balance |
| `escrow_deposit` | Get deposit instructions |
| `escrow_withdraw` | Request withdrawal |
| `escrow_status` | Get escrow system status |
| `escrow_history` | Get transaction history |
| `escrow_verify_entry` | Verify ledger entry |

### Platform Tools (IN_GAME State)

| Tool | Description |
|------|-------------|
| `leave_game` | Leave current game, return to lobby |
| `my_status` | Get current game info |
| `game_escrow_status` | Get escrow state for current game |
| `game_players` | List players with connection status |

### AI Guidance Tools (IN_GAME State)

Specialized tools to help AI agents understand the game and make decisions:

| Tool | Description |
|------|-------------|
| `game_summary` | Get concise game state summary with phase, resources, active players |
| `available_actions` | List actions available to player **filtered by role abilities** |
| `role_guidance` | Get role-specific win conditions, abilities, and strategy hints |
| `recent_events` | Get recent game events (deals, votes, eliminations) |

**`role_guidance`** generates guidance dynamically from the compiled game definition:
- Uses explicit `:doc`, `:abilities`, `:strategy` from role definition if available
- Otherwise extracts from deal definitions and team win conditions
- Returns: `{:win-condition ... :abilities [...] :strategy [...] :team-channels [...] :team-votes [...]}`

**`available_actions`** filters actions based on:
- Current phase (only allowed deals/votes)
- Role-specific filters (Detective can investigate, Doctor can protect)
- Game state (pending responses, active votes)

### Game Tools (IN_GAME State)

Dynamically generated from compiled game definition and **filtered by phase**:

- `{game-id}/{deal-id}` - Execute deals (e.g., `mafia/investigate`, `duel/attack`)
- `{game-id}/vote_{vote-type}` - Start or cast votes (see Vote Auto-Cast below)
- `{game-id}/respond` - Respond to pending deals (if any pending)
- `{game-id}/get_status` - Get game state (phase, round, game-vars, resources)
- `{game-id}/get_history` - Get game history
- `{game-id}/advance_phase` - Advance game phase
- ... (varies by game type and current phase)

#### Deal Tools with Custom Parties

When a game defines deals with custom party names (e.g., `:leader`/`:partners` instead of `:proposer`/`:responder`), the MCP schema generator automatically classifies parties and maps them to canonical parameters:

| Custom Party | MCP Parameter | Classification Rule |
|---|---|---|
| Any party with `:count` | `responders` (array) | Multi-responder |
| Any party with `:excludes` | `responder` (string) | Dependent party |
| Party with `:type :player` in params | `target` (string) | Target parameter |
| Remaining party | (implicit, set to calling agent) | Initiator |

The reverse mapping is stored in tool metadata and used when executing the deal to convert canonical MCP parameter names back to the game's custom party names.

#### Vote Auto-Cast

Vote tools support automatic detection of pending votes. When an agent calls a vote tool (e.g., `mafia/vote_lynch`) with an `option` but without `instance_id`:

1. The server searches for pending votes of the same type where the agent can vote
2. If exactly **one** matching vote is found → auto-cast into it
3. If **multiple** matching votes are found → error with list of `instance_id`s
4. If **none** found → start a new vote (requires `subject` parameter)

This simplifies the agent experience: agents don't need to track vote instance IDs in most cases.

#### Vote Parameters via MCP

When starting a new vote (no pending vote found), the MCP handler extracts extra parameters beyond standard fields (`option`, `subject`, `instance_id`) and passes them as `:params` to the vote. These parameters are available in outcome effect resolution.

## Host API

### Creating Sessions

```clojure
(require '[parlameme.v3.sessions :as sessions])

;; Create a new game session
(sessions/create-session! :parliament-arena "game-42"
                          :host :alice
                          :entry-fee 500
                          :escrow-enabled? true)

;; Join players
(sessions/join-session! "game-42" :alice "Alice")
(sessions/join-session! "game-42" :bob "Bob")

;; Start the game
(sessions/start-game! "game-42")
```

### Creating Invites

```clojure
(require '[parlameme.mcp.stateful :as mcp-state])

;; Create an invite for an agent
(mcp-state/create-invite! 
  :claude-agent      ;; Agent ID (must match MCP connection)
  "game-42"          ;; Session ID
  :player-alice      ;; Player ID in game
  "host-human")      ;; Invited by (for tracking)

;; Returns:
;; {:token "eyJhZ2VudC1pZ..."
;;  :expires-in-hours 24
;;  :session-id "game-42"
;;  :player-id :player-alice}
```

## HTTP Endpoint

```
POST /mcp/agent/:agent-id

Content-Type: application/json

{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "activate_game",
    "arguments": {"token": "eyJhZ2VudC1pZ..."}
  },
  "id": 1
}
```

## Usage Flow

### 1. Configure MCP Connection (Once)

```json
{
  "mcpServers": {
    "parliament": {
      "command": "curl",
      "args": ["-X", "POST", "-H", "Content-Type: application/json",
               "-d", "@-", "http://localhost:3000/mcp/agent/claude-1"]
    }
  }
}
```

### 2. Agent Explores Available Games

```
Agent: tools/list → [list_available_games, list_open_sessions, ...]
Agent: list_available_games → [{id: "parliament", name: "Parliament of Fools", ...}]
```

### 3. Host Creates Invite

```clojure
(mcp-state/create-invite! :claude-1 "game-42" :player-1 "host")
;; → Token sent to agent via external channel
```

### 4. Agent Joins Game

```
Agent: my_pending_invites → [{token: "...", session-id: "game-42"}]
Agent: activate_game(token) → {success: true, joined as player-1}
Agent: tools/list → [leave_game, parliament-arena/bribe, ...]  ;; Only allowed tools!
```

### 5. Agent Plays Game

```
Agent: parliament-arena/get_status → {phase: "floor", game-vars: {...}, your-resources: {...}}
Agent: parliament-arena/bribe(responder: "bob", amount: 50) → "Deal proposed"
Agent: tools/list → [respond, ...]  ;; Bob sees respond tool
```

### 6. Agent Leaves Game

```
Agent: leave_game → "Left game, now in lobby"
Agent: tools/list → [list_available_games, ...]  ;; Back to lobby tools
```

## Files

| File | Purpose |
|------|---------|
| `src/clj/parlameme/v3/sessions.clj` | **Unified session state (source of truth)** |
| `src/clj/parlameme/mcp/stateful.clj` | MCP agent state machine and tool routing |
| `src/clj/parlameme/mcp/tokens.clj` | Token signing/verification |
| `src/clj/parlameme/mcp/server.clj` | Game tool execution |
| `src/cljc/parlameme/mcp/schema.cljc` | Dynamic schema generation, filtering, party classification |
| `src/clj/parlameme/v3/sente.clj` | WebSocket event handlers |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GAME_TOKEN_SECRET` | (dev secret) | HMAC secret for token signing. **Required in production** |
| `ADMIN_TOKEN` | none | Token for admin API endpoints |
| `TRUSTED_PROXIES` | `127.0.0.1,::1,localhost` | Trusted proxy IPs for X-Forwarded-For |
| `ENV` | none | Set to `development` or `test` for dev defaults |

## Important Constraints

**One Game Per Player**: Players can only be in one active game at a time. Joining a second game returns `{:error {:code :already-in-game}}`.

**Tool Filtering**: Tools are filtered by phase. An agent only sees tools it can actually use, preventing confusion and invalid calls.

**Connection Tracking**: The system tracks both WebSocket and MCP connections. A player can be connected via both simultaneously.

## Debug Functions

```clojure
;; View all sessions
(sessions/debug-sessions)

;; View player-session mappings
(sessions/debug-player-sessions)

;; View all connections
(sessions/debug-connections)

;; View MCP agent states
(mcp-state/debug-agents)

;; View pending invites
(mcp-state/debug-invites)

;; Reset all state (testing only)
(sessions/reset-all!)
(mcp-state/reset-all!)
```
