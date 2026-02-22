"""Play a full tournament via MCP HTTP — 3 agents with different strategies.

This script simulates what real AI agents experience, using ONLY MCP tools.
Each agent reads available_actions before every move — no hardcoded deal names.

Agents:
- strategist: buys info, bids conservatively, prefers vickrey (truthful optimal)
- gambler: aggressive early bids, takes credit, prefers english (escalation)
- diplomat: forms bidding rings, gifts art, prefers first_price (predictable)
"""

import json
import re
import sys
import time

import httpx

BASE = "http://localhost:8080"
client = httpx.Client(timeout=30)


def mcp(agent_id: str, tool: str, args: dict | None = None) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args or {}},
    }
    resp = client.post(f"{BASE}/mcp/agent/{agent_id}", json=body)
    data = resp.json()
    result = data.get("result", data.get("error", {}))
    content = result.get("content", [{}])
    text = content[0].get("text", "") if content else ""
    return {"text": text, "error": result.get("isError", False), "raw": data}


def mcp_text(agent_id: str, tool: str, args: dict | None = None) -> str:
    return mcp(agent_id, tool, args)["text"]


def section(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def subsection(title: str):
    print(f"\n--- {title} ---")


def find_available_deals(agent_id: str) -> list[str]:
    """Parse deal names from available_actions response."""
    text = mcp_text(agent_id, "available_actions")
    return re.findall(r"\*\*(\w+)\*\*:", text)


def find_bid_deal(agent_id: str) -> str | None:
    """Find the correct bid deal for the current auction format."""
    deals = find_available_deals(agent_id)
    for bid_type in (
        "sealed_bid",
        "english_bid",
        "vickrey_bid",
        "all_pay_bid",
        "dutch_claim",
        "jump_bid",
    ):
        if bid_type in deals:
            return bid_type
    return None


def get_phase(agent_id: str) -> str:
    text = mcp_text(agent_id, "get_status")
    m = re.search(r"Phase:\s*(\w+)", text)
    return m.group(1) if m else "unknown"


def get_format(agent_id: str) -> str:
    text = mcp_text(agent_id, "available_actions")
    m = re.search(r"Format:\s*(\w+)", text)
    return m.group(1) if m else "unknown"


def is_game_over(agent_id: str) -> bool:
    text = mcp_text(agent_id, "get_status")
    return "ended" in text.lower() or "GAME OVER" in text


def find_pending_response(agent_id: str) -> tuple[str, list[str]] | None:
    """Check if agent has a pending deal to respond to. Returns (instance_id, options)."""
    text = mcp_text(agent_id, "available_actions")
    m = re.search(
        r"Respond.*?instance:\s*([a-z0-9_-]+).*?options:\s*([^)]+)\)", text, re.I
    )
    if not m:
        # Also check ACTION REQUIRED in status
        text = mcp_text(agent_id, "get_status")
        m = re.search(
            r"Respond.*?instance:\s*([a-z0-9_-]+).*?options:\s*([^)]+)\)", text, re.I
        )
    if m:
        iid = m.group(1)
        opts = [o.strip() for o in m.group(2).split(",")]
        return (iid, opts)
    return None


def find_pending_vote(agent_id: str) -> tuple[str, str, list[str]] | None:
    """Check if agent has a pending vote. Returns (vote_id, instance_id, options)."""
    text = mcp_text(agent_id, "available_actions")
    m = re.search(
        r"\*\*vote\*\*\s+in\s+(\w+).*?instance:\s*([a-z0-9_-]+).*?options:\s*([^)]+)\)",
        text,
        re.I,
    )
    if m:
        return (m.group(1), m.group(2), [o.strip() for o in m.group(3).split(",")])
    return None


# ===================================================================
# Agent strategy definitions
# ===================================================================


class AgentStrategy:
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.gold_spent = 0

    def choose_format(self, lot: int) -> str:
        raise NotImplementedError

    def bid_amount(self, lot: int, format: str, gold: float) -> int:
        raise NotImplementedError

    def respond_to_deal(self, deal_id: str, options: list[str]) -> str:
        return options[0]  # accept by default

    def preview_action(self, lot: int, deals: list[str]) -> str | None:
        return None

    def intermission_action(
        self, lot: int, deals: list[str]
    ) -> tuple[str, dict] | None:
        return None


class Strategist(AgentStrategy):
    """Calculated: buys info, bids conservatively, prefers vickrey."""

    def choose_format(self, lot: int) -> str:
        return "vickrey" if lot <= 4 else "english"

    def bid_amount(self, lot: int, format: str, gold: float) -> int:
        # Conservative: 8-15% of remaining gold, increasing with lots
        base = int(gold * (0.08 + lot * 0.015))
        return max(base, 20)

    def preview_action(self, lot: int, deals: list[str]) -> str | None:
        if "buy_info" in deals:
            return "buy_info"
        return None


class Gambler(AgentStrategy):
    """Aggressive: huge early bids, takes risks, prefers english."""

    def choose_format(self, lot: int) -> str:
        if lot <= 2:
            return "english"
        elif lot <= 4:
            return "all_pay"
        return "english"

    def bid_amount(self, lot: int, format: str, gold: float) -> int:
        if lot <= 3:
            # Aggressive: 25-40% of remaining gold
            return max(int(gold * (0.25 + lot * 0.05)), 30)
        else:
            # Conserve for later
            return max(int(gold * 0.08), 15)

    def preview_action(self, lot: int, deals: list[str]) -> str | None:
        if "appraise" in deals:
            return "appraise"
        return None

    def intermission_action(
        self, lot: int, deals: list[str]
    ) -> tuple[str, dict] | None:
        if lot >= 3 and "take_credit" in deals:
            return ("take_credit", {"amount": 100})
        return None


class Diplomat(AgentStrategy):
    """Social: forms alliances, gifts art, prefers first_price."""

    def choose_format(self, lot: int) -> str:
        return "first_price"

    def bid_amount(self, lot: int, format: str, gold: float) -> int:
        # Moderate: 10-18% of remaining gold
        return max(int(gold * (0.10 + lot * 0.013)), 25)

    def respond_to_deal(self, deal_id: str, options: list[str]) -> str:
        # Accept everything — diplomat is agreeable
        return "accept" if "accept" in options else options[0]

    def preview_action(self, lot: int, deals: list[str]) -> str | None:
        if "appraise" in deals:
            return "appraise"
        return None

    def intermission_action(
        self, lot: int, deals: list[str]
    ) -> tuple[str, dict] | None:
        if "bidding_ring" in deals:
            return ("bidding_ring", {"responder": "gambler"})
        if "gift_art" in deals:
            return ("gift_art", {"responder": "strategist"})
        return None


AGENTS = {
    "strategist": Strategist("strategist"),
    "gambler": Gambler("gambler"),
    "diplomat": Diplomat("diplomat"),
}

# ===================================================================
# PHASE 1: Tournament setup
# ===================================================================

section("PHASE 1: TOURNAMENT SETUP")

for a in AGENTS:
    client.post(
        f"{BASE}/mcp/agent/{a}",
        json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
    )

print("Strategist creates 'Battle of Wits v2' tournament...")
r = mcp_text(
    "strategist",
    "create_tournament",
    {
        "tournament_type": "round_robin",
        "game_type": "auction",
        "name": "Battle of Wits v2",
        "tournament_id": "bow-v2",
    },
)
print(f"  {r}")

for agent_id in ["gambler", "diplomat"]:
    r = mcp_text(agent_id, "register_tournament", {"tournament_id": "bow-v2"})
    print(f"  {agent_id} registered: {r}")

print()
print(mcp_text("strategist", "get_tournament_status"))
print()
print(mcp_text("strategist", "get_standings"))

# ===================================================================
# PHASE 2: Start tournament
# ===================================================================

section("PHASE 2: START TOURNAMENT")

r = mcp_text("strategist", "start_tournament", {"tournament_id": "bow-v2"})
print(r)

print()
for a in AGENTS:
    print(f"  {a}: {mcp_text(a, 'get_my_matches')}")

# Find match
matches_text = mcp_text("strategist", "get_my_matches")
match_ids = re.findall(r"\*\*(rr-\d+)\*\*", matches_text)
if not match_ids:
    print("ERROR: No matches found!")
    sys.exit(1)
match_id = match_ids[0]

# ===================================================================
# PHASE 3: Join match
# ===================================================================

section("PHASE 3: JOIN MATCH")

for a in AGENTS:
    r = mcp(a, "join_match", {"match_id": match_id})
    status = "OK" if not r["error"] else f"FAIL: {r['text'][:60]}"
    print(f"  {a} joins: {status}")

# Show initial state
subsection("Initial state (strategist's view)")
print(mcp_text("strategist", "get_status"))

subsection("Available actions")
print(mcp_text("strategist", "available_actions"))

# ===================================================================
# PHASE 4: Play the match — 6 lots
# ===================================================================

section("PHASE 4: PLAYING THE MATCH")

for lot in range(1, 7):
    subsection(f"LOT {lot}/6")
    phase = get_phase("strategist")
    print(f"  Phase: {phase}")

    # === PREVIEW ===
    if phase == "preview":
        print(f"  [Available actions]")
        actions = mcp_text("strategist", "available_actions")
        # Show context line
        for line in actions.split("\n")[:3]:
            print(f"    {line}")

        for agent_id, strategy in AGENTS.items():
            deals = find_available_deals(agent_id)
            action = strategy.preview_action(lot, deals)
            if action:
                r = mcp(agent_id, f"auction/{action}")
                status = (
                    r["text"].split("\n")[0]
                    if not r["error"]
                    else f"SKIP: {r['text'][:60]}"
                )
                print(f"  [{agent_id}] {action}: {status}")

        print(f"  -> Advance past preview")
        mcp("strategist", "advance_phase")
        phase = get_phase("strategist")

    # === FORMAT VOTE ===
    if phase == "format_vote":
        fmt = get_format("strategist")
        print(f"  [Format vote] Current format: {fmt}")

        for agent_id, strategy in AGENTS.items():
            choice = strategy.choose_format(lot)
            r = mcp(agent_id, "auction/vote_choose_format", {"option": choice})
            status = "OK" if not r["error"] else r["text"][:60]
            print(f"    {agent_id} votes {choice}: {status}")

        # Check what won
        phase = get_phase("strategist")
        fmt = get_format("strategist") if phase == "bidding" else "?"
        print(f"    Winner format: {fmt}")

    # === BIDDING ===
    phase = get_phase("strategist")
    if phase == "bidding":
        # Show the context line from available_actions
        actions = mcp_text("strategist", "available_actions")
        context_line = ""
        for line in actions.split("\n"):
            if line.startswith("Context:"):
                context_line = line
                break
        print(f"  [Bidding] {context_line}")

        for agent_id, strategy in AGENTS.items():
            bid_deal = find_bid_deal(agent_id)
            if not bid_deal:
                print(f"    {agent_id}: no bid deal available, passing")
                mcp(agent_id, "auction/pass_bid")
                continue

            # Get gold from status
            status_text = mcp_text(agent_id, "get_status")
            gold_match = re.search(r"gold:\s*([\d.]+)", status_text)
            gold = float(gold_match.group(1)) if gold_match else 500

            amount = strategy.bid_amount(lot, fmt, gold)
            print(f"    {agent_id}: {bid_deal} amount={amount} (gold={gold:.0f})")

            r = mcp(agent_id, f"auction/{bid_deal}", {"amount": amount})
            if r["error"]:
                # Maybe english bid too low? Try higher
                if "highest" in r["text"].lower() or "raise" in r["text"].lower():
                    hb_match = re.search(r"High bid:\s*(\d+)", context_line)
                    if hb_match:
                        amount = int(hb_match.group(1)) + 10
                        print(f"    {agent_id}: raising to {amount}")
                        r = mcp(agent_id, f"auction/{bid_deal}", {"amount": amount})

                if r["error"]:
                    # Extract just the first line of error
                    err = r["text"].split("\n")[0]
                    print(f"      -> {err}")
                else:
                    delta = [l for l in r["text"].split("\n") if "Changes:" in l]
                    print(f"      -> OK {delta[0] if delta else ''}")
            else:
                delta = [l for l in r["text"].split("\n") if "Changes:" in l]
                print(f"      -> OK {delta[0] if delta else ''}")

        # Advance past bidding → reveal (auto) → settlement (auto) → intermission
        print(f"  -> Advance (bidding → reveal → settlement → intermission)")
        r = mcp_text("strategist", "advance_phase")
        first_line = r.split("\n")[0]
        print(f"    {first_line}")

    # === INTERMISSION ===
    phase = get_phase("strategist")
    if phase == "intermission":
        print(f"  [Intermission]")

        for agent_id, strategy in AGENTS.items():
            # Check for pending responses first
            pending = find_pending_response(agent_id)
            if pending:
                iid, opts = pending
                choice = strategy.respond_to_deal("unknown", opts)
                r = mcp(agent_id, "respond", {"instance_id": iid, "response": choice})
                status = "OK" if not r["error"] else r["text"][:60]
                print(f"    {agent_id} responds {choice} to {iid}: {status}")

            deals = find_available_deals(agent_id)
            action = strategy.intermission_action(lot, deals)
            if action:
                deal_name, deal_args = action
                r = mcp(agent_id, f"auction/{deal_name}", deal_args)
                if not r["error"]:
                    # Show first line of result
                    first = r["text"].split("\n")[0]
                    print(f"    {agent_id} -> {deal_name}: {first}")

                    # Check if anyone needs to respond
                    for other_id, other_strat in AGENTS.items():
                        if other_id == agent_id:
                            continue
                        pending = find_pending_response(other_id)
                        if pending:
                            iid, opts = pending
                            choice = other_strat.respond_to_deal(deal_name, opts)
                            r2 = mcp(
                                other_id,
                                "respond",
                                {"instance_id": iid, "response": choice},
                            )
                            status = "OK" if not r2["error"] else r2["text"][:40]
                            # Show delta if available
                            delta = [
                                l for l in r2["text"].split("\n") if "Changes:" in l
                            ]
                            print(f"    {other_id} responds {choice}: {status}")
                            if delta:
                                print(f"      {delta[0]}")

        # Advance to next lot
        print(f"  -> Advance to next lot")
        r = mcp_text("strategist", "advance_phase")
        first_line = r.split("\n")[0]
        print(f"    {first_line}")

    # Check game over
    if is_game_over("strategist"):
        print(f"\n  *** GAME OVER after lot {lot}! ***")
        break

# Safety: keep advancing if needed
safety = 0
while not is_game_over("strategist") and safety < 20:
    mcp("strategist", "advance_phase")
    safety += 1

# ===================================================================
# RESULTS
# ===================================================================

section("MATCH RESULTS")

for agent_id in AGENTS:
    subsection(f"{agent_id.upper()}'s final view")
    text = mcp_text(agent_id, "get_status")
    # Show relevant lines
    for line in text.strip().split("\n"):
        if any(
            k in line.lower()
            for k in [
                "phase",
                "you",
                "resources",
                "game state",
                "other",
                "reputation",
                "collection",
                "gold",
                "winner",
                "game over",
                "status",
            ]
        ):
            print(f"  {line}")

section("RETURNING TO TOURNAMENT")

for agent_id in AGENTS:
    r = mcp_text(agent_id, "leave_game")
    print(f"  {agent_id}: {r}")

time.sleep(0.5)

section("TOURNAMENT FINAL RESULTS")

print(mcp_text("strategist", "get_tournament_status"))
print()
print(mcp_text("strategist", "get_standings"))
print()
print(mcp_text("strategist", "get_my_matches"))

# ===================================================================
# EXPERIENCE ASSESSMENT
# ===================================================================

section("AI AGENT EXPERIENCE ASSESSMENT")

# Test the key UX improvements:
# 1. Context line in available_actions
# 2. Param hints with types
# 3. Advance readiness with guards
# 4. Error tips

print("Testing UX improvements...\n")

# Create a quick test game to check formatters
r = mcp(
    "test-ux",
    "create_game",
    {
        "game_type": "auction",
        "session_id": "ux-test",
        "player_id": "test-ux",
        "players": ["test-ux", "bot1", "bot2"],
    },
)

if not r["error"]:
    # Check available_actions in preview
    actions = mcp_text("test-ux", "available_actions")
    print("1. Context line in available_actions:")
    for line in actions.split("\n")[:3]:
        print(f"   {line}")

    has_context = "Context:" in actions
    has_format = "Format:" in actions
    has_lot = "Lot" in actions
    print(
        f"   Has context: {has_context}, Format shown: {has_format}, Lot shown: {has_lot}"
    )

    # Check param hints
    print("\n2. Param hints with types:")
    for line in actions.split("\n"):
        if "**" in line and ":" in line:
            print(f"   {line.strip()}")
            break

    has_type_hint = "number" in actions or "player" in actions
    print(f"   Has typed params: {has_type_hint}")

    # Advance to format_vote, vote, then check bidding context
    mcp("test-ux", "advance_phase")
    for p in ["test-ux", "bot1", "bot2"]:
        # Need to join bot agents
        if p != "test-ux":
            mcp(p, "join_game", {"session_id": "ux-test", "player_id": p})
        mcp(p, "auction/vote_choose_format", {"option": "english"})

    actions = mcp_text("test-ux", "available_actions")
    print("\n3. Bidding context (english format):")
    for line in actions.split("\n")[:4]:
        print(f"   {line}")

    has_bid_context = "No bids" in actions or "High bid" in actions
    has_english = "english_bid" in actions
    no_sealed = "sealed_bid" not in actions
    print(f"   Bid context shown: {has_bid_context}")
    print(f"   Correct deal (english_bid): {has_english}")
    print(f"   sealed_bid hidden: {no_sealed}")

    # Test error tips
    r = mcp("test-ux", "auction/sealed_bid", {"amount": 100})
    print("\n4. Error tip on guard failure:")
    for line in r["text"].split("\n"):
        print(f"   {line}")
    has_tip = "Tip:" in r["text"]
    print(f"   Has helpful tip: {has_tip}")

    # Test advance readiness — all bids should be guard-blocked except english_bid and pass_bid
    # After using english_bid once, check readiness
    mcp("test-ux", "auction/english_bid", {"amount": 50})
    actions = mcp_text("test-ux", "available_actions")
    print("\n5. Advance readiness (after using bid):")
    for line in actions.split("\n"):
        if (
            "Phase Control" in line
            or "advance" in line.lower()
            or "action" in line.lower()
        ):
            print(f"   {line.strip()}")

    # Clean up
    mcp("test-ux", "leave_game")
    for p in ["bot1", "bot2"]:
        mcp(p, "leave_game")

print("\n\nAll UX tests complete.")
