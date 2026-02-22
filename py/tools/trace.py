"""Hunter-based tracing presets for game engine observation.

Usage as module:
    from tools.trace import trace_deals, trace_effects, snapshot
    t = trace_deals(); ...; t.stop()

Usage as CLI:
    uv run python -m tools.trace deals [--depth N] [--output FILE]
    uv run python -m tools.trace effects [--depth N]
    uv run python -m tools.trace phases
    uv run python -m tools.trace mcp
    uv run python -m tools.trace flow --output /tmp/flow.jsonl
    uv run python -m tools.trace errors
    uv run python -m tools.trace snap --game auction --players 3 --seed 42
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import hunter

# ---------------------------------------------------------------------------
# Preset: deals — deal lifecycle (start -> response -> effects -> outcome)
# ---------------------------------------------------------------------------


def trace_deals(*, depth: int = 8, output: str | None = None):
    """Trace deal start, response, resolution, and effects."""
    stream = open(output, "w") if output else sys.stderr
    return hunter.trace(
        hunter.Q(
            module_startswith="engine.runtime.core",
            function_in=[
                "start_deal",
                "respond_to_deal",
                "_resolve_outcome",
                "start_vote",
                "cast_vote",
                "_tally_votes",
            ],
            kind_in=["call", "return"],
        )
        | hunter.Q(
            module="engine.runtime.effects",
            function="apply_effects",
            kind="call",
        ),
        action=hunter.CallPrinter(stream=stream),
    )


# ---------------------------------------------------------------------------
# Preset: effects — deep effect application chain
# ---------------------------------------------------------------------------


def trace_effects(*, depth: int = 5, output: str | None = None):
    """Trace every individual effect application."""
    stream = open(output, "w") if output else sys.stderr
    return hunter.trace(
        hunter.Q(module="engine.runtime.effects", depth_lt=depth),
        action=hunter.CallPrinter(stream=stream),
    )


# ---------------------------------------------------------------------------
# Preset: phases — phase transitions and cascading
# ---------------------------------------------------------------------------


def trace_phases(*, output: str | None = None):
    """Trace phase advance logic: cascades, skips, entry effects."""
    stream = open(output, "w") if output else sys.stderr
    return hunter.trace(
        hunter.Q(
            module="engine.runtime.core",
            function_in=[
                "advance_phase",
                "_advance_one",
                "_run_entry_effects",
                "_should_skip",
                "_cleanup_phase",
                "run_setup",
                "check_victory",
            ],
            kind_in=["call", "return"],
        ),
        action=hunter.CallPrinter(stream=stream),
    )


# ---------------------------------------------------------------------------
# Preset: mcp — MCP request/response flow
# ---------------------------------------------------------------------------


def trace_mcp(*, output: str | None = None):
    """Trace MCP tool dispatch and handler flow."""
    stream = open(output, "w") if output else sys.stderr
    return hunter.trace(
        hunter.Q(
            module="mcp.server",
            function_in=[
                "handle_request",
                "_handle_tools_call",
                "_handle_platform_tool",
                "_handle_game_tool",
                "_tool_act",
                "_tool_wait_for_turn",
                "_tool_get_status",
                "_tool_available_actions",
                "_tool_advance_phase",
                "_tool_respond",
                "_tool_create_game",
                "_tool_join_game",
                "_exec_deal",
                "_exec_vote",
                "_exec_send",
            ],
            kind_in=["call", "return"],
        ),
        action=hunter.CallPrinter(stream=stream),
    )


# ---------------------------------------------------------------------------
# Preset: flow — structured JSONL game event logger
# ---------------------------------------------------------------------------


class FlowLogger(hunter.Action):
    """Write structured game events to JSONL file."""

    TRACKED_FUNCTIONS = frozenset(
        {
            "advance_phase",
            "_advance_one",
            "start_deal",
            "respond_to_deal",
            "start_vote",
            "cast_vote",
            "check_victory",
            "apply_effects",
            "execute_speech_act",
            "send_message",
        }
    )

    def __init__(self, path: str):
        self._file = open(path, "w")

    def __call__(self, event):
        if event.kind != "call":
            return
        if event.function not in self.TRACKED_FUNCTIONS:
            return
        record: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "fn": event.function,
            "mod": event.module,
            "depth": event.depth,
        }
        # Capture key arguments (safe subset)
        loc = event.locals
        for key in (
            "deal_id",
            "vote_id",
            "player_id",
            "actor_id",
            "option",
            "response",
            "phase",
            "speech_act_id",
            "ch_id",
        ):
            if key in loc:
                record[key] = repr(loc[key])
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

    def close(self):
        self._file.close()


class TracerHandle:
    """Wrapper that bundles a hunter tracer with a closeable resource."""

    def __init__(self, tracer, resource=None):
        self._tracer = tracer
        self._resource = resource

    def stop(self):
        self._tracer.stop()
        if self._resource:
            self._resource.close()


def trace_flow(*, output: str = "/tmp/parlameme_flow.jsonl"):
    """Log structured game events to JSONL for post-analysis."""
    logger = FlowLogger(output)
    tracer = hunter.trace(
        hunter.Q(module_startswith="engine.runtime") | hunter.Q(module="mcp.server"),
        action=logger,
    )
    return TracerHandle(tracer, logger)


# ---------------------------------------------------------------------------
# Preset: errors — exception snooper with backlog
# ---------------------------------------------------------------------------


def trace_errors(*, backlog: int = 20, output: str | None = None):
    """Catch exceptions with surrounding context. Finds swallowed errors."""
    stream = open(output, "w") if output else sys.stderr
    return hunter.trace(
        hunter.Q(module_startswith="engine") | hunter.Q(module_startswith="mcp"),
        action=hunter.ErrorSnooper(
            max_backlog=backlog,
            max_events=50,
            stream=stream,
        ),
    )


# ---------------------------------------------------------------------------
# Snap: one-shot state dump (no tracing)
# ---------------------------------------------------------------------------


def snapshot(state, compiled=None) -> str:
    """God-view state dump. No visibility filtering."""
    lines = [
        f"## State Snapshot",
        f"Phase: {state.phase} | Round: {state.round} | Status: {state.status}",
    ]

    if state.vars_:
        lines.append(f"\n### Vars")
        for k, v in state.vars_.items():
            lines.append(f"  {k}: {v}")

    lines.append(f"\n### Entities ({len(state.entities)})")
    for eid, entity in state.entities.items():
        active = "ACTIVE" if entity.active else "DEAD"
        res = ", ".join(f"{k}={v}" for k, v in entity.resources.items())
        attrs = ", ".join(f"{k}={v}" for k, v in entity.attrs_.items())
        parts = [f"  **{eid}** [{active}]"]
        if res:
            parts.append(f"    Resources: {res}")
        if attrs:
            parts.append(f"    Attrs: {attrs}")
        if entity.groups:
            parts.append(f"    Groups: {', '.join(entity.groups)}")
        lines.extend(parts)

    if state.pending_deals:
        lines.append(f"\n### Pending Deals ({len(state.pending_deals)})")
        for iid, pd in state.pending_deals.items():
            responses = {k: v for k, v in pd.responders.items()}
            lines.append(
                f"  {iid}: {pd.deal_id} by {pd.proposer} | "
                f"responses: {responses} | params: {pd.params}"
            )

    if state.pending_votes:
        lines.append(f"\n### Pending Votes ({len(state.pending_votes)})")
        for iid, pv in state.pending_votes.items():
            cast = len(pv.votes)
            total = len(pv.eligible)
            lines.append(
                f"  {iid}: {pv.vote_id} [{cast}/{total} voted] | "
                f"options: {pv.options} | votes: {dict(pv.votes)}"
            )

    if state.relations:
        lines.append(f"\n### Relations ({len(state.relations)})")
        for (a, b), rels in state.relations.items():
            lines.append(f"  {a} --{', '.join(rels)}--> {b}")

    if state.groups:
        lines.append(f"\n### Groups ({len(state.groups)})")
        for gid, group in state.groups.items():
            members = getattr(group, "members", group)
            if isinstance(members, (set, frozenset, list, tuple)):
                lines.append(f"  {gid}: {', '.join(str(m) for m in members)}")
            else:
                lines.append(f"  {gid}: {members}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _run_snap(args):
    """Run snap preset: create a game and dump state."""
    from engine.runtime.core import GameRuntime
    from games import REGISTRY

    game_id = args.game or "auction"
    compiled = REGISTRY.get(game_id)
    if not compiled:
        print(f"Unknown game: {game_id}. Available: {list(REGISTRY.keys())}")
        return

    n = args.players or compiled.min_players
    players = [f"p{i}" for i in range(n)]
    rt = GameRuntime(compiled)
    state = rt.start_game(players, seed=args.seed)
    state = rt.run_setup(state)

    # Advance extra phases if requested
    for _ in range(args.advance):
        state = rt.advance_phase(state)

    print(snapshot(state, compiled))


def _run_trace(preset_fn, args):
    """Start a trace, run a game to completion, stop trace."""
    from engine.runtime.core import GameRuntime
    from games import REGISTRY

    game_id = args.game or "auction"
    compiled = REGISTRY.get(game_id)
    if not compiled:
        print(f"Unknown game: {game_id}. Available: {list(REGISTRY.keys())}")
        return

    kwargs = {}
    if hasattr(args, "depth") and args.depth:
        kwargs["depth"] = args.depth
    if args.output:
        kwargs["output"] = args.output

    n = args.players or compiled.min_players
    players = [f"p{i}" for i in range(n)]
    rt = GameRuntime(compiled)

    tracer = preset_fn(**kwargs)
    try:
        state = rt.start_game(players, seed=args.seed)
        state = rt.run_setup(state)
        for _ in range(args.advance):
            state = rt.advance_phase(state)
    finally:
        tracer.stop()


def main():
    parser = argparse.ArgumentParser(
        description="Hunter tracing presets for Parlameme engine",
    )
    parser.add_argument(
        "preset",
        choices=[
            "deals",
            "effects",
            "phases",
            "mcp",
            "flow",
            "errors",
            "snap",
        ],
    )
    parser.add_argument("--depth", type=int, default=None)
    parser.add_argument("--output", "-o", default=None)
    parser.add_argument("--game", "-g", default="auction")
    parser.add_argument("--players", "-n", type=int, default=None)
    parser.add_argument("--seed", "-s", type=int, default=42)
    parser.add_argument(
        "--advance",
        "-a",
        type=int,
        default=1,
        help="Number of extra phase advances after setup",
    )
    args = parser.parse_args()

    PRESETS = {
        "deals": trace_deals,
        "effects": trace_effects,
        "phases": trace_phases,
        "mcp": trace_mcp,
        "flow": trace_flow,
        "errors": trace_errors,
    }

    if args.preset == "snap":
        _run_snap(args)
    elif args.preset == "flow":
        _run_trace(trace_flow, args)
    else:
        _run_trace(PRESETS[args.preset], args)


if __name__ == "__main__":
    main()
