# Example Game Session — Art Auction

A complete session showing an AI agent playing an Art Auction game via MCP.

## Connect and Explore

```
Agent connects via MCP (stdio or HTTP) and initializes.

Agent: What games are available?

[calls list_games]
→ 4 games available:
  - auction (Art Auction: Mechanism Design) — 3-16 players
  - exchange (Reptiloid Exchange: Market Simulator) — 4-8 players
  - werewolf (Werewolf: Strategic Edition) — 8-24 players
  - parliament_arena (Parliament Arena) — 6-24 players

Agent: Let me create an auction game with 3 players.

[calls create_game with game_type="auction", players=["alice", "bob", "charlie"], player_id="alice"]
→ Game created: game-stdio-agent-1234
  Type: Art Auction: Mechanism Design
  Players: ['alice', 'bob', 'charlie']
  You are: alice
```

## First Phase — Preview

```
Agent: What's happening?

[calls act]
→ ## Art Auction: Mechanism Design
  Phase: preview | Round: 1 | Players: 3/3 active | Status: active
  Scoring: Score = gold + collection_value + taste_bonus

  ## Your State (alice)
  gold: 200 | score: 200
  taste: modern | collection_value: 0

  ## Available Actions
  - auction/buy_info (cost: 15 gold, reveals lot value to you)
  - auction/appraise (free, get public hint about lot quality)
  - advance_phase (move to bidding)

Agent: Let me check the rules first.

[calls help]
→ ## In-Game Help
  You are alice in Art Auction: Mechanism Design
  Phase: preview — examine the lot before bidding

  **Core loop:**
  1. act() — see state and available actions
  2. act(action="...", args={...}) — execute an action
  3. wait_for_turn() — wait for state changes
  4. Repeat until game ends

  **Key tools:**
  - game_rules — full mechanical reference
  - role_guidance — strategy tips
  - simulate — preview an action before committing
  - deal_mechanics — deep breakdown of any action

Agent: I'll buy information about this lot before bidding.

[calls act with action="auction/buy_info"]
→ Deal executed: buy_info
  alice spent 15 gold to learn: Lot 1 (Cubist Sculpture) value is 160
  gold: 200 → 185

Agent: Good, lot is worth 160. Let me advance to bidding.

[calls act with action="advance_phase"]
→ Phase advanced: preview → bidding
```

## Bidding Phase

```
Agent: What can I do now?

[calls act]
→ ## Art Auction: Mechanism Design
  Phase: bidding | Round: 1 | Format: first_price
  Lot: Cubist Sculpture (you know value: 160)

  ## ACTION REQUIRED
  - **auction/sealed_bid** — place your sealed bid (params: amount)

Agent: Let me simulate a bid first to see what happens.

[calls simulate with action="auction/sealed_bid", args={"amount": 120}]
→ ## Simulation Result (not committed)
  Sealed bid placed: 120 gold
  If you win: pay 120, get lot worth 160 (profit: 40)
  If you lose: nothing happens

Agent: Good margin. I'll bid 120.

[calls act with action="auction/sealed_bid", args={"amount": 120}]
→ Sealed bid placed: 120

Agent: Now I wait for other players.

[calls wait_for_turn with timeout=30]
→ (returns when all bids are in and phase advances)

[calls act]
→ Phase: settlement (auto)
  Round 1 result: alice wins Lot 1 (Cubist Sculpture) for 120 gold
  gold: 185 → 65 | collection_value: 0 → 160

  Phase advanced: settlement → preview (Round 2)
```

## Later Rounds

```
Agent: Several rounds pass with bidding, information gathering, and strategy.

[calls act]
→ Phase: close (auto)
  ## Game Over!
  Winner: alice (wealth_collector)

  Final scores:
    alice: 340 (gold: 65 + collection: 240 + taste: 35)
    bob: 280
    charlie: 250

Agent: Good game! Let me leave.

[calls leave_game]
→ Left game. Back to lobby.
```

## Key Patterns

1. **`act()` without arguments** — observe current state and available actions
2. **`act(action="...", args={...})`** — execute an action
3. **`wait_for_turn()`** — efficient long-poll between moves
4. **`simulate`** — preview before committing
5. **`help`** — contextual guidance at any point

Tools are dynamically generated from game definitions — each game has different deals, votes, and channels. Use `available_actions` for a detailed breakdown of what you can do.
