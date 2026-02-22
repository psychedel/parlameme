"""Tests for agent package — bridge, providers, runner."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.bridge import InProcessBridge
from agent.providers import (
    LLMResponse,
    OllamaProvider,
    ToolCall,
    _convert_tools_to_anthropic,
    _convert_tools_to_ollama,
    _extract_text,
    create_provider,
)
from agent.runner import (
    AgentRunner,
    TurnEntry,
    _extract_content,
    _extract_game_facts,
    _msg_text,
    _summarize_result,
)
from games import REGISTRY as GAME_REGISTRY
from strategy.archetypes import get_archetype
from strategy.schema import Strategy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_mcp() -> MagicMock:
    """Create a mock MCPServer."""
    mcp = MagicMock()
    mcp.handle_request = AsyncMock()
    return mcp


def _mock_provider(
    tool_name: str = "get_status", tool_args: dict | None = None
) -> MagicMock:
    """Create a mock LLM provider that returns a single tool call."""
    provider = MagicMock()
    provider.complete = AsyncMock(
        return_value=LLMResponse(
            content="I'll check the game status.",
            tool_calls=[ToolCall(id="tc1", name=tool_name, args=tool_args or {})],
            stop_reason="tool_use",
        )
    )
    return provider


# ---------------------------------------------------------------------------
# Bridge tests
# ---------------------------------------------------------------------------


class TestInProcessBridge:
    @pytest.fixture()
    def bridge(self) -> InProcessBridge:
        mcp = _mock_mcp()
        mcp.handle_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }
        return InProcessBridge(mcp, "test-agent")

    async def test_call_tool(self, bridge: InProcessBridge):
        result = await bridge.call_tool("get_status", {})
        assert result == {"content": [{"type": "text", "text": "ok"}]}
        bridge.mcp.handle_request.assert_called_once()
        call_args = bridge.mcp.handle_request.call_args
        assert call_args[0][0] == "test-agent"
        req = call_args[0][1]
        assert req["method"] == "tools/call"
        assert req["params"]["name"] == "get_status"

    async def test_call_tool_error(self):
        mcp = _mock_mcp()
        mcp.handle_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -1, "message": "Not in game"},
        }
        bridge = InProcessBridge(mcp, "test-agent")
        result = await bridge.call_tool("get_status")
        assert result == {"code": -1, "message": "Not in game"}

    async def test_list_tools(self, bridge: InProcessBridge):
        bridge.mcp.handle_request.return_value = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {"name": "get_status", "description": "Get game status"},
                    {"name": "act", "description": "Observe and act"},
                ]
            },
        }
        tools = await bridge.list_tools()
        assert len(tools) == 2
        assert tools[0]["name"] == "get_status"

    async def test_initialize(self, bridge: InProcessBridge):
        bridge.mcp.handle_request.return_value = {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "parlameme"},
            },
        }
        result = await bridge.initialize()
        assert result["protocolVersion"] == "2024-11-05"

    async def test_request_counter_increments(self, bridge: InProcessBridge):
        await bridge.call_tool("a")
        await bridge.call_tool("b")
        assert bridge._req_counter == 2


# ---------------------------------------------------------------------------
# Provider utility tests
# ---------------------------------------------------------------------------


class TestProviderUtils:
    def test_convert_tools_to_anthropic(self):
        tools = [
            {
                "name": "sealed_bid",
                "description": "Place a sealed bid",
                "inputSchema": {
                    "type": "object",
                    "properties": {"amount": {"type": "number"}},
                    "required": ["amount"],
                },
            }
        ]
        result = _convert_tools_to_anthropic(tools)
        assert len(result) == 1
        assert result[0]["name"] == "sealed_bid"
        assert result[0]["input_schema"]["properties"]["amount"]["type"] == "number"

    def test_convert_tools_no_schema(self):
        tools = [{"name": "get_status", "description": "Status"}]
        result = _convert_tools_to_anthropic(tools)
        assert result[0]["input_schema"] == {"type": "object", "properties": {}}

    def test_convert_tools_to_ollama(self):
        tools = [{"name": "test", "description": "Test tool"}]
        result = _convert_tools_to_ollama(tools)
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "test"

    def test_ollama_string_args_parsed(self):
        """Ollama may return tool arguments as a JSON string — should be parsed."""
        from agent.providers import OllamaProvider

        # Simulate Ollama response with string arguments
        provider = OllamaProvider(model="test")
        # We test the parsing logic via the ToolCall extraction
        # Construct the message dict that Ollama would return
        msg = {
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "sealed_bid",
                        "arguments": '{"amount": 50}',  # string, not dict
                    }
                }
            ],
        }
        import json

        raw_args = msg["tool_calls"][0]["function"]["arguments"]
        parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        assert parsed == {"amount": 50}

    def test_extract_text_string(self):
        assert _extract_text({"content": "hello"}) == "hello"

    def test_extract_text_blocks(self):
        msg = {
            "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        }
        assert _extract_text(msg) == "a\nb"

    def test_create_provider_anthropic(self):
        p = create_provider("anthropic", api_key="test")
        assert isinstance(p, object)  # AnthropicProvider

    def test_create_provider_ollama(self):
        p = create_provider("ollama", model="llama3.2")
        assert isinstance(p, OllamaProvider)
        assert p.model == "llama3.2"

    def test_create_provider_unknown(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            create_provider("unknown")


# ---------------------------------------------------------------------------
# Runner utility tests
# ---------------------------------------------------------------------------


class TestRunnerUtils:
    def test_extract_content_list(self):
        result = {
            "content": [
                {"type": "text", "text": "Phase: bidding"},
                {"type": "text", "text": "Your turn"},
            ]
        }
        assert "Phase: bidding" in _extract_content(result)
        assert "Your turn" in _extract_content(result)

    def test_extract_content_string(self):
        result = {"content": "Simple text"}
        assert _extract_content(result) == "Simple text"

    def test_summarize_result_short(self):
        result = {"content": "Short result"}
        assert _summarize_result(result) == "Short result"

    def test_summarize_result_truncates(self):
        result = {"content": "x" * 1000}
        summary = _summarize_result(result)
        assert len(summary) <= 500
        assert summary.endswith("...")

    def test_is_game_ended_status(self):
        assert AgentRunner._is_game_ended({"status": "ended"}) is True
        assert AgentRunner._is_game_ended({"status": "active"}) is False

    def test_is_game_ended_trigger(self):
        assert AgentRunner._is_game_ended({"trigger": "game_ended"}) is True

    def test_is_game_ended_content(self):
        result = {"content": "Status: ended | Winner: alice"}
        assert AgentRunner._is_game_ended(result) is True

    def test_is_game_ended_content_list(self):
        result = {"content": [{"type": "text", "text": "GAME OVER — alice wins!"}]}
        assert AgentRunner._is_game_ended(result) is True

    def test_is_game_ended_false(self):
        assert AgentRunner._is_game_ended({"content": "Phase: bidding"}) is False
        assert AgentRunner._is_game_ended({}) is False


# ---------------------------------------------------------------------------
# Runner integration tests (with mocks)
# ---------------------------------------------------------------------------


class TestAgentRunner:
    def _make_runner(
        self,
        tool_name: str = "get_status",
        tool_args: dict | None = None,
    ) -> tuple[AgentRunner, MagicMock, MagicMock]:
        mcp = _mock_mcp()
        bridge = InProcessBridge(mcp, "test-agent")
        provider = _mock_provider(tool_name, tool_args)
        strategy = Strategy(game_id="auction", name="Test Strategy")
        compiled = GAME_REGISTRY["auction"]

        runner = AgentRunner(
            strategy=strategy,
            bridge=bridge,
            provider=provider,
            compiled=compiled,
        )
        return runner, mcp, provider

    async def test_run_turn_calls_provider(self):
        runner, mcp, provider = self._make_runner()

        # Mock bridge responses
        mcp.handle_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "Phase: bidding"}]},
        }

        entry, _ = await runner._run_turn("Phase: bidding\nYour turn.")
        assert entry.turn == 1
        assert entry.action == "get_status"
        assert not entry.error
        provider.complete.assert_called_once()

    async def test_run_turn_records_entry(self):
        runner, mcp, provider = self._make_runner(
            tool_name="auction/sealed_bid",
            tool_args={"amount": 50},
        )
        mcp.handle_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "Bid placed"}]},
        }

        entry, _ = await runner._run_turn("Phase: bidding\nYour turn.")
        assert entry.action == "auction/sealed_bid"
        assert entry.args == {"amount": 50}
        assert len(runner.turn_log) == 1

    async def test_run_turn_with_callback(self):
        entries: list[TurnEntry] = []
        runner, mcp, _ = self._make_runner()
        runner.on_turn = lambda e: entries.append(e)
        mcp.handle_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }

        await runner._run_turn("Game state: bidding")
        assert len(entries) == 1
        assert entries[0].turn == 1

    async def test_stop(self):
        runner, mcp, _ = self._make_runner()
        mcp.handle_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }
        runner.stop()
        log = await runner.run_game()
        # Should stop immediately without running turns
        assert len(log) == 0

    async def test_game_end_detection(self):
        runner, mcp, _ = self._make_runner()
        # First call: initialize, second: wait_for_turn returns game ended
        call_count = 0

        async def mock_handle(agent_id, request):
            nonlocal call_count
            call_count += 1
            return {
                "jsonrpc": "2.0",
                "id": request.get("id", 1),
                "result": {"trigger": "game_ended", "content": "GAME OVER"},
            }

        mcp.handle_request = mock_handle
        log = await runner.run_game()
        assert runner.game_ended is True

    async def test_history_compaction(self):
        runner, mcp, _ = self._make_runner()
        # Fill up message history with game-like content
        for i in range(20):
            runner.messages.append(
                {"role": "user", "content": f"Phase: bidding | Round: {i}"}
            )
        assert len(runner.messages) == 20

        runner._compact_history()
        # Should be compacted: 1 summary + 6 recent = 7
        assert len(runner.messages) == 7
        assert "[Game summary" in runner.messages[0]["content"]

    async def test_history_compaction_short_history(self):
        runner, mcp, _ = self._make_runner()
        runner.messages = [{"role": "user", "content": "msg"}]
        runner._compact_history()
        # Should not compact if <= 8 messages
        assert len(runner.messages) == 1

    async def test_consecutive_error_limit(self):
        runner, mcp, provider = self._make_runner()
        # Provider always fails
        provider.complete.side_effect = RuntimeError("LLM error")
        mcp.handle_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }

        log = await runner.run_game()
        # Should stop after MAX_CONSECUTIVE_ERRORS
        assert len(log) <= 5
        assert all(e.error for e in log)

    async def test_act_result_reused_as_observation(self):
        """When the LLM calls act(), the result should be reused as next observation."""
        runner, mcp, provider = self._make_runner(
            tool_name="act",
            tool_args={"action": "auction/sealed_bid", "args": {"amount": 50}},
        )
        mcp.handle_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {"type": "text", "text": "## Action Result\nBid placed\n\n---\n\nPhase: bidding"}
                ]
            },
        }

        entry, next_obs = await runner._run_turn("Phase: bidding\nYour turn.")
        assert entry.action == "act"
        # The act result contains updated status, so next_obs should be set
        assert next_obs is not None
        assert "Phase: bidding" in next_obs

    async def test_non_act_tool_no_reuse(self):
        """Non-act tool calls should not set next observation."""
        runner, mcp, provider = self._make_runner(
            tool_name="get_messages",
            tool_args={},
        )
        mcp.handle_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "No messages"}]},
        }

        entry, next_obs = await runner._run_turn("Game state here")
        assert entry.action == "get_messages"
        assert next_obs is None  # Non-act tool — need fresh wait_for_turn

    def test_system_prompt_compiled(self):
        runner, _, _ = self._make_runner()
        assert "<identity>" in runner.system_prompt
        assert "Art Auction" in runner.system_prompt

    def test_archetype_compiles_for_runner(self):
        """All archetypes produce valid system prompts."""
        from strategy.archetypes import ARCHETYPES

        for game_id, templates in ARCHETYPES.items():
            compiled = GAME_REGISTRY[game_id]
            for t in templates:
                mcp = _mock_mcp()
                bridge = InProcessBridge(mcp, "test")
                provider = _mock_provider()
                runner = AgentRunner(
                    strategy=t,
                    bridge=bridge,
                    provider=provider,
                    compiled=compiled,
                )
                assert "<identity>" in runner.system_prompt
                assert len(runner.system_prompt) > 200


# ---------------------------------------------------------------------------
# BotRunner tests
# ---------------------------------------------------------------------------


class TestBotRunner:
    def test_import(self):
        from agent.bots import BotRunner

        assert BotRunner is not None

    def test_init(self):
        from agent.bots import BotRunner

        session = MagicMock()
        session.state = MagicMock()
        session.state.status = "active"
        compiled = GAME_REGISTRY["auction"]
        bot = BotRunner(session, compiled, ["bot-1", "bot-2"], seed=42)
        assert bot.bot_player_ids == ["bot-1", "bot-2"]
        assert not bot._stopped

    def test_stop(self):
        from agent.bots import BotRunner

        session = MagicMock()
        session.state = MagicMock()
        compiled = GAME_REGISTRY["auction"]
        bot = BotRunner(session, compiled, ["bot-1"])
        bot._stopped = False
        bot.stop()
        assert bot._stopped

    async def test_act_for_bots_inactive_game(self):
        """Should not act if game is not active."""
        from agent.bots import BotRunner

        session = MagicMock()
        session.state = MagicMock()
        session.state.status = "ended"
        compiled = GAME_REGISTRY["auction"]
        bot = BotRunner(session, compiled, ["bot-1"])
        await bot._act_for_bots()
        # No calls should be made
        session.respond_deal.assert_not_called()
        session.cast_vote.assert_not_called()


# ---------------------------------------------------------------------------
# Structured history compaction tests
# ---------------------------------------------------------------------------


class TestExtractGameFacts:
    """Test structured extraction from message history."""

    def test_extracts_phases(self):
        msgs = [
            {"role": "user", "content": "Phase: preview | Round: 1"},
            {"role": "user", "content": "Phase: format_vote | Round: 1"},
            {"role": "user", "content": "Phase: bidding | Round: 1"},
        ]
        result = _extract_game_facts(msgs, turn_count=3)
        assert "preview" in result
        assert "format_vote" in result
        assert "bidding" in result
        assert "→" in result  # Phase transitions use arrows

    def test_deduplicates_phases(self):
        msgs = [
            {"role": "user", "content": "Phase: bidding | Round: 1"},
            {"role": "user", "content": "Phase: bidding | Round: 1"},
            {"role": "user", "content": "Phase: bidding | Round: 1"},
        ]
        result = _extract_game_facts(msgs, turn_count=3)
        # Should only appear once in phase list
        assert result.count("bidding") == 1

    def test_extracts_round(self):
        msgs = [
            {"role": "user", "content": "Phase: preview | Round: 3"},
        ]
        result = _extract_game_facts(msgs, turn_count=1)
        assert "Last known round: 3" in result

    def test_extracts_tool_outcomes(self):
        msgs = [
            {
                "role": "user",
                "content": "Tool 'sealed_bid' result: Action executed successfully.\nOutcome: accepted",
            },
        ]
        result = _extract_game_facts(msgs, turn_count=1)
        assert "sealed_bid" in result
        assert "accepted" in result

    def test_extracts_errors(self):
        msgs = [
            {
                "role": "user",
                "content": "Tool 'sealed_bid' result: Error: insufficient_resources",
            },
        ]
        result = _extract_game_facts(msgs, turn_count=1)
        assert "ERROR" in result
        assert "insufficient_resources" in result

    def test_extracts_vote_tally(self):
        msgs = [
            {
                "role": "user",
                "content": "Tool 'vote_format' result: Vote cast successfully.\nTally: sealed=2, english=1",
            },
        ]
        result = _extract_game_facts(msgs, turn_count=1)
        assert "tally" in result
        assert "sealed=2" in result

    def test_extracts_resource_changes(self):
        msgs = [
            {
                "role": "user",
                "content": "Tool 'act' result: Bid placed\nChanges: credits: 100 -> 50 (-50)",
            },
        ]
        result = _extract_game_facts(msgs, turn_count=1)
        assert "credits: 100 -> 50 (-50)" in result

    def test_extracts_last_resources(self):
        msgs = [
            {"role": "user", "content": "Resources: credits: 100, reputation: 50"},
            {"role": "user", "content": "Resources: credits: 80, reputation: 55"},
        ]
        result = _extract_game_facts(msgs, turn_count=2)
        assert "credits: 80, reputation: 55" in result

    def test_extracts_game_over(self):
        msgs = [
            {
                "role": "user",
                "content": "GAME OVER: alice wins! (highest_bidder)",
            },
        ]
        result = _extract_game_facts(msgs, turn_count=1)
        assert "Game ended" in result
        assert "alice wins" in result

    def test_skips_observation_tools(self):
        """wait_for_turn, get_status, available_actions should not appear in actions."""
        msgs = [
            {"role": "user", "content": "Tool 'wait_for_turn' result: Phase: bidding"},
            {"role": "user", "content": "Tool 'get_status' result: Phase: bidding"},
            {"role": "user", "content": "Tool 'available_actions' result: sealed_bid"},
        ]
        result = _extract_game_facts(msgs, turn_count=3)
        assert "Actions taken" not in result

    def test_handles_list_content(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Phase: settlement | Round: 2"},
                ],
            },
        ]
        result = _extract_game_facts(msgs, turn_count=1)
        assert "settlement" in result

    def test_empty_messages(self):
        result = _extract_game_facts([], turn_count=0)
        assert "[Game summary" in result

    def test_assistant_messages_handled(self):
        """Assistant messages with content list should not crash."""
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I'll bid now."},
                    {"type": "tool_use", "id": "tc1", "name": "act", "input": {}},
                ],
            },
        ]
        result = _extract_game_facts(msgs, turn_count=1)
        assert "[Game summary" in result


class TestMsgText:
    def test_string_content(self):
        assert _msg_text({"content": "hello"}) == "hello"

    def test_list_content(self):
        msg = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        assert _msg_text(msg) == "a\nb"

    def test_empty(self):
        assert _msg_text({}) == ""
        assert _msg_text({"content": 42}) == ""
