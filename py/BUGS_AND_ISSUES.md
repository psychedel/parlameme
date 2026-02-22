# Bugs, Issues, and Shortcomings — Python Engine Integration Testing

Discovered during comprehensive MCP + UI + Tournament testing session (2026-02-08).

Games tested: Parliament Arena (flagship), Auction, Werewolf.
Interfaces tested: MCP (HTTP JSON-RPC), NiceGUI UI (Playwright), Tournament system.

---

## BUGS (Incorrect behavior)

### BUG-1: MCP `get_status` hides public attributes for other players
- **Severity**: High
- **Where**: MCP formatter in `mcp/server.py`
- **Symptom**: "Other Players" section only shows public resources (influence, reputation, etc.), NOT public attributes (faction, role, position). The UI shows them correctly.
- **Impact**: AI agents can't see faction/role/position of other players, breaking political games like Parliament Arena.

### BUG-3: `elect_position` vote — vacant_position not auto-progressed
- **Severity**: High
- **Where**: `games/parliament_arena.py` — elect_position vote Cond logic
- **Symptom**: After electing Speaker, `vacant_position` becomes `"none"`. Second election for PM can't assign position because Cond checks `game.vacant_position == "prime_minister"` but it's `"none"`. Need SetVar to cycle: speaker → prime_minister → opposition_leader.
- **Impact**: Only Speaker position gets assigned; PM and Opposition Leader never filled.

### BUG-5: `available_actions` shows ALL deals regardless of actor filter
- **Severity**: High
- **Where**: MCP `available_actions` tool handler
- **Symptom**: Every player sees every deal in the current phase, regardless of actor_filter. E.g., non-speakers see `speaker_set_agenda`, non-wolves see `wolf_mark`, cupid sees `seer_vision`.
- **Impact**: Leaks game design information (what roles exist), misleads players about their capabilities.

### BUG-7: Private backroom messages not visible in `get_all_messages`
- **Severity**: Medium
- **Where**: MCP `get_all_messages` handler or channel read filter
- **Symptom**: Messages sent to private `backroom` channel don't appear in get_all_messages for the sender.
- **Impact**: Players can't verify their private messages were sent.

### BUG-12: Vue console error on chat send
- **Severity**: Low
- **Where**: NiceGUI client-side (Vue component lifecycle)
- **Symptom**: `TypeError` in Vue `beforeUnmount` when sending a chat message — full page re-renders breaking component lifecycle.
- **Impact**: Minor UI glitch, doesn't prevent functionality.

### BUG-16: Auction phase doesn't auto-advance after vote completion
- **Severity**: Medium
- **Where**: `engine/runtime/core.py` or `games/auction.py`
- **Symptom**: After format_vote completes, game stays in format_vote phase instead of auto-advancing to bidding. Players must manually call `advance_phase`.
- **Impact**: Breaks flow for AI agents that expect automatic progression.

### BUG-20: Tournament creates matches with fewer players than game minimum
- **Severity**: Critical
- **Where**: Tournament match spawning in `server/tournament.py`
- **Symptom**: Round-robin tournament for Auction (min 3 players) creates 2-player matches. Each match is a pair from the participant pool, but Auction needs at least 3.
- **Impact**: Tournament matches start with invalid player counts, producing broken games.

### BUG-22: MCP `create_game` has no player count validation
- **Severity**: Critical
- **Where**: `mcp/server.py` `_tool_create_game()` (line ~342)
- **Symptom**: `create_game` with no `players` arg creates a 1-player game and immediately starts it, even for games requiring 8-10+ players (e.g., Werewolf). No check against `compiled.min_players` or `compiled.max_players`.
- **Impact**: Games start in broken state with wrong number of players. Role assignment, group creation, and victory conditions all malfunction.

### BUG-24: Messages stored with empty content (param name mismatch)
- **Severity**: High
- **Where**: `mcp/server.py` `_exec_send()` (line ~378)
- **Symptom**: MCP tool schema defines param as `"content"`, but `_exec_send()` does `args.get("content", "")`. If agent sends `"message"` key (natural naming), content silently defaults to `""`. Messages are stored and displayed with no text.
- **Impact**: Two sub-bugs: (1) silent fallback to empty string without validation, (2) confusing param name.
- **Fix**: Add `content = args.get("content") or args.get("message", "")` + validate non-empty.

### BUG-26: Accusation target can't respond — filter mismatch
- **Severity**: Medium
- **Where**: `engine/runtime/core.py` deal response filter
- **Symptom**: When alice accuses diana, diana gets error "responder 'diana' does not match filter for deal 'accuse'" when trying to respond. The accusation target should be the responder.
- **Impact**: Accused players can't defend themselves. Accusations become one-sided.

### BUG-27: Victory reports single winner for team games
- **Severity**: Medium
- **Where**: Victory resolution in `engine/runtime/core.py`
- **Symptom**: Werewolf village victory says "alice wins!" instead of "Village team wins!" with all village members. Victory condition `village_wins` is `type: single` but should be team-based.
- **Impact**: Misleading — only one player credited for a team effort.

### BUG-28: Analytics shows 0 wins for all players
- **Severity**: High
- **Where**: `server/analytics.py` or archive winner detection
- **Symptom**: Leaderboard shows all players with 0 wins and "D" (draw) recent form, even though games have clear winners (e.g., werewolf village victory).
- **Impact**: Glicko-2 ratings don't differentiate, leaderboard is meaningless.

---

## ISSUES (Design/UX shortcomings)

### ISSUE-4: Pending speech acts not shown in `get_status`
- **Severity**: Medium
- **Where**: MCP `get_status` formatter
- **Symptom**: When speech acts are pending (e.g., unfulfilled promises, unresolved accusations), they don't appear in get_status output. Players have no visibility into active speech acts.
- **Impact**: Players can't track commitments or pending verification.

### ISSUE-6: Multilateral deal "pending" text confusing
- **Severity**: Low
- **Where**: MCP deal response formatting
- **Symptom**: After first multilateral response, message says "pending" without context. Should say "Waiting for more responses (1/3)" or similar.
- **Impact**: Confusing for AI agents trying to understand deal state.

### ISSUE-8: `send_backroom` silently accepts `recipient` param
- **Severity**: Low
- **Where**: MCP channel send handler
- **Symptom**: Private channel tool accepts `recipient` param but unclear if it's actually used for targeting. No error if param is invalid.
- **Impact**: Ambiguous API contract.

### ISSUE-9: UI Actions tab only shows "Advance Phase"
- **Severity**: High
- **Where**: NiceGUI game view, Actions tab
- **Symptom**: No deal or vote buttons in the Actions tab — only "Advance Phase". The UI is view-only for game state; all interaction must happen via MCP.
- **Impact**: UI is essentially a spectator mode. Human players can't play through the browser.

### ISSUE-10: Eliminated player cards not visually dimmed
- **Severity**: Low
- **Where**: NiceGUI player card rendering
- **Symptom**: Eliminated players show same visual treatment as active ones. Only difference is the status badge text.
- **Impact**: Hard to quickly scan who's alive.

### ISSUE-11: No visual explanation of own vs other player visibility
- **Severity**: Low
- **Where**: NiceGUI player card layout
- **Symptom**: Own card shows 8 resources, others show 5. No label explaining "Private resources only visible to you."
- **Impact**: Confusing for new players who don't understand information asymmetry.

### ISSUE-13: Tab auto-switches to Actions after chat send
- **Severity**: Low
- **Where**: NiceGUI tab behavior
- **Symptom**: After sending a chat message, UI switches from Chat tab to Actions tab. User has to switch back to see their message or continue chatting.
- **Impact**: Annoying UX for players who want to have conversations.

### ISSUE-15: Vote options not shown in `available_actions`
- **Severity**: Medium
- **Where**: MCP `available_actions` tool
- **Symptom**: Available actions lists vote name and description but not the available options (e.g., "lynch" vs "spare"). Agent has to guess or check tool schema.
- **Impact**: Requires extra tool calls to discover valid options.

### ISSUE-17: All bid types shown regardless of voted format
- **Severity**: Medium
- **Where**: Auction game `available_actions` or deal filtering
- **Symptom**: After voting first_price format, all bid types (sealed_bid, ascending_bid, dutch_bid, etc.) still appear. Only the voted format should be available.
- **Impact**: Players can execute bids in wrong format, or get confused about which to use.

### ISSUE-18: Tournament host auto-registered causes confusion
- **Severity**: Low
- **Where**: Tournament registration flow
- **Symptom**: Host is auto-registered on creation, but `register_tournament` gives "Already registered" error when host tries to register. Confusing flow.
- **Impact**: Minor UX confusion.

### ISSUE-19: All tournament matches show "active" simultaneously
- **Severity**: Medium
- **Where**: Tournament match state management
- **Symptom**: All round-robin matches marked "active" at tournament start, but engine's one-game-per-player rule means only one can actually be played.
- **Impact**: Misleading state. Agents try to join multiple matches and get errors.

### ISSUE-21: Completed games list includes archives from deleted game types
- **Severity**: Low
- **Where**: Archive listing / lobby UI
- **Symptom**: Duel, Mafia, Election archives still shown in completed games even though those games were removed. No way to clean up stale archives.
- **Impact**: Cluttered UI, confusing "Games by Type" analytics.

### ISSUE-23: Seer investigation result not reflected in `get_status`
- **Severity**: Medium
- **Where**: MCP `get_status` visibility or reveal system
- **Symptom**: After seer investigates diana (werewolf), the reveal result only appears in event history ("Your vision reveals: diana is werewolf"). The `get_status` "Other Players" section for diana doesn't show the revealed role.
- **Impact**: Seer has to remember investigation results manually; they're not persistent in the status view.

### ISSUE-29: Leaderboard includes players from deleted game types
- **Severity**: Low
- **Where**: Analytics, stats computation
- **Symptom**: Players like "duel-host", "mafia-host", "election-host" appear in leaderboard from old archives of removed games.
- **Impact**: Cluttered analytics with irrelevant data.

---

## SUMMARY

| Category | Count |
|----------|-------|
| Critical bugs | 2 (BUG-20, BUG-22) |
| High-severity bugs | 4 (BUG-1, BUG-3, BUG-5, BUG-24) |
| Medium bugs | 3 (BUG-7, BUG-16, BUG-26) |
| Low bugs | 1 (BUG-12) |
| High-severity UX issues | 2 (BUG-27, BUG-28, ISSUE-9) |
| Medium UX issues | 5 |
| Low UX issues | 6 |
| **Total** | **23** |

### Priority fix order:
1. BUG-22: Add min/max player validation to `create_game`
2. BUG-20: Tournament match player count validation
3. BUG-5: Filter `available_actions` by actor filter
4. BUG-24: Validate non-empty message content + accept both "content"/"message"
5. BUG-1: Include public attributes in MCP `get_status` other-player section
6. BUG-3: Auto-progress vacant_position through election cycle
7. BUG-28: Fix analytics win detection from archives
8. ISSUE-9: Add deal/vote interaction buttons to UI
