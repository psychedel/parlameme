# Strategy Layer

The strategy layer provides structured AI agent configuration — a three-level system where users can pick a pre-built archetype, tune personality sliders, or write per-phase/per-role/per-deal tactical text. Strategies compile into XML-sectioned LLM system prompts. The layer includes persistence (JSON file per strategy with version backups), a library of archetypes for each game, scenario extraction for testing strategies, and NiceGUI pages for editing and browsing.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  UI Pages                                                            │
│  ┌──────────────────────┐  ┌─────────────────────────────────────┐  │
│  │ /strategies           │  │ /workshop/{strategy_id}             │  │
│  │ Browse, create, fork  │  │ Tabbed editor + live prompt preview │  │
│  └──────────┬───────────┘  └──────────┬──────────────────────────┘  │
│             └──────────────┬──────────┘                              │
│                            ▼                                         │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  StrategyStore (JSON persistence)                             │   │
│  │  save / load / delete / list / fork / versions                │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         ▼                                            │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Strategy (frozen attrs)                                      │   │
│  │  Level 1: archetype      ── pick a template                   │   │
│  │  Level 2: sliders + priorities  ── tune personality           │   │
│  │  Level 3: structured text ── per-phase, per-role, per-deal    │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         ▼                                            │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  compile_strategy(strategy, compiled) → system prompt         │   │
│  │  XML sections: identity, priorities, phase_tactics,           │   │
│  │  role_guidance, deal_rules, channel_strategy, instructions    │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         ▼                                            │
│  ┌─────────────────────────────┐   ┌────────────────────────────┐   │
│  │  AgentRunner (agent layer)   │   │  Scenarios (testing)       │   │
│  │  Uses prompt as LLM system   │   │  Extract from archives     │   │
│  │  message                     │   │  Evaluate deterministic/LLM│   │
│  └─────────────────────────────┘   └────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## File Layout

| File | Purpose | Lines |
|------|---------|-------|
| `strategy/__init__.py` | Package exports: `Strategy`, `StrategyStore` | ~10 |
| `strategy/schema.py` | Frozen `Strategy` dataclass, personality axes, priority options | ~97 |
| `strategy/compiler.py` | `compile_strategy()` → XML-sectioned system prompt | ~220 |
| `strategy/archetypes.py` | Pre-built strategy templates: 4 per game × 4 games = 16 | ~750 |
| `strategy/store.py` | JSON persistence with version backups, path traversal protection | ~190 |
| `strategy/stats.py` | Win/loss stats from game archives | ~115 |
| `strategy/scenarios.py` | Scenario extraction, synthetic generation, deterministic/LLM evaluation | ~510 |
| `server/pages/strategies.py` | Browse/create/fork page (`/strategies`) | ~255 |
| `server/pages/workshop.py` | Tabbed editor + live preview (`/workshop/{id}`) | ~835 |
| `tests/test_strategy.py` | 46 tests: schema, store, archetypes, compiler | ~470 |
| `tests/test_scenarios.py` | Scenario extraction, synthetic generation, evaluation tests | ~555 |

---

## Strategy Document — Frozen Schema

```python
@attrs.frozen
class Strategy:
    id: str                          # uuid4().hex[:12]
    name: str = "Untitled Strategy"
    game_id: str = ""                # "auction" | "werewolf" | "parliament_arena" | "exchange"
    author: str = ""                 # browser-local UUID

    # Level 1: archetype
    archetype: str = ""              # template id ("shark", "diplomat", etc.)

    # Level 2: sliders + priorities
    personality: dict[str, float]    # {aggression, honesty, loyalty, risk_tolerance} each 0.0–1.0
    priorities: tuple[str, ...]      # ordered list from PRIORITY_OPTIONS

    # Level 3: structured text
    persona: str = ""                # free-text personality / backstory
    phase_tactics: dict[str, str]    # phase_id → tactic text
    role_overrides: dict[str, str]   # role_id → override text
    deal_rules: dict[str, str]       # deal_id → rule text
    channel_rules: dict[str, str]    # channel_id → usage text

    # Metadata
    version: int = 1
    forked_from: str | None = None
    created_at: float
    updated_at: float
    tags: tuple[str, ...]
    public: bool = False
```

### Three Complexity Levels

| Level | User effort | What's customized | Example |
|-------|-------------|-------------------|---------|
| **1. Archetype** | One click | Everything pre-filled from template | Pick "Shark" for Auction |
| **2. Sliders** | Drag 4 sliders + reorder priorities | Personality axes + goal ordering | Aggression: 0.9, Honesty: 0.2 |
| **3. Text** | Write tactics per phase/role/deal | Full control over every game element | "In bidding: bid 70% of value on sealed, 90% on English" |

Each level cascades: archetype sets sliders+text as defaults, sliders modify behavior descriptions, text overrides hints from the game's ContextConfig.

### Personality Axes

```python
PERSONALITY_AXES = ("aggression", "honesty", "loyalty", "risk_tolerance")
```

Each axis maps to behavioral guidance in the compiled prompt:
- `≤ 0.2` → "very low {axis}"
- `0.2–0.4` → "low {axis}"
- `0.4–0.6` → neutral (not mentioned)
- `0.6–0.8` → "high {axis}"
- `≥ 0.8` → "very high {axis}"

### Priority Options

```python
PRIORITY_OPTIONS = ("survival", "wealth", "reputation", "alliances",
                    "information", "dominance", "deception")
```

Compiled as a ranked list: "Your priorities in order: 1. Wealth, 2. Dominance, ...".

---

## Prompt Compiler

`compile_strategy(strategy, compiled)` converts a Strategy document into an XML-sectioned system prompt. Output is typically 1000–1500 tokens.

### Sections

```
<identity>
  You are an AI player in Art Auction: Mechanism Design.
  [game_summary from ContextConfig]
  [persona OR archetype name]
  [score_explanation]
</identity>

<priorities>
  Your priorities in order: 1. Wealth, 2. Dominance, ...
  Personality: very high aggression, very low honesty, high risk tolerance.
</priorities>

<phase_tactics>
  preview: [user tactic OR PhaseHint.summary + tips]
  bidding: [user tactic OR PhaseHint fallback]
</phase_tactics>

<role_guidance>    (skipped for games without roles, e.g. Auction)
  If your role is seer: [user override OR RoleHint.strategy + allies + threats]
</role_guidance>

<deal_rules>
  sealed_bid: [user rule OR deal.doc + outcome_summary()]
</deal_rules>

<channel_strategy>  (skipped for games without channels)
  village_square: [user rule OR ChannelHint.when_to_use + risk]
</channel_strategy>

<instructions>
  Use the `act` tool to observe game state and take actions.
  Use `wait_for_turn` between actions to wait for other players.
  Think step by step about your strategy before each action.
  Be concise in your reasoning.
</instructions>
```

### Compiler Optimizations

- **Automatic phases skipped**: Phases with `automatic=True` (setup, settlement, close, reveal) are never included — agents can't act in them, so mentioning them wastes tokens.
- **Critical urgency markers**: When using PhaseHint fallbacks, phases with `urgency="critical"` get a `[CRITICAL]` prefix to ensure the LLM pays attention.
- **Deal priority ordering**: Deals are sorted by `ContextConfig.deal_priorities` (descending) so the most important deals appear first in the prompt.
- **Role enrichment**: RoleHint fallbacks include `key_actions` (e.g., "Key actions: seer_vision, claim_role") and `phase_tips` (e.g., "Phase tips — night: Investigate quietly; day: Share findings").

### Fallback Hierarchy

For every section, the compiler checks:
1. **User-defined text** (from Strategy fields)
2. **ContextConfig hints** (from CompiledGame — `PhaseHint`, `RoleHint`, `ChannelHint`)
3. **Auto-generated** (from deal/outcome definitions via `mcp.mechanics.outcome_summary()`)

Empty sections (only XML tags, no content) are omitted from the final prompt.

### Token Estimation

```python
def estimate_tokens(text: str) -> int:
    return len(text) // 4  # ~4 chars per token for English
```

Used by the workshop UI to show real-time token count in the preview panel.

---

## Archetypes — Pre-Built Templates

16 archetypes across all 4 games:

### Auction (4)

| Archetype | Personality | Strategy |
|-----------|-------------|----------|
| **Value Hunter** | Low aggression, high honesty | Patient analysis, bid at 70-85% of value, prefer Vickrey |
| **Shark** | Very high aggression, low honesty | Jump bids, intimidation, force others to overpay |
| **Info Broker** | Moderate aggression | Always buy info, exploit asymmetry, bid at exact value |
| **Contrarian** | Low aggression, high risk | Go against the crowd, target ignored lots, vote for unpopular formats |

### Werewolf (4)

| Archetype | Personality | Strategy |
|-----------|-------------|----------|
| **Silent Observer** | Very low aggression, high honesty | Watch, listen, vote on evidence only |
| **Bold Accuser** | Very high aggression | Dominate discussion, accuse the quiet, force reactions |
| **Loyal Defender** | Low aggression, very high loyalty | Protect innocents, build trust networks |
| **Infiltrator** | Low aggression, very low honesty | Blend with village, misdirect, sacrifice weak allies |

### Parliament Arena (4)

| Archetype | Personality | Strategy |
|-----------|-------------|----------|
| **Coalition Builder** | Low aggression, very high loyalty | Build largest bloc, share power, reward loyalty |
| **Power Broker** | Moderate aggression, very low loyalty | Sell votes, trade intel, serve the highest bidder |
| **Ideologue** | Moderate aggression, very high honesty | Push faction agenda, never sell vote, expose corruption |
| **Chaos Agent** | High aggression, zero loyalty | Betray coalitions, make conflicting deals, profit from confusion |

### Exchange (4)

| Archetype | Personality | Strategy |
|-----------|-------------|----------|
| **Market Maker** | Low aggression, moderate honesty | Provide liquidity via bid-ask spreads, avoid directional bets |
| **Insider Trader** | Moderate aggression, very low honesty | Invest in research/investigation, trade on information asymmetry |
| **Corporate Raider** | Very high aggression, very high risk | Hostile takeovers, corner markets, regulatory pressure on competitors |
| **Reputation Builder** | Low aggression, very high honesty | Dividends, honest analysis, reliable OTC dealing, long-game reputation |

### Validation

Archetype tests verify:
- Phase tactics reference actual game phases
- Deal rules reference actual game deals
- All personality axes are in valid range (0.0–1.0)
- Unique archetype IDs per game
- All archetypes compile to valid prompts

---

## Strategy Store — JSON Persistence

`StrategyStore` saves each strategy as a separate JSON file in `data/strategies/`. Strategy IDs are validated against `^[a-zA-Z0-9_-]{1,64}$` to prevent path traversal attacks.

### File Structure

```
data/strategies/
├── abc123def456.json        # current version
├── abc123def456.v1.json     # version 1 backup
├── abc123def456.v2.json     # version 2 backup
└── xyz789...json            # another strategy
```

### CRUD Operations

| Method | Description |
|--------|-------------|
| `save(strategy)` | Write to disk. If file exists and version is bumped, back up old version |
| `load(strategy_id)` | Load by ID. Returns `None` if not found or corrupt |
| `delete(strategy_id)` | Remove file + all version backups |
| `list_all()` | All strategies sorted by `updated_at` descending |
| `list_by_author(author)` | Filter by author ID |
| `list_public()` | Filter `public=True` (community gallery) |
| `fork(strategy_id, new_author)` | Copy with new ID, author, `forked_from` pointer |
| `list_versions(strategy_id)` | Available backup version numbers |
| `load_version(strategy_id, version)` | Load a specific backup |

### Serialization

Uses `cattrs.Converter` for `attrs.frozen` ↔ `dict` conversion. All field types survive the round-trip (tuples, nested dicts, optional fields with defaults).

---

## Scenarios — Strategy Testing

The scenario system provides decision points for strategy evaluation. Two sources: archive extraction (real games) and synthetic generation (no archives needed). Two evaluation modes: deterministic (free, instant) and LLM-based (cheap, ~2 sec).

### Scenario Extraction (from Archives)

```python
def extract_scenarios(game_id: str, limit: int = 10) -> list[Scenario]:
```

1. Load archives for the game type (up to 10, shuffled for variety)
2. Replay each archive step-by-step using `GameRuntime`
3. At each player decision (deal, vote, respond, speech_act), capture:
   - `view_for()` output (what the player sees)
   - `format_available_actions()` output (what they can do)
   - The actual decision they made (ground truth)
4. Filter out automatic/timeout decisions and trivial states
5. Score by interestingness: `deals > responds > votes > speech_acts`, later rounds score higher
6. Deduplicate by `(phase, round, category)` for variety

### Synthetic Generation (no Archives Needed)

```python
def generate_synthetic_scenarios(game_id: str, count: int = 5, seed: int = 42) -> list[Scenario]:
```

Starts a game with dummy bot players, advances through phases, and tries random valid deals. Captures each successful deal attempt as a scenario. Deterministic (same seed → same scenarios). Works for any game in the registry without requiring existing archives.

### Scenario Data

```python
@attrs.frozen
class Scenario:
    id: str               # "{archive_id}@{step}"
    game_id: str
    archive_id: str
    step: int             # decision index
    player_id: str
    phase: str
    round: int
    description: str      # formatted game status (what player sees)
    available_actions: str # formatted action list
    actual_decision: dict  # ground truth
    category: str         # "deal" | "vote" | "respond" | "speech_act"
```

### Deterministic Evaluation

```python
def evaluate_deterministic(scenario, strategy_dict) -> DeterministicResult:
```

Rule-matching without an LLM:
- Check `phase_tactics[phase]` for matching text
- Check `deal_rules` keys against `available_actions` text (matches all relevant deals, not just the actual decision)
- Check `role_overrides` against scenario description
- Apply personality heuristics (high aggression → aggressive action, high risk tolerance → likely accept)
- Add top priority hint from `priorities` list
- Confidence: `high` (2+ matches), `medium` (1 match), `low` (0 matches)

Free and instant. Good for rapid iteration in the workshop editor.

### LLM Evaluation

```python
async def evaluate_with_llm(scenario, system_prompt, provider) -> LLMTestResult:
```

Sends the scenario description + available actions to the LLM with the compiled strategy as system prompt. Asks for `ACTION: <name>` + `REASON: <text>`. Compares chosen action to ground truth.

~100-200 tokens output, ~2 seconds per scenario. Used for deeper validation.

### Interestingness Scoring

```python
score = round * 2                    # later rounds more interesting
score += {deal: 10, respond: 8, vote: 6, speech_act: 5}
score += len(available_actions) / 100  # more choices = more interesting
```

---

## Strategy Statistics

`strategy/stats.py` scans game archives for strategy metadata and computes per-strategy win/loss records.

When an agent plays via `AgentRunner`, the session metadata includes:
```python
{"strategies": {"agent-0": "strategy_id_here"}}
```

`strategy_stats()` scans all archives, finds those with strategy metadata, and computes:

```python
{
    "strategy_id": {
        "games": 5,
        "wins": 3,
        "losses": 1,
        "draws": 1,
        "win_rate": 0.6,
        "game_ids": ["auction", "werewolf"],
    }
}
```

---

## UI Pages

### `/strategies` — Strategy Browser

Browse all strategies with filtering by author and public gallery. Actions:
- **Create**: New blank strategy → redirect to workshop
- **Fork**: Copy a public strategy with new author
- **Delete**: Remove strategy and all version backups

### `/workshop/{strategy_id}` — Strategy Editor

Split-panel layout:
- **Left**: Tabbed editor (Identity, Priorities, Phase Tactics, Role Guidance, Deal Rules, Channels)
- **Right**: Live compiled prompt preview with token count

Features:
- Archetype picker (populates all fields from template)
- Personality sliders (4 axes, 0.0–1.0)
- Priority drag-and-drop reordering
- Per-phase/role/deal/channel text fields (auto-populated from game ContextConfig)
- Debounced preview refresh on every edit
- Scenario testing panel (extract + evaluate deterministic)
- "Play" button → navigate to `/workshop/play/{strategy_id}`

---

## Testing

### test_strategy.py (46 tests)

| Test Class | Count | Focus |
|------------|-------|-------|
| `TestStrategySchema` | 6 | Creation, immutability, evolve, version bump, unique IDs |
| `TestStrategyStore` | 15 | CRUD, list/filter, fork, version backups, serialization roundtrip, path traversal rejection, valid ID chars, `.v` backup detection |
| `TestArchetypes` | 8 | All 4 games have archetypes, fields populated, valid personality, phase/deal references, unique IDs |
| `TestCompiler` | 17 | Minimal/full compilation, persona, phase tactics, role overrides, deal rules, channels, instructions, fallback hints, personality labels, token estimation, all games/archetypes compile, skip auto phases, critical urgency, deal priority ordering, role key_actions, role phase_tips |

### test_scenarios.py (30 tests)

| Test Class | Count | Focus |
|------------|-------|-------|
| `TestScenario` | 2 | Creation, frozen immutability |
| `TestDecisionPlayer` | 6 | Extract player from all decision types |
| `TestInterestingness` | 2 | Deals > votes, later rounds > earlier |
| `TestExtractScenarios` | 5 | Archive extraction, unknown game, empty dir, limit, sorting |
| `TestDeterministicTesting` | 7 | Phase tactics, deal rules, confidence levels, personality, risk tolerance |
| `TestLLMTesting` | 3 | Mock provider, error handling, no-match detection |
| `TestDeterministicAvailableDeals` | 2 | Deal rules match available_actions, priority hints |
| `TestSyntheticGeneration` | 3 | Synthetic auction, unknown game, deterministic seeding |
