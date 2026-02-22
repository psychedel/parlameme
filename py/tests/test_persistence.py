"""Tests for session persistence store."""

import json
import time

import pytest

from server.persistence import SessionStore


def test_track_and_flush(tmp_path):
    path = tmp_path / "sessions.json"
    store = SessionStore(path=path)

    store.track("game-1", "auction", ["alice", "bob", "charlie"])
    store.flush()

    assert path.exists()
    data = json.loads(path.read_text())
    assert "game-1" in data
    assert data["game-1"]["game_type"] == "auction"
    assert data["game-1"]["players"] == ["alice", "bob", "charlie"]


def test_remove(tmp_path):
    path = tmp_path / "sessions.json"
    store = SessionStore(path=path)

    store.track("game-1", "auction", ["alice", "bob", "charlie"])
    store.track("game-2", "werewolf", ["a", "b", "c", "d", "e", "f", "g", "h"])
    store.remove("game-1")
    store.flush()

    data = json.loads(path.read_text())
    assert "game-1" not in data
    assert "game-2" in data


def test_touch_updates_activity(tmp_path):
    path = tmp_path / "sessions.json"
    store = SessionStore(path=path)

    store.track("game-1", "auction", ["alice", "bob", "charlie"])
    t1 = store._data["game-1"]["last_activity"]

    # Simulate time passing
    store._data["game-1"]["last_activity"] = t1 - 10
    store.touch("game-1")
    t2 = store._data["game-1"]["last_activity"]

    assert t2 > t1 - 10


def test_load_stale_detection(tmp_path):
    path = tmp_path / "sessions.json"
    now = time.time()

    # Pre-write data with one stale and one active session
    data = {
        "stale-game": {
            "game_type": "auction",
            "players": ["a", "b", "c"],
            "created_at": now - 7200,
            "last_activity": now - 7200,  # 2 hours ago
        },
        "active-game": {
            "game_type": "werewolf",
            "players": ["a", "b", "c", "d", "e", "f", "g", "h"],
            "created_at": now - 300,
            "last_activity": now - 300,  # 5 min ago
        },
    }
    path.write_text(json.dumps(data))

    store = SessionStore(path=path)
    recovery = store.load()

    assert "stale-game" in recovery["stale"]
    assert "active-game" in recovery["active"]
    assert "stale-game" not in recovery["active"]
    assert "active-game" not in recovery["stale"]


def test_load_missing_file(tmp_path):
    path = tmp_path / "nonexistent.json"
    store = SessionStore(path=path)
    recovery = store.load()

    assert recovery == {"stale": {}, "active": {}}


def test_load_corrupt_file(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("not valid json {{}")

    store = SessionStore(path=path)
    recovery = store.load()

    assert recovery == {"stale": {}, "active": {}}


def test_sessions_property(tmp_path):
    path = tmp_path / "sessions.json"
    store = SessionStore(path=path)

    store.track("g1", "auction", ["a", "b", "c"])
    store.track("g2", "werewolf", ["a", "b", "c", "d", "e", "f", "g", "h"])

    sessions = store.sessions
    assert len(sessions) == 2
    assert "g1" in sessions
    assert "g2" in sessions


def test_flush_idempotent(tmp_path):
    path = tmp_path / "sessions.json"
    store = SessionStore(path=path)

    store.track("g1", "auction", ["a", "b", "c"])
    store.flush()
    store.flush()  # second flush should be no-op (not dirty)

    data = json.loads(path.read_text())
    assert "g1" in data


def test_remove_nonexistent(tmp_path):
    """Removing a non-existent session should be a no-op."""
    path = tmp_path / "sessions.json"
    store = SessionStore(path=path)
    store.remove("does-not-exist")  # should not raise
    assert not store._dirty


def test_track_with_seed(tmp_path):
    path = tmp_path / "sessions.json"
    store = SessionStore(path=path)
    store.track("g1", "auction", ["a", "b", "c"], seed=99)
    store.flush()

    data = json.loads(path.read_text())
    assert data["g1"]["seed"] == 99


def test_touch_with_decisions(tmp_path):
    path = tmp_path / "sessions.json"
    store = SessionStore(path=path)
    store.track("g1", "auction", ["a", "b", "c"])

    decisions = ({"type": "deal", "deal": "appraise", "actor": "a"},)
    store.touch("g1", decisions=decisions)
    store.flush()

    data = json.loads(path.read_text())
    assert len(data["g1"]["decisions"]) == 1
    assert data["g1"]["decisions"][0]["type"] == "deal"


def test_recover_sessions_roundtrip():
    """Recovery replays decisions to reconstruct game state."""
    from games.auction import auction
    from server.sessions import _sessions, recover_sessions

    registry = {"auction": auction}

    # Simulate persisted active session with decisions
    active_data = {
        "test-recover": {
            "game_type": "auction",
            "players": ["alice", "bob", "charlie"],
            "seed": 42,
            "decisions": [
                {"type": "advance_phase"},
            ],
        }
    }

    # Clear global state
    _sessions.pop("test-recover", None)

    n = recover_sessions(active_data, registry)
    assert n == 1
    assert "test-recover" in _sessions

    sess = _sessions["test-recover"]
    assert sess.state.phase is not None
    assert len(sess.player_ids) == 3

    # Cleanup
    sess._cancel_phase_timer()
    _sessions.pop("test-recover", None)


def test_recover_sessions_starts_phase_timer():
    """Recovered active sessions must start phase timer for timeout."""
    from games.auction import auction
    from server.sessions import _sessions, recover_sessions

    registry = {"auction": auction}
    active_data = {
        "test-timer": {
            "game_type": "auction",
            "players": ["alice", "bob", "charlie"],
            "seed": 42,
            "decisions": [
                {"type": "advance_phase"},
            ],
        }
    }
    _sessions.pop("test-timer", None)

    n = recover_sessions(active_data, registry)
    assert n == 1

    sess = _sessions["test-timer"]
    # Active session should have a phase timer running
    assert sess.state.status != "ended"
    assert sess._timeout_task is not None
    assert not sess._timeout_task.done()

    # Cleanup
    sess._cancel_phase_timer()
    _sessions.pop("test-timer", None)


def test_recover_ended_session_no_timer():
    """Recovered ended sessions should NOT start phase timer."""
    import attrs

    from games.auction import auction
    from server.sessions import GameSession, _sessions

    # Manually create a session and end it, then verify timer is None
    session = GameSession("test-ended", auction, ["a", "b", "c"])
    state = attrs.evolve(session._state, status="ended")
    session._state = state
    session._start_phase_timer()  # should be a no-op for ended games
    assert session._timeout_task is None

    # Cleanup
    _sessions.pop("test-ended", None)


def test_recover_unknown_game_type():
    """Recovery skips sessions with unknown game types."""
    from server.sessions import recover_sessions

    active_data = {
        "bad-game": {
            "game_type": "nonexistent",
            "players": ["a", "b"],
            "seed": 1,
            "decisions": [],
        }
    }
    n = recover_sessions(active_data, {})
    assert n == 0


@pytest.mark.asyncio
async def test_remove_session_cancels_timer():
    """Removing a session should cancel its phase timeout task."""
    from games.auction import auction
    from server.sessions import _sessions, create_session, remove_session

    sid = "test-remove-timer"
    _sessions.pop(sid, None)

    session = create_session(sid, auction, ["alice", "bob", "charlie"])
    await session.start()  # run setup → advance to first interactive phase

    # After start, an interactive phase should have a timer
    task = session._timeout_task
    assert task is not None, f"Expected timer for phase={session.state.phase}"
    assert not task.done()

    # Remove should cancel the timer
    remove_session(sid)
    # Task may still be in "cancelling" state until event loop runs
    import asyncio

    await asyncio.sleep(0)  # let CancelledError propagate
    assert task.cancelled() or task.done()
    assert sid not in _sessions


def test_winner_crediting():
    """Winner gets credited, losers get participation credit."""
    from engine.ledger import MemoryLedger
    from games.auction import auction
    from server.sessions import (
        PARTICIPATION_CREDIT,
        WINNER_CREDIT,
        GameSession,
        set_ledger,
    )

    ledger = MemoryLedger()
    set_ledger(ledger)

    session = GameSession("credit-test", auction, ["alice", "bob", "charlie"])

    # Simulate a finished game with victory
    import attrs

    state = session._state
    state = attrs.evolve(
        state,
        status="ended",
        victory_result={"condition": "test", "winner": "alice", "type": "single"},
    )
    session._state = state
    session._maybe_archive()

    assert ledger.balance("alice") == WINNER_CREDIT
    assert ledger.balance("bob") == PARTICIPATION_CREDIT
    assert ledger.balance("charlie") == PARTICIPATION_CREDIT

    # Cleanup
    set_ledger(None)


def test_draw_crediting():
    """All players get draw credit when game ends without winner."""
    from engine.ledger import MemoryLedger
    from games.auction import auction
    from server.sessions import DRAW_CREDIT, GameSession, set_ledger

    ledger = MemoryLedger()
    set_ledger(ledger)

    session = GameSession("draw-test", auction, ["alice", "bob", "charlie"])

    import attrs

    state = session._state
    state = attrs.evolve(
        state,
        status="ended",
        victory_result={"condition": "test"},  # No winner
    )
    session._state = state
    session._maybe_archive()

    assert ledger.balance("alice") == DRAW_CREDIT
    assert ledger.balance("bob") == DRAW_CREDIT
    assert ledger.balance("charlie") == DRAW_CREDIT

    # Cleanup
    set_ledger(None)
