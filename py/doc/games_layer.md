# Games Layer

The games layer contains four compiled game definitions built using the DSL builder. Each game is a frozen `CompiledGame` instance — the single source of truth that drives the runtime, MCP tool generation, UI rendering, archive replay, and AI agent strategies. Games are registered in `games/__init__.py` → `REGISTRY` dict.

## File Layout

| File | Lines | Game |
|------|-------|------|
| `games/__init__.py` | ~22 | REGISTRY dict, imports all 4 games |
| `games/auction.py` | ~655 | Art Auction: Mechanism Design |
| `games/werewolf.py` | ~731 | Werewolf: Strategic Edition |
| `games/parliament_arena.py` | ~1440 | Parliament Arena: The Last Assembly |
| `games/exchange.py` | ~1265 | Reptiloid Exchange: Market Simulator |

Tests: `tests/test_games/` — per-game integration tests (22 + 29 + 30 + 65 = 146 tests).

## Registry

```python
# games/__init__.py
REGISTRY: dict[str, CompiledGame] = {
    "auction": auction,
    "exchange": exchange,
    "werewolf": werewolf,
    "parliament_arena": parliament_arena,
}
```

Every subsystem imports from `REGISTRY`: MCP server (tool generation), server app (session creation), tournament system (match spawning), strategy archetypes (game-specific templates), analytics (stats aggregation).

---

## Game Summary

| Game | ID | Players | Phases | Deals | Votes | Speech Acts | Roles | Resources | Victories |
|------|----|---------|--------|-------|-------|-------------|-------|-----------|-----------|
| Art Auction | `auction` | 3–16 | 7 | 12 | 1 | 0 | 0 | 5 | 3 |
| Reptiloid Exchange | `exchange` | 4–8 | 7 | 12 | 3 | 3 | 5 | 8 | 2 |
| Werewolf | `werewolf` | 8–24 | 7 | 11 | 1 | 2 | 10 | 6 | 3 |
| Parliament Arena | `parliament_arena` | 6–24 | 7 | 12 | 4 | 5 | 7 | 8 | 3 |

---

## Art Auction: Mechanism Design

A game about information asymmetry, auction theory, and strategic bidding. Players bid on art lots across 6 rounds, each with a different auction format chosen by vote.

### Phase Sequence

```
setup (auto) → preview → format_vote → bidding → reveal (auto) → settlement (auto) → intermission
```

Loops for 6 lots. Victory checked after each settlement.

### Resources

| Resource | Initial | Visibility | Description |
|----------|---------|------------|-------------|
| `gold` | 1000 | private | Currency for bidding |
| `credit` | 500 | private | Borrowing capacity (0–1000) |
| `reputation` | 50 | public | Auction house standing (0–100) |
| `collection_value` | 0 | public | Portfolio worth |
| `insider_info` | 0 | private | Information tokens (0–5) |

### Deals (12)

**Bidding formats** (active in `bidding` phase, guarded by current auction_type):
- `sealed_bid` — First-price sealed bid (guard: `auction_type == "first_price"`)
- `vickrey_bid` — Second-price sealed bid (guard: `auction_type == "vickrey"`)
- `english_bid` — Ascending bid with outcome guard on bid > highest (guard: `auction_type == "english"`)
- `jump_bid` — 50%+ raise in English auction, +5 reputation (guard: `auction_type == "english"`)
- `dutch_claim` — Claim at descending price (guard: `auction_type == "dutch"` AND no winner yet)
- `all_pay_bid` — Everyone pays their bid, highest wins (guard: `auction_type == "all_pay"`)
- `pass_bid` — Opt out of current lot (always available)

**Information market** (active in `preview`):
- `buy_info` — 50 gold for +1 insider_info and +10 taste_bonus
- `appraise` — 25 gold to broadcast collection value

**Social** (active in `intermission`):
- `bidding_ring` — Collusion proposal (responses: join/reject/expose)
- `take_credit` — Borrow gold, 20% interest deducted from collection_value
- `gift_art` — Transfer 100 collection_value to another player

### Vote

- `choose_format` — Plurality vote for next auction format (first_price/english/dutch/vickrey/all_pay)

### Victory Conditions (3, all distribution type)

| Condition | Trigger | Score Formula |
|-----------|---------|---------------|
| `wealth_leader` | `current_lot > total_lots` (after 6 lots) | `gold + collection_value + taste_bonus` |
| `collection_master` | Same trigger | `collection_value` |
| `reputation_king` | Same trigger | `reputation` |

All three use `type="distribution"` — all players scored, highest wins. Priority: wealth > collection > reputation.

### Context (ContextConfig)

6 variable hints, 4 phase hints, 2 channel hints. No role hints (game has no roles).

---

## Werewolf: Strategic Edition

Classic social deduction with full role set, night actions, commitments (hunter/lover/elder), whisper channels, and multi-phase days.

### Phase Sequence

```
setup (auto) → first_night → night → dawn (auto) → day → trial → dusk (auto)
```

Loops: night → dawn → day → trial → dusk → night. Victory checked at dawn and dusk.

### Roles (10)

| Role | Team | Night Action | Special |
|------|------|-------------|---------|
| `villager` | Village | — | Filler role, no abilities |
| `seer` | Village | `seer_vision` — reveal one player's role | Core info role |
| `witch` | Village | `witch_heal` / `witch_poison` — save or kill | One-use potions |
| `hunter` | Village | `hunter_aim` — set revenge target | On death: kills aimed target (commitment) |
| `bodyguard` | Village | `bodyguard_protect` — shield one player | min_players=10 |
| `cupid` | Village | `cupid_link` — bind self + target as lovers | min_players=10, lovers die together |
| `elder` | Village | — | min_players=12, vengeance curse on death |
| `werewolf` | Wolves | `wolf_mark` — mark target for elimination | Count: 2 |
| `alpha_wolf` | Wolves | `alpha_convert` — convert villager to wolf | min_players=12, once per game |
| `tanner` | Neutral | — | min_players=10, wins ONLY if lynched |

### Deals (11)

**Night actions** (phase-restricted, role-filtered):
- `seer_vision`, `witch_heal`, `witch_poison`, `bodyguard_protect`, `alpha_convert`, `cupid_link`, `hunter_aim`, `wolf_mark`

**Day actions**:
- `accuse` — Public accusation (responses: second/defend)
- `claim_role` — Casual role claim, 2 per game (cheap talk, unverified)
- `form_council` — Create village_council group (responses: join/decline)

### Vote

- `lynch` — Majority vote to lynch or spare (with subject targeting)

### Speech Acts (2)

- `declare_role` — Verifiable role claim under oath (verified on death: +20 trust if true, -25 trust if false)
- `predict_death` — Predict a player dies tonight (verified at phase_change: +15 trust + +5 influence if correct)

### Commitments (3)

- **hunter_revenge** — On eliminate: if aimed_at is set, eliminate that target (once)
- **lover_heartbreak** — On eliminate: if lover attr set, eliminate lover (once)
- **elder_vengeance** — On eliminate: if role is elder, all alive villagers lose -15 trust and -5 influence (once)

### Victory Conditions (3)

| Condition | Trigger | Type |
|-----------|---------|------|
| `tanner_wins` | `last_lynched_role == "tanner"` | single, team=neutral |
| `village_wins` | All wolves eliminated | single, team=village |
| `wolves_win` | Wolves >= villagers | single, team=wolves |

### Channels (4)

- `village_square` — Public daytime discussion
- `wolf_den` — Group channel for wolf_pack (night only)
- `whisper` — Private channel with +2 suspicion per message
- `announcements` — Broadcast channel (deaths, events)

---

## Parliament Arena: The Last Assembly

Political simulation with factions, legislation, bribery, espionage, expulsion, and dead hand mechanisms. The most complex game at ~1440 lines.

### Phase Sequence

```
setup (auto) → election (once) → caucus → agenda → floor → vote → fallout (auto)
```

Loops: caucus → agenda → floor → vote → fallout → caucus. Election runs once at game start.

### Factions (7 roles)

| Faction | Bonus Per Round | Strategy |
|---------|----------------|----------|
| `vault_dweller` | +1 intel | Information advantage, investigation |
| `scrap_lord` | +5 caps | Economic power, bribery |
| `green_cultist` | -2 radiation | Survival, environmental bills |
| `iron_guard` | +2 influence | Political power, positions |
| `free_radical` | +1 intel | Underground contacts, disruption |
| `old_timer` | +3 reputation | Coalition building, trust |
| `backbencher` | +1 achievement | Filler role (free_radicals team) |

Each player also has a **hidden_type** attribute (`loyalist` / `opportunist` / `ideologue` / `chaotic`) distributed randomly at setup — visible only to the player, discoverable via `investigate`.

### Hidden Type Mechanics

- **Opportunist**: Gains +10 bonus caps when accepting bribes
- **Ideologue**: Gains +10 bonus reputation when exposing bribes
- **Loyalist**: Pays double reputation (-30 total) when betraying coalitions
- **Chaotic**: Dead hand deals extra damage (influence + radiation to all)

### Deals (12)

**Diplomacy:**
- `promise` — Non-binding (responses: acknowledge/dismiss)
- `handshake` — Reputation-backed agreement (10 rep stakes, creates relation)
- `blood_oath` — Maximum commitment, once/game (25 rep stakes, triggers vengeance on death)
- `form_coalition` — Multilateral alliance (2-3 responders, majority rule)
- `betray_coalition` — Break alliance relations for +20 caps

**Economy:**
- `bribe` — Pay caps for political support (responses: accept/reject/expose)
- `trade_intel` — Exchange intel tokens (responses: trade/accept/reject)

**Espionage:**
- `investigate` — 3 intel to reveal target's hidden_type (60% real, 40% fake — mutually exclusive)
- `blackmail` — 2 intel to damage target's reputation (25% chance identity leaks)
- `leak_scandal` — 2 intel for reputation attack (70% success, 30% backfire — mutually exclusive)

**Governance:**
- `speaker_set_agenda` — Speaker-only: set bill type for next vote
- `appoint_position` — PM-only: appoint target as Minister, add to cabinet

### Votes (4)

- `bill_vote` — Vote on legislation (effect depends on bill type: taxation/defense/welfare/radiation_cleanup/emergency_powers)
- `no_confidence` — Supermajority to remove any official (PM removal dissolves cabinet)
- `expulsion` — Supermajority to eliminate a player
- `elect_position` — Majority to fill vacant position (cycles: speaker → PM → opposition_leader)

### Speech Acts (5)

- `claim_type` — Claim your hidden_type (verified on death/game_end)
- `accuse_type` — Accuse target's hidden_type (costs 5 influence, verified on target's elimination)
- `promise_vote` — Promise voting direction (costs 5 reputation, tracked 2 rounds)
- `predict_expulsion` — Predict who gets expelled (costs 1 intel, verified at phase_change)
- `interrogate` — Opposition Leader or high-influence player forces response (costs 8 influence)

### Commitments (4)

- **dead_hand** — On eliminate: damages everyone's reputation. Chaotic types cause extra influence/radiation damage
- **blood_oath_vengeance** — On eliminate: surviving blood oath partners gain +10 influence, +3 intel
- **cabinet_crisis** — On eliminate: if PM eliminated, cabinet dissolves
- **radiation_emergency** — On phase_change: if >1/3 of players have radiation>=60, those players lose rations and gain radiation (recurring)

### Victory Conditions (3, all distribution type)

| Condition | Trigger | Score Formula |
|-----------|---------|---------------|
| `session_complete` | `round >= 10` | `caps×25 + influence×25 + reputation×20 + achievements×15 + intel×10 - radiation×15 - suspicion×10` |
| `power_consolidation` | Any player reaches 100 influence | `influence×35 + caps×20 + reputation×20 + achievements×10 - radiation×10 - suspicion×5` |
| `apocalypse` | >50% of players have radiation>=80 | `reputation×40 - radiation×30 + rations×15 + achievements×10 - suspicion×5` |

### Channels (4)

- `assembly` — Public floor debate
- `faction_caucus` — Group channel per faction
- `backroom` — Private channel (+3 suspicion per message)
- `intelligence_wire` — Broadcast channel for investigation results

---

## Reptiloid Exchange: Market Simulator

Resource exchange with circular production chain, order book matching, OTC trades, market manipulation, and regulation. The newest and second most complex game.

### Phase Sequence

```
setup (auto) → morning_briefing → open_market → research_phase → afternoon_market → settlement (auto) → close (auto)
```

Loops for 10 trading days. Settlement auto-runs: matches orders, resolves speech acts, checks victory.

### Circular Production Chain

```
harvester → alpha    refiner → beta    synthesizer → gamma    excavator → delta
    ↑                                                              |
    └──────────────────────── circular dependency ─────────────────┘
```

Each role specializes in one commodity but needs others for the full chain. This creates natural trade demand.

### Roles (5)

| Role | Production | Income |
|------|-----------|--------|
| `harvester` | +2 alpha/day | — |
| `refiner` | +2 beta/day | — |
| `synthesizer` | +2 gamma/day | — |
| `excavator` | +2 delta/day | — |
| `freelancer` | — | +50 credits/day |

### Resources (8)

| Resource | Initial | Visibility | Description |
|----------|---------|------------|-------------|
| `credits` | 500 | private | Universal currency |
| `alpha`, `beta`, `gamma`, `delta` | 0 | private | Trade commodities |
| `research` | 2 | private | Intelligence tokens (0–10) |
| `reputation` | 50 | public | Social capital (0–100) |
| `suspicion` | 0 | public | Regulatory attention (0–100) |

### Deals (12)

**Order book trading:**
- `limit_order` — Post bid/ask at specific price (locks funds via escrow)
- `market_order` — Execute at best price (locks max_price × qty)
- `cancel_order` — Remove pending order, refund locked funds

**Direct trading:**
- `otc_trade` — Bilateral trade with response guard (responder must hold the asset)

**Information:**
- `buy_research` — 30 credits for +1 research token
- `investigate_position` — 1 research to reveal target's commodity holdings (+3 suspicion)
- `publish_analysis` — 1 research for +5 reputation, broadcast

**Market manipulation:**
- `insider_trade` — 2 research to acquire assets (+10 suspicion)
- `hostile_takeover` — Stake 200 credits + 20 reputation (responses: surrender/defend/poison_pill)

**Finance:**
- `pay_dividend` — Burns credits, gains reputation (amount/10)
- `report_violation` — Stake 10 reputation, target pays fine or denies
- `audit_defense` — Burns credits to reduce suspicion (amount/5)

### Custom Effects

- **PlaceOrder**: Adds order to order book from deal params (side, price, qty, asset). Auto-increments sequence number.
- **MatchOrders**: Continuous double auction — matches bids (desc) vs asks (asc) by price-time priority. Credits counterparties at midpoint price, refunds unmatched.
- **CancelOrder**: Validates ownership, removes from book, refunds locked funds.

### Votes (3)

- `market_halt` — Supermajority to suspend trading, reduces all suspicion by 15
- `elect_regulator` — Majority to elect market regulator
- `bailout_vote` — Majority, each firm pays 25 credits

### Speech Acts (3)

- `predict_price` — Predict asset price direction (verified at settlement by comparing price history)
- `accuse_manipulation` — Accuse target of manipulation (verified: suspicion >= 30)
- `promise_delivery` — Promise to deliver assets within 2 rounds

### Commitments (2)

- **margin_call** — On phase_change: if credits <= 0, emergency loan +100 credits, -15 reputation, +10 suspicion (recurring)
- **reputation_collapse** — On eliminate: if suspicion >= 80, -25 reputation

### Victory Conditions (2)

| Condition | Trigger | Score Formula |
|-----------|---------|---------------|
| `trading_champion` | `trading_day > total_days` (after 10 days) | `credits×10 + (alpha+beta+gamma+delta)×15 + reputation×20 - suspicion×10` |
| `market_domination` | `credits >= 2000` | Single winner (type="single") |

### Key Design: Escrow Model

Orders lock funds on placement (`Damage` effect removes credits). `MatchOrders` only credits counterparties and refunds unmatched remainder. GTC (good-til-cancelled) semantics — unmatched orders stay on book, players must `cancel_order` to reclaim locked funds.

### Response Guards (OTC)

OTC trade `accept` outcome has a guard: `resource_of(responder, asset) >= qty`. The responder must actually hold the assets they're trading. Guard failure returns `GUARD_FAILED` error and the deal stays pending.

---

## DSL Patterns Common to All Games

### Phase Definition

```python
.phase(
    "bidding",
    allows=["sealed_bid", "english_bid", "pass_bid"],
    channels=["auction_floor", "auctioneer"],
    duration=60,
    effects=[SetVar("highest_bid", 0)],  # entry effects
)
```

### Deal Definition

```python
.deal(
    "sealed_bid",
    actor=alive(),
    guard=game.auction_type == "first_price",
    per_phase=1,
    params={"amount": {"type": "number", "min": 1, "label": "Bid amount"}},
    stakes={"actor": [("gold", "amount")]},
    effects=[
        SetAttr("actor", "current_bid", params.amount),
        Notify("actor", "Your sealed bid of {amount} is locked."),
    ],
    doc="First-price sealed bid.",
)
```

### Victory Condition

```python
.victory(
    "wealth_leader",
    when=game.current_lot > game.total_lots,
    type="distribution",
    score=actor.gold + actor.collection_value + actor.taste_bonus,
    priority=1,
    message="Wealthiest collector wins!",
)
```

### Context Config (AI Hints)

```python
.context(
    game_summary="Art auction with information asymmetry...",
    score_explanation="Gold + collection value at game end",
    var_hints=[VarHint("current_lot", "Lot", format="progress", max_var="total_lots")],
    phase_hints=[PhaseHint("bidding", "Place your bid.", urgency="critical")],
    channel_hints=[ChannelHint("wolf_den", when_to_use="Coordinate night kills")],
    deal_priorities={"sealed_bid": 100, "pass_bid": 80},
)
```

---

## Fixes Applied (Deep Analysis Session)

1. **REGISTRY type hint**: `dict[str, object]` → `dict[str, CompiledGame]`
2. **PA investigate/leak_scandal**: Fixed two independent `Maybe` blocks that could fire simultaneously (24% both-fire probability) → now mutually exclusive using `SetVar` flag + `When` fallback
3. **Exchange margin_call**: Changed from `trigger="eliminate"` (dead — no eliminates in Exchange) to `trigger="phase_change"` (fires on phase transitions); also added phase_change commitment firing to the runtime (`_fire_phase_change_commitments`)
4. **Effect type annotations**: Entity fields on `Reveal`, `Eliminate`, `Boost`, `Damage`, `Transfer`, `SetAttr`, `Notify`, `Relate`, `Unrelate`, `JoinGroup`, `LeaveGroup`, `SetAdd`, `SetRemove`, `SetResource`, `Reactivate` widened from `str` to `str | Expr` to match actual usage

---

## Testing

Tests in `tests/test_games/`:

| File | Tests | Focus |
|------|-------|-------|
| `test_auction.py` | 22 | Resources, format guards, 5 bid types, info market, settlement, replay |
| `test_werewolf.py` | 29 | Roles, night actions, phase transitions, votes, victory, commitments, replay |
| `test_parliament_arena.py` | 30 | Factions, elections, deals, bill voting, expulsion, visibility, dead hand, investigate mutual exclusivity, replay |
| `test_exchange.py` | 65 | Order book, escrow, matching, OTC guards, cancel, research, regulation, margin call, victory, replay |

All game tests follow the same pattern:
1. Create `GameRuntime(compiled)`
2. `start_game(players, seed)` + `run_setup(state)`
3. Execute specific game actions via `start_deal`, `cast_vote`, `respond_to_deal`
4. Assert state changes (resources, attrs, victory, eliminates)
5. Verify archive replay produces identical final state
