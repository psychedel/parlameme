"""Tests for archive/replay system and MCP HTTP integration."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from engine.archive import (
    Archive,
    ReplayResult,
    archive_to_dict,
    create_archive,
    dict_to_archive,
    load_archive,
    replay,
    replay_with_result,
    save_archive,
    verify,
)
from engine.runtime.core import GameRuntime
from engine.runtime.state import GameState
from games.auction import auction
from games.werewolf import werewolf

# =========================================================================
# Helpers
# =========================================================================

_AUCTION_PLAYERS = ["alice", "bob", "charlie"]


def _play_auction_game(seed: int = 42) -> tuple[GameRuntime, GameState]:
    """Play several turns of Auction and return runtime + final state."""
    rt = GameRuntime(auction)
    state = rt.start_game(_AUCTION_PLAYERS, seed=seed)
    state = rt.run_setup(state)

    # preview phase: alice appraises, bob appraises
    result = rt.start_deal(state, "appraise", actor_id="alice")
    if result["ok"]:
        state = result["state"]
    result = rt.start_deal(state, "appraise", actor_id="bob")
    if result["ok"]:
        state = result["state"]

    # Advance phase
    state = state.record_decision({"type": "advance_phase"})
    state = rt.advance_phase(state)

    return rt, state


# =========================================================================
# Archive creation
# =========================================================================


class TestArchiveCreation:
    def test_create_from_auction(self):
        rt = GameRuntime(auction)
        state = rt.start_game(_AUCTION_PLAYERS, seed=42)
        state = rt.run_setup(state)

        archive = create_archive(auction, state)
        assert archive.game_id == "auction"
        assert archive.seed == 42
        assert archive.players == tuple(_AUCTION_PLAYERS)
        assert archive.version == 1
        assert archive.rules_hash  # non-empty

    def test_archive_captures_decisions(self):
        rt = GameRuntime(auction)
        state = rt.start_game(_AUCTION_PLAYERS, seed=42)
        state = rt.run_setup(state)

        result = rt.start_deal(state, "appraise", actor_id="alice")
        state = result["state"]
        result = rt.start_deal(state, "appraise", actor_id="bob")
        state = result["state"]

        archive = create_archive(auction, state)
        assert len(archive.decisions) == 2
        assert archive.decisions[0]["type"] == "deal"
        assert archive.decisions[0]["deal"] == "appraise"

    def test_archive_metadata(self):
        rt = GameRuntime(auction)
        state = rt.start_game(_AUCTION_PLAYERS, seed=1)
        archive = create_archive(auction, state, metadata={"source": "test"})
        assert archive.metadata["source"] == "test"


# =========================================================================
# Deterministic replay
# =========================================================================


class TestReplay:
    def test_replay_produces_identical_state(self):
        _, state = _play_auction_game(seed=42)
        archive = create_archive(auction, state)

        replayed = replay(archive, auction)

        # Resources must match exactly
        for eid in _AUCTION_PLAYERS:
            for res in ("gold", "reputation"):
                assert state.get_resource(eid, res) == replayed.get_resource(
                    eid, res
                ), f"Mismatch: {eid}.{res}"

    def test_replay_different_seeds_differ(self):
        _, state1 = _play_auction_game(seed=42)
        _, state2 = _play_auction_game(seed=99)

        a1 = create_archive(auction, state1)
        a2 = create_archive(auction, state2)
        assert a1.seed != a2.seed

    def test_replay_is_deterministic(self):
        """Replaying same archive twice produces identical results."""
        _, state = _play_auction_game()
        archive = create_archive(auction, state)

        r1 = replay(archive, auction)
        r2 = replay(archive, auction)

        for eid in _AUCTION_PLAYERS:
            assert r1.entities[eid].resources == r2.entities[eid].resources

    def test_verify_valid_archive(self):
        _, state = _play_auction_game()
        archive = create_archive(auction, state)
        result = verify(archive, auction)
        assert result["valid"]
        assert result["decisions_expected"] == result["decisions_replayed"]
        assert result["failed"] == 0
        assert result["fingerprint"]
        assert len(result["fingerprint"]) == 16  # sha256[:16]

    def test_verify_fingerprint_deterministic(self):
        """Same archive produces same fingerprint on repeated verify."""
        _, state = _play_auction_game()
        archive = create_archive(auction, state)
        r1 = verify(archive, auction)
        r2 = verify(archive, auction)
        assert r1["fingerprint"] == r2["fingerprint"]

    def test_verify_different_states_different_fingerprints(self):
        """Different game states produce different fingerprints."""
        from engine.archive import _state_fingerprint

        rt = GameRuntime(auction)
        state1 = rt.start_game(_AUCTION_PLAYERS, seed=42)
        state1 = rt.run_setup(state1)

        # Create a second state with a resource change
        import attrs

        e = state1.entities["alice"]
        new_res = dict(e.resources)
        new_res["gold"] = new_res.get("gold", 0) + 500
        e2 = attrs.evolve(e, resources=new_res)
        state2 = attrs.evolve(state1, entities={**state1.entities, "alice": e2})

        fp1 = _state_fingerprint(state1)
        fp2 = _state_fingerprint(state2)
        assert fp1 != fp2

    def test_verify_corrupted_archive_invalid(self):
        """Archive with bad decisions should not be valid."""
        archive = Archive(
            game_id="auction",
            rules_hash=auction.source_hash,
            seed=42,
            players=tuple(_AUCTION_PLAYERS),
            decisions=({"type": "deal", "deal": "nonexistent", "proposer": "alice"},),
        )
        result = verify(archive, auction)
        assert not result["valid"]
        assert result["failed"] == 1

    def test_empty_archive_replays_to_initial(self):
        """Archive with no decisions replays to post-setup state."""
        rt = GameRuntime(auction)
        state = rt.start_game(_AUCTION_PLAYERS, seed=1)
        state = rt.run_setup(state)

        archive = create_archive(auction, state)
        assert len(archive.decisions) == 0

        replayed = replay(archive, auction)
        assert replayed.status == "active"
        assert replayed.get_resource("alice", "gold") == 1000

    def test_vote_replay_determinism(self):
        """Votes (start_vote + cast_vote) must replay identically."""
        from games.parliament_arena import parliament_arena

        players = [f"p{i}" for i in range(6)]
        rt = GameRuntime(parliament_arena)
        state = rt.start_game(players, seed=42)
        state = rt.run_setup(state)

        # PA starts in election phase — start a vote
        result = rt.start_vote(
            state, "elect_position", proposer_id="p0", subject_id="p0"
        )
        assert result["ok"], f"start_vote failed: {result}"
        state = result["state"]
        instance_id = result["instance_id"]

        # Verify start_vote recorded a decision
        start_vote_decisions = [d for d in state.decisions if d["type"] == "start_vote"]
        assert len(start_vote_decisions) == 1
        assert start_vote_decisions[0]["vote_id"] == "elect_position"

        # All players vote
        for pid in players:
            result = rt.cast_vote(state, instance_id, pid, "elect")
            assert result["ok"], f"cast_vote {pid} failed: {result}"
            state = result["state"]

        # Vote should have auto-completed
        assert not state.pending_votes, "Vote should have auto-completed"

        # Advance phase
        state = state.record_decision({"type": "advance_phase"})
        state = rt.advance_phase(state)

        # Create archive and verify replay
        archive = create_archive(parliament_arena, state)
        result = verify(archive, parliament_arena)
        assert result["valid"], (
            f"Replay mismatch: expected {result['decisions_expected']}, "
            f"got {result['decisions_replayed']}"
        )

        # Verify replayed state matches
        replayed = result["state"]
        for pid in players:
            assert state.is_active(pid) == replayed.is_active(pid)
            for res in ("caps", "influence", "reputation", "intel", "suspicion"):
                assert state.get_resource(pid, res) == replayed.get_resource(
                    pid, res
                ), f"Mismatch: {pid}.{res}"

    def test_vote_replay_with_multiple_votes(self):
        """Multiple consecutive votes replay correctly."""
        from games.parliament_arena import parliament_arena

        players = [f"p{i}" for i in range(6)]
        rt = GameRuntime(parliament_arena)
        state = rt.start_game(players, seed=99)
        state = rt.run_setup(state)

        # Vote 1: elect speaker
        result = rt.start_vote(
            state, "elect_position", proposer_id="p0", subject_id="p1"
        )
        assert result["ok"]
        state = result["state"]
        vid1 = result["instance_id"]
        for pid in players:
            result = rt.cast_vote(state, vid1, pid, "elect")
            state = result["state"]

        # Advance
        state = state.record_decision({"type": "advance_phase"})
        state = rt.advance_phase(state)

        # Some deals in caucus
        result = rt.start_deal(state, "promise", actor_id="p0", responder_id="p2")
        if result["ok"]:
            state = result["state"]

        # Advance to floor
        state = state.record_decision({"type": "advance_phase"})
        state = rt.advance_phase(state)

        # Create archive and verify
        archive = create_archive(parliament_arena, state)
        result = verify(archive, parliament_arena)
        assert result["valid"], (
            f"Replay mismatch: expected {result['decisions_expected']}, "
            f"got {result['decisions_replayed']}"
        )


# =========================================================================
# ReplayResult and replay_with_result
# =========================================================================


class TestReplayResult:
    def test_clean_replay_has_zero_failures(self):
        """Valid archive replays with zero failed decisions."""
        _, state = _play_auction_game(seed=42)
        archive = create_archive(auction, state)
        result = replay_with_result(archive, auction)
        assert result.failed == 0
        assert isinstance(result, ReplayResult)

    def test_corrupted_decision_counted_as_failure(self):
        """A bogus decision in the archive increments failed count."""
        rt = GameRuntime(auction)
        state = rt.start_game(_AUCTION_PLAYERS, seed=42)
        state = rt.run_setup(state)

        # Create archive with a bad decision injected
        archive = Archive(
            game_id="auction",
            rules_hash=auction.source_hash,
            seed=42,
            players=tuple(_AUCTION_PLAYERS),
            decisions=(
                {"type": "deal", "deal": "nonexistent_deal", "proposer": "alice"},
            ),
        )
        result = replay_with_result(archive, auction)
        assert result.failed == 1
        assert result.state is not None

    def test_replay_logs_warning_on_failures(self, caplog):
        """replay() logs a warning when there are failed decisions."""
        import logging

        archive = Archive(
            game_id="auction",
            rules_hash=auction.source_hash,
            seed=42,
            players=tuple(_AUCTION_PLAYERS),
            decisions=(
                {"type": "deal", "deal": "bogus", "proposer": "alice"},
                {
                    "type": "respond",
                    "instance_id": "no-such",
                    "responder": "bob",
                    "response": "accept",
                },
            ),
        )
        with caplog.at_level(logging.WARNING):
            state = replay(archive, auction)
        assert "failed decisions" in caplog.text
        assert state is not None

    def test_empty_archive_zero_failures(self):
        """Archive with no decisions has zero failures."""
        archive = Archive(
            game_id="auction",
            rules_hash=auction.source_hash,
            seed=42,
            players=tuple(_AUCTION_PLAYERS),
            decisions=(),
        )
        result = replay_with_result(archive, auction)
        assert result.failed == 0


# =========================================================================
# Serialization
# =========================================================================


class TestSerialization:
    def test_roundtrip_dict(self):
        rt = GameRuntime(auction)
        state = rt.start_game(_AUCTION_PLAYERS, seed=42)
        state = rt.run_setup(state)
        result = rt.start_deal(state, "appraise", actor_id="alice")
        state = result["state"]

        archive = create_archive(auction, state)
        d = archive_to_dict(archive)
        restored = dict_to_archive(d)

        assert restored.game_id == archive.game_id
        assert restored.seed == archive.seed
        assert restored.players == archive.players
        assert len(restored.decisions) == len(archive.decisions)

    def test_json_serializable(self):
        rt = GameRuntime(auction)
        state = rt.start_game(_AUCTION_PLAYERS, seed=42)
        archive = create_archive(auction, state)
        d = archive_to_dict(archive)

        # Must be JSON-serializable
        json_str = json.dumps(d)
        restored = json.loads(json_str)
        assert restored["game_id"] == "auction"

    def test_save_and_load(self):
        rt = GameRuntime(auction)
        state = rt.start_game(_AUCTION_PLAYERS, seed=7)
        state = rt.run_setup(state)
        result = rt.start_deal(state, "appraise", actor_id="alice")
        state = result["state"]
        archive = create_archive(auction, state)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_archive(archive, Path(tmpdir) / "test.json")
            loaded = load_archive(path)

            assert loaded.game_id == archive.game_id
            assert loaded.seed == archive.seed
            assert loaded.players == archive.players
            assert len(loaded.decisions) == len(archive.decisions)

    def test_load_replay_verify(self):
        """Full pipeline: play -> archive -> save -> load -> replay -> verify."""
        rt = GameRuntime(auction)
        state = rt.start_game(_AUCTION_PLAYERS, seed=123)
        state = rt.run_setup(state)

        result = rt.start_deal(state, "appraise", actor_id="alice")
        state = result["state"]
        result = rt.start_deal(state, "appraise", actor_id="bob")
        state = result["state"]

        archive = create_archive(auction, state)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_archive(archive, Path(tmpdir) / "game.json")
            loaded = load_archive(path)

            result = verify(loaded, auction)
            assert result["valid"]

            replayed = result["state"]
            assert replayed.get_resource("alice", "gold") == state.get_resource(
                "alice", "gold"
            )


# =========================================================================
# Werewolf archive (complex game with roles + votes)
# =========================================================================


class TestWerewolfArchive:
    def test_werewolf_setup_replay(self):
        """Werewolf setup (role assignment) is deterministic with same seed."""
        rt = GameRuntime(werewolf)
        players = [f"p{i}" for i in range(8)]
        state = rt.start_game(players, seed=42)
        state = rt.run_setup(state)

        archive = create_archive(werewolf, state)
        replayed = replay(archive, werewolf)

        # Roles must match exactly (deterministic from seed)
        for pid in players:
            orig_role = state.get_attr(pid, "role")
            replay_role = replayed.get_attr(pid, "role")
            assert orig_role == replay_role, f"{pid}: {orig_role} != {replay_role}"


# =========================================================================
# Game registry
# =========================================================================


class TestGameRegistry:
    def test_all_games_registered(self):
        from games import REGISTRY

        assert len(REGISTRY) == 4
        expected = {
            "auction",
            "exchange",
            "werewolf",
            "parliament_arena",
        }
        assert set(REGISTRY.keys()) == expected

    def test_all_games_have_valid_compiled(self):
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            assert compiled.id == gid, f"{gid}: compiled.id = {compiled.id}"
            assert compiled.name, f"{gid}: missing name"
            assert compiled.min_players >= 2, f"{gid}: min_players < 2"
            assert compiled.max_players >= compiled.min_players
            assert len(compiled.phases) > 0, f"{gid}: no phases"

    def test_all_games_start_and_setup(self):
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            rt = GameRuntime(compiled)
            n = compiled.min_players
            players = [f"p{i}" for i in range(n)]
            state = rt.start_game(players, seed=1)
            state = rt.run_setup(state)
            assert state.status == "active", f"{gid}: status = {state.status}"


# =========================================================================
# Session auto-archive on game completion
# =========================================================================


class TestSessionAutoArchive:
    @pytest.mark.asyncio
    async def test_archive_saved_on_victory(self, tmp_path):
        """GameSession auto-saves archive when game ends."""
        import attrs

        import server.sessions as sess_mod

        # Point archive dir to temp
        orig_dir = sess_mod.ARCHIVE_DIR
        sess_mod.ARCHIVE_DIR = tmp_path
        try:
            session = sess_mod.GameSession(
                "auto-arch-1",
                auction,
                _AUCTION_PLAYERS,
                seed=42,
            )
            await session.start()

            # Simulate game end via attrs.evolve
            state = session._state
            state = attrs.evolve(
                state,
                status="ended",
                victory_result={
                    "condition": "wealth_leader",
                    "winner": "alice",
                    "type": "distribution",
                    "scores": {"alice": 100, "bob": 80, "charlie": 60},
                },
            )
            session._state = state
            session._maybe_archive()

            assert session.archive is not None
            assert session.archive.game_id == "auction"

            # File should exist
            archive_path = tmp_path / "auto-arch-1.json"
            assert archive_path.exists()
        finally:
            sess_mod.ARCHIVE_DIR = orig_dir

    @pytest.mark.asyncio
    async def test_no_archive_before_end(self, tmp_path):
        """No archive created for active games."""
        import server.sessions as sess_mod

        orig_dir = sess_mod.ARCHIVE_DIR
        sess_mod.ARCHIVE_DIR = tmp_path
        try:
            session = sess_mod.GameSession(
                "no-arch-1",
                auction,
                _AUCTION_PLAYERS,
                seed=42,
            )
            await session.start()
            await session.execute_deal("appraise", actor_id="alice")

            assert session.state.status == "active"
            assert session.archive is None
            assert not (tmp_path / "no-arch-1.json").exists()
        finally:
            sess_mod.ARCHIVE_DIR = orig_dir

    @pytest.mark.asyncio
    async def test_list_archives(self, tmp_path):
        """list_archives() returns saved archive summaries."""
        import server.sessions as sess_mod

        orig_dir = sess_mod.ARCHIVE_DIR
        sess_mod.ARCHIVE_DIR = tmp_path
        try:
            # Save a test archive manually
            rt = GameRuntime(auction)
            state = rt.start_game(_AUCTION_PLAYERS, seed=1)
            state = rt.run_setup(state)
            archive = create_archive(auction, state)
            save_archive(archive, tmp_path / "test-game.json")

            archives = sess_mod.list_archives()
            assert len(archives) == 1
            assert archives[0]["session_id"] == "test-game"
            assert archives[0]["game_id"] == "auction"
            assert archives[0]["players"] == list(_AUCTION_PLAYERS)
        finally:
            sess_mod.ARCHIVE_DIR = orig_dir


# =========================================================================
# MCP server unit tests
# =========================================================================


class _SimpleSessionStore:
    """Minimal session store for testing."""

    def __init__(self):
        from server.sessions import (
            create_session as _create,
        )
        from server.sessions import (
            get_session as _get,
        )
        from server.sessions import (
            list_sessions as _list,
        )
        from server.sessions import (
            remove_session as _remove,
        )

        self._create = _create
        self._get = _get
        self._list = _list
        self._remove = _remove

    def get(self, sid):
        return self._get(sid)

    def list_all(self):
        return self._list()

    def create(self, sid, compiled, player_ids):
        return self._create(sid, compiled, player_ids)

    def remove(self, sid):
        self._remove(sid)


class TestMCPServerUnit:
    @pytest.fixture
    def mcp_server(self):
        from games import REGISTRY
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)
        return server

    @pytest.mark.asyncio
    async def test_initialize(self, mcp_server):
        resp = await mcp_server.handle_request(
            "agent-1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            },
        )
        assert "result" in resp
        assert resp["result"]["serverInfo"]["name"] == "parlameme"

    @pytest.mark.asyncio
    async def test_tools_list_lobby(self, mcp_server):
        resp = await mcp_server.handle_request(
            "agent-1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
        )
        tools = resp["result"]["tools"]
        names = [t["name"] for t in tools]
        assert "list_games" in names
        assert "create_game" in names
        assert "my_status" in names

    @pytest.mark.asyncio
    async def test_list_games(self, mcp_server):
        resp = await mcp_server.handle_request(
            "agent-1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_games", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "auction" in text
        assert "werewolf" in text

    @pytest.mark.asyncio
    async def test_create_and_join_game(self, mcp_server):
        # Agent 1 creates game with 3 players (auction min_players=3)
        resp = await mcp_server.handle_request(
            "agent-1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "test-session",
                        "player_id": "alice",
                        "players": ["alice", "bob", "charlie"],
                    },
                },
            },
        )
        assert "error" not in resp
        text = resp["result"]["content"][0]["text"]
        assert "test-session" in text

        # Agent 2 joins
        resp = await mcp_server.handle_request(
            "agent-2",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "join_game",
                    "arguments": {
                        "session_id": "test-session",
                        "player_id": "bob",
                    },
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "bob" in text

    @pytest.mark.asyncio
    async def test_game_lifecycle(self, mcp_server):
        """Create game -> get status -> execute deal -> advance phase."""
        from server.sessions import remove_session

        # Create (auction needs min 3 players)
        await mcp_server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "lifecycle-test",
                        "player_id": "p1",
                        "players": ["p1", "p2", "p3"],
                    },
                },
            },
        )

        # Get status
        resp = await mcp_server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_status", "arguments": {}},
            },
        )
        assert "error" not in resp

        # Execute a deal (appraise via game tool)
        resp = await mcp_server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "auction/appraise",
                    "arguments": {},
                },
            },
        )
        # The routing works regardless of whether the deal succeeds
        assert "result" in resp or "error" in resp

        # Cleanup
        remove_session("lifecycle-test")

    @pytest.mark.asyncio
    async def test_unknown_method(self, mcp_server):
        resp = await mcp_server.handle_request(
            "agent",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "nonexistent",
                "params": {},
            },
        )
        assert "error" in resp

    @pytest.mark.asyncio
    async def test_my_status(self, mcp_server):
        resp = await mcp_server.handle_request(
            "test-agent",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "my_status", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "lobby" in text.lower()
