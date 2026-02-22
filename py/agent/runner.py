"""Agent runner — game-playing loop with compiled strategy.

Simple loop: observe → think → act → wait.
No framework (LangChain/LangGraph) — the game provides structure via MCP tools.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.bridge import InProcessBridge
from agent.providers import LLMProvider, LLMResponse, ToolCall
from engine.runtime.state import CompiledGame
from strategy.compiler import compile_strategy
from strategy.schema import Strategy

log = logging.getLogger(__name__)

MAX_TURNS = 100
MAX_CONSECUTIVE_ERRORS = 5
HISTORY_COMPACT_INTERVAL = 10
WAIT_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Turn log entry (for UI display)
# ---------------------------------------------------------------------------


@dataclass
class TurnEntry:
    """Single turn in the agent's decision log."""

    turn: int
    timestamp: float
    action: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    result_summary: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Agent Runner
# ---------------------------------------------------------------------------


class AgentRunner:
    """Run an LLM agent with a compiled strategy in a game session.

    The runner is designed to be launched as a background asyncio task.
    It calls MCP tools via InProcessBridge and an LLM via the provider.
    """

    def __init__(
        self,
        strategy: Strategy,
        bridge: InProcessBridge,
        provider: LLMProvider,
        compiled: CompiledGame,
        on_turn: Callable[[TurnEntry], Any] | None = None,
    ):
        self.strategy = strategy
        self.bridge = bridge
        self.provider = provider
        self.compiled = compiled
        self.system_prompt = compile_strategy(strategy, compiled)
        self.on_turn = on_turn  # Callback for UI updates

        self.messages: list[dict[str, Any]] = []
        self.turn_log: list[TurnEntry] = []
        self.turn_count = 0
        self.game_ended = False
        self._stopped = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_game(self) -> list[TurnEntry]:
        """Main game loop — run until game ends, max turns, or stopped.

        Returns the full turn log.
        """
        await self.bridge.initialize()
        consecutive_errors = 0

        # Initial observation to seed the first turn
        pending_observation: str | None = None

        while self.turn_count < MAX_TURNS and not self._stopped:
            try:
                if pending_observation is None:
                    # Wait for our turn (long-poll) — returns full status
                    wait_result = await self.bridge.call_tool(
                        "wait_for_turn", {"timeout": WAIT_TIMEOUT}
                    )
                    if self._is_game_ended(wait_result):
                        self.game_ended = True
                        break
                    pending_observation = _extract_content(wait_result)

                entry, pending_observation = await self._run_turn(
                    pending_observation
                )
                if entry.error:
                    consecutive_errors += 1
                    pending_observation = None  # Re-observe on next turn
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        log.warning(
                            "Agent %s: %d consecutive errors, stopping",
                            self.bridge.agent_id,
                            consecutive_errors,
                        )
                        break
                else:
                    consecutive_errors = 0

            except asyncio.CancelledError:
                log.info("Agent %s: cancelled", self.bridge.agent_id)
                break
            except Exception as exc:
                log.exception("Agent %s: unexpected error", self.bridge.agent_id)
                consecutive_errors += 1
                pending_observation = None  # Re-observe on next turn
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    break
                # Brief pause before retry
                await asyncio.sleep(1.0)

            # Compact history periodically to prevent context overflow
            if self.turn_count > 0 and self.turn_count % HISTORY_COMPACT_INTERVAL == 0:
                self._compact_history()

        return self.turn_log

    def stop(self) -> None:
        """Signal the runner to stop after the current turn."""
        self._stopped = True

    # ------------------------------------------------------------------
    # Turn execution
    # ------------------------------------------------------------------

    async def _run_turn(
        self, observation: str
    ) -> tuple[TurnEntry, str | None]:
        """Execute one think-act cycle using the provided observation.

        Returns (entry, next_observation) — if the tool result already contains
        updated status, next_observation is set so we skip the wait_for_turn call.
        """
        self.turn_count += 1
        entry = TurnEntry(turn=self.turn_count, timestamp=time.time())
        next_obs: str | None = None

        try:
            # 1. Add observation to conversation
            self.messages.append({"role": "user", "content": observation})

            # 2. Get available tools
            tools = await self.bridge.list_tools()

            # 3. Ask LLM for decision
            response = await self.provider.complete(
                messages=self.messages,
                tools=tools,
                system=self.system_prompt,
            )

            # 4. Record assistant message
            self.messages.append(_response_to_message(response))
            entry.reasoning = response.content

            # 5. Execute tool calls
            if response.tool_calls:
                tc = response.tool_calls[0]  # Execute first tool call
                entry.action = tc.name
                entry.args = tc.args

                result = await self.bridge.call_tool(tc.name, tc.args)
                result_text = _summarize_result(result)
                entry.result_summary = result_text

                # Add tool result to conversation
                self.messages.append(
                    {
                        "role": "user",
                        "content": f"Tool '{tc.name}' result: {result_text}",
                    }
                )

                if self._is_game_ended(result):
                    self.game_ended = True
                elif tc.name == "act" and result_text:
                    # act() returns updated status — reuse as next observation
                    next_obs = result_text

                # Handle additional tool calls if LLM returned multiple
                if not self.game_ended:
                    for tc_extra in response.tool_calls[1:]:
                        extra_result = await self.bridge.call_tool(
                            tc_extra.name, tc_extra.args
                        )
                        self.messages.append(
                            {
                                "role": "user",
                                "content": f"Tool '{tc_extra.name}' result: {_summarize_result(extra_result)}",
                            }
                        )
                        if self._is_game_ended(extra_result):
                            self.game_ended = True
                            break
            else:
                # LLM didn't use any tools — just thinking
                entry.action = "think"

        except Exception as exc:
            entry.error = str(exc)
            log.warning(
                "Agent %s turn %d error: %s",
                self.bridge.agent_id,
                self.turn_count,
                exc,
            )

        self._record(entry)
        return entry, next_obs

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _record(self, entry: TurnEntry) -> None:
        """Record turn and notify callback."""
        self.turn_log.append(entry)
        if self.on_turn:
            try:
                self.on_turn(entry)
            except Exception:
                pass  # Don't let UI callback errors crash the agent

    def _compact_history(self) -> None:
        """Summarize old messages into structured game facts.

        Extracts key information (phase, resources, actions, outcomes)
        from old messages and replaces them with a concise fact summary.
        Keeps the last 6 messages intact for recent context.
        """
        if len(self.messages) <= 8:
            return

        # Keep last 6 messages
        old = self.messages[:-6]
        recent = self.messages[-6:]

        summary = _extract_game_facts(old, self.turn_count)
        self.messages = [{"role": "user", "content": summary}] + recent

    @staticmethod
    def _is_game_ended(result: dict[str, Any]) -> bool:
        """Check if a tool result indicates the game has ended."""
        if not isinstance(result, dict):
            return False
        # Check in content text
        content = result.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if "Status: ended" in text or "GAME OVER" in text:
                        return True
        elif isinstance(content, str):
            if "Status: ended" in content or "GAME OVER" in content:
                return True
        # Check direct status field
        if result.get("status") == "ended":
            return True
        # Check trigger from wait_for_turn
        if result.get("trigger") == "game_ended":
            return True
        return False


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _extract_content(result: dict[str, Any]) -> str:
    """Extract text content from MCP tool result."""
    content = result.get("content", "")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    if isinstance(content, str):
        return content
    return str(result)


def _response_to_message(response: LLMResponse) -> dict[str, Any]:
    """Convert LLM response to a conversation message for history."""
    content_parts: list[dict[str, Any]] = []
    if response.content:
        content_parts.append({"type": "text", "text": response.content})
    for tc in response.tool_calls:
        content_parts.append(
            {
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.args,
            }
        )
    return {"role": "assistant", "content": content_parts or response.content}


def _summarize_result(result: dict[str, Any]) -> str:
    """Create a brief summary of a tool result for conversation context."""
    text = _extract_content(result)
    if len(text) > 500:
        return text[:497] + "..."
    return text or str(result)[:200]


# ---------------------------------------------------------------------------
# Structured history compaction
# ---------------------------------------------------------------------------

import re

# Patterns for extracting game facts from message text
_RE_PHASE = re.compile(r"Phase:\s*(\S+)")
_RE_ROUND = re.compile(r"Round:\s*(\d+)")
_RE_STATUS = re.compile(r"Status:\s*(\S+)")
_RE_RESOURCES = re.compile(r"Resources:\s*(.+)")
_RE_TOOL = re.compile(r"Tool '(\w+)' result:")
_RE_OUTCOME = re.compile(r"Outcome:\s*(.+)")
_RE_ERROR = re.compile(r"Error:\s*(.+)")
_RE_CHANGES = re.compile(r"Changes:\s*(.+)")
_RE_GAME_OVER = re.compile(r"GAME OVER:\s*(.+)")
_RE_VOTE_CAST = re.compile(r"Vote cast successfully")
_RE_VOTE_TALLY = re.compile(r"Tally:\s*(.+)")


def _extract_game_facts(messages: list[dict[str, Any]], turn_count: int) -> str:
    """Extract structured game facts from old messages.

    Parses phase transitions, resource snapshots, action outcomes, and vote
    results to build a concise summary that preserves decision-relevant info.
    """
    phases_seen: list[str] = []
    last_resources: str = ""
    last_round: str = ""
    actions: list[str] = []  # "tool_name → outcome" entries
    errors: list[str] = []
    resource_changes: list[str] = []
    game_over_info: str = ""

    for msg in messages:
        text = _msg_text(msg)
        if not text:
            continue

        # Track phase transitions
        m = _RE_PHASE.search(text)
        if m:
            phase = m.group(1).rstrip("|")
            if not phases_seen or phases_seen[-1] != phase:
                phases_seen.append(phase)

        # Track round
        m = _RE_ROUND.search(text)
        if m:
            last_round = m.group(1)

        # Last known resources
        m = _RE_RESOURCES.search(text)
        if m:
            last_resources = m.group(1).strip()

        # Tool calls with outcomes
        m_tool = _RE_TOOL.search(text)
        if m_tool:
            tool_name = m_tool.group(1)
            # Extract outcome from same message
            m_outcome = _RE_OUTCOME.search(text)
            m_vote = _RE_VOTE_CAST.search(text)
            m_tally = _RE_VOTE_TALLY.search(text)
            m_err = _RE_ERROR.search(text)

            if m_err:
                entry = f"{tool_name} -> ERROR: {m_err.group(1)[:60]}"
                errors.append(entry)
            elif m_tally:
                entry = f"{tool_name} -> tally: {m_tally.group(1)[:60]}"
                actions.append(entry)
            elif m_outcome:
                entry = f"{tool_name} -> {m_outcome.group(1)[:60]}"
                actions.append(entry)
            elif m_vote:
                entry = f"{tool_name} -> vote cast"
                actions.append(entry)
            elif tool_name not in ("wait_for_turn", "get_status", "available_actions"):
                # Skip observation-only tools from action log
                actions.append(tool_name)

        # Resource changes (deltas)
        m = _RE_CHANGES.search(text)
        if m:
            resource_changes.append(m.group(1).strip()[:80])

        # Game over
        m = _RE_GAME_OVER.search(text)
        if m:
            game_over_info = m.group(1).strip()

    # Build structured summary
    lines = [f"[Game summary — turns 1-{turn_count}, {len(messages)} messages compacted]"]

    if phases_seen:
        lines.append(f"Phases traversed: {' → '.join(phases_seen)}")
    if last_round:
        lines.append(f"Last known round: {last_round}")

    if actions:
        # Show all actions but cap at reasonable size
        shown = actions if len(actions) <= 12 else actions[-12:]
        lines.append(f"Actions taken ({len(actions)} total):")
        for a in shown:
            lines.append(f"  - {a}")

    if errors:
        shown = errors[-3:]
        lines.append(f"Errors encountered ({len(errors)} total):")
        for e in shown:
            lines.append(f"  - {e}")

    if resource_changes:
        lines.append(f"Resource changes: {'; '.join(resource_changes[-5:])}")

    if last_resources:
        lines.append(f"Last known resources: {last_resources}")

    if game_over_info:
        lines.append(f"Game ended: {game_over_info}")

    return "\n".join(lines)


def _msg_text(msg: dict[str, Any]) -> str:
    """Extract plain text from a message (handles both str and list content)."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text", "")
                if text:
                    parts.append(text)
        return "\n".join(parts)
    return ""
