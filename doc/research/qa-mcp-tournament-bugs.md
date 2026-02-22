# QA Testing: MCP Tournament Flow - Bugs Found

**Date:** 2026-02-02  
**Test:** Full round-robin tournament with 3 MCP agents (player1, player2, player3)

## Summary

Successfully completed tournament with all 3 matches, but found multiple issues with state management and auto-spawning.

## Bugs Found

### BUG #1: MCP Schema Parameter Naming Inconsistency
- **Severity:** Low
- **Description:** `create_tournament` tool uses `tournament_type` parameter, but documentation and intuition suggest `format` or `type`
- **Impact:** Confusion when creating tournaments via MCP
- **Location:** `src/clj/parlameme/tournament/mcp.clj`

### BUG #2: Tournament Completion Callback Not Registered on Server Start
- **Severity:** High
- **Description:** After server restart/recovery, tournament system's completion callback isn't registered until `t-sessions/init!` is called
- **Impact:** Match results don't auto-report to tournament; tournament shows 0 completed matches
- **Workaround:** Manual `(t-sessions/init!)` call
- **Location:** `src/clj/parlameme/tournament/sessions.clj`

### BUG #3: Agent State Machine - Stuck in Completed Game
- **Severity:** High
- **Description:** After match completion, agents remain in `:in-game` state instead of transitioning back to `:in-tournament`
- **Impact:** Players can't be auto-matched for next tournament match
- **Location:** `src/clj/parlameme/mcp/stateful.clj`, `src/clj/parlameme/tournament/sessions.clj`

### BUG #4: leave_game Returns to Lobby Instead of Tournament
- **Severity:** Medium
- **Description:** When agent calls `leave_game` after tournament match, they go to `:lobby` state instead of `:in-tournament`
- **Impact:** Agent loses tournament context, can't continue tournament
- **Location:** `src/clj/parlameme/mcp/stateful.clj`

### BUG #5: Cannot Re-register for Active Tournament
- **Severity:** Medium
- **Description:** After leaving game and going to lobby, attempting to re-register for tournament fails with membership validation error
- **Impact:** Agent stuck outside tournament they're supposed to be in
- **Location:** Tournament membership validation logic

### BUG #6: Match Session Not Auto-Created
- **Severity:** Critical
- **Description:** Match 2 session ID was registered in `match-session-ids` but actual game session wasn't created. `spawn-available-matches!` didn't run after match 1 completion.
- **Impact:** Players can't join scheduled matches
- **Root Cause:** `report-match-result!` calls `spawn-available-matches!` but players were still "in session" (player-sessions map not cleared)
- **Location:** `src/clj/parlameme/tournament/sessions.clj:1144`

### BUG #7: Agent State Not Synced After Tournament Completion
- **Severity:** High
- **Description:** After tournament completes (`:completed` status), agents remain in `:in-game` state with game tools still available
- **Impact:** Agents stuck, can't start new games or tournaments
- **Expected:** Agents should return to `:lobby` state
- **Location:** Tournament completion callback chain

## Test Results

Tournament completed successfully with manual intervention:
- Match 0: player1 beat player3 (4-0)
- Match 1: player2 beat player3 (10-0)  
- Match 2: player1 beat player2 (4-0)

**Final Standings:**
1. player1: 6 points, 2 wins
2. player2: 3 points, 1 win
3. player3: 0 points, 0 wins

## Recommendations

1. Add `on-tournament-complete` hook to transition all participants to lobby
2. Fix `on-game-complete` to check if player is in tournament and transition to `:in-tournament` state
3. Call `spawn-available-matches!` after player-sessions are cleared, not during `report-match-result`
4. Register tournament callbacks in server startup sequence, not lazily
5. Add state machine diagram to documentation for agent lifecycle

---

# QA Testing: UI Game Flow - Bugs Found

**Date:** 2026-02-02
**Test:** Playwright UI testing of game flow and chat

## Test Results

### Successful Features:
- Game lobby shows correctly (players waiting, add bot button)
- Game start works after adding bot
- Resource display (Health, Energy, Shield) updates correctly
- Action palette shows available actions based on resources
- Target selection for actions works
- Action execution (attack, rest, all-out-attack) works correctly
- Victory screen displays winner, standings, condition

### Bugs Found:

### BUG #12: "View Results" Button Does Nothing
- **Severity:** Low
- **Description:** On victory screen, "View Results" button closes the modal without showing any results
- **Expected:** Should show detailed game results or statistics
- **Location:** `src/cljs/parlameme/game/views.cljs` (victory modal)

### BUG #13: Stale Game State After Session Ends
- **Severity:** Medium
- **Description:** After game ends, navigating to game.html shows old game state with error messages "You are not in a game session"
- **Expected:** Should show lobby or prompt to join a new game
- **Location:** Client-side session management

### BUG #14: re-frame Bad Dispatch Error on Chat Send
- **Severity:** Low
- **Description:** Console error "re-frame: ignoring bad :dispatch value" appears when sending chat message
- **Impact:** Message still sends successfully, but indicates code issue
- **Location:** `src/cljs/parlameme/game/events.cljs` (chat dispatch)

### BUG #15: Chat Messages Not Synced in Real-Time
- **Severity:** High
- **Description:** Messages sent from MCP/backend don't appear in UI chat without page refresh
- **Impact:** Players can't have real-time conversations
- **Root Cause:** WebSocket not pushing channel messages, or UI not subscribed to updates
- **Location:** `src/clj/parlameme/v3/sente.clj`, `src/cljs/parlameme/game/subs.cljs`

## UI Positive Notes

1. Clean, readable interface with emoji icons
2. Resource panel clearly shows player stats
3. Action palette intuitive with target selection
4. Victory screen celebratory with standings
5. Chat channel selector dropdown works well
6. Message input with Send button functional

---

# QA Testing: Edge Cases - Bugs Found

**Date:** 2026-02-02
**Test:** Reconnection, session cancellation, state sync

## Test Results

### BUG #16: Chat History Not Loaded on Reconnect
- **Severity:** Medium
- **Description:** After page refresh/reconnect, chat shows "No messages yet" even though messages were sent earlier
- **Expected:** Should load recent messages from server
- **Location:** `src/cljs/parlameme/game/events.cljs` (session restore), `src/clj/parlameme/v3/sente.clj`

### BUG #17: No Real-Time Notification of Game Cancellation
- **Severity:** High
- **Description:** When game is cancelled server-side, UI continues showing active game. Only shows error after attempting action.
- **Expected:** Should receive WebSocket event and show cancellation modal
- **Location:** `src/clj/parlameme/v3/sente.clj` (broadcast cancel event)

## Positive Edge Case Behaviors

1. **Reconnection works** - Player can refresh page and rejoin their session
2. **Session restoration** - Game state correctly restored on reconnect
3. **Error handling** - Server correctly rejects actions on cancelled session
4. **LocalStorage session** - Session ID persisted in browser for auto-rejoin

---

# Summary of All Bugs Found

## Critical (3)
- **BUG #6**: Match session not auto-created after previous match completion
- **BUG #15**: Chat messages not synced in real-time (no WebSocket push)
- **BUG #17**: No real-time notification of game cancellation

## High (5)
- **BUG #2**: Tournament completion callback not registered on server start
- **BUG #3**: Agent state machine stuck in completed game
- **BUG #7**: Agent state not synced after tournament completion
- **BUG #11**: Agent stuck in :in-game state after tournament completes
- **BUG #16**: Chat history not loaded on reconnect

## Medium (4)
- **BUG #4**: leave_game returns to lobby instead of tournament
- **BUG #5**: Cannot re-register for active tournament
- **BUG #13**: Stale game state shown after session ends
- **BUG #16**: Chat history not loaded on reconnect

## Low (4)
- **BUG #1**: MCP schema parameter naming inconsistency
- **BUG #12**: "View Results" button does nothing visible
- **BUG #14**: re-frame bad dispatch error on chat send

## Recommendations Priority

1. Fix WebSocket message broadcasting for chat (BUG #15)
2. Fix tournament callback registration on startup (BUG #2)
3. Fix agent state transitions after game/tournament completion (BUG #3, #7, #11)
4. Add game cancellation WebSocket broadcast (BUG #17)
5. Fix spawn-available-matches timing (BUG #6)
6. Load chat history on session restore (BUG #16)
