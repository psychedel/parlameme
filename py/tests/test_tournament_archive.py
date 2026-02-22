"""Tests for tournament archive and chronicle system."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tournament.archive import (
    TournamentArchive,
    archive_to_dict,
    create_tournament_archive,
    dict_to_archive,
    generate_tournament_chronicle,
    load_tournament_archive,
    save_tournament_archive,
    save_tournament_chronicle,
)
from tournament.state import Match, Standing, TournamentState

# =========================================================================
# Helpers
# =========================================================================


def _make_completed_tournament() -> TournamentState:
    """Create a completed tournament state for testing."""
    matches = {
        "rr-0": Match(
            id="rr-0",
            participants=("alice", "bob"),
            round=1,
            status="completed",
            winner="alice",
            scores={"alice": 3, "bob": 1},
            session_id="t-cup-rr-0",
        ),
        "rr-1": Match(
            id="rr-1",
            participants=("alice", "charlie"),
            round=1,
            status="completed",
            winner="charlie",
            scores={"alice": 1, "charlie": 2},
            session_id="t-cup-rr-1",
        ),
        "rr-2": Match(
            id="rr-2",
            participants=("bob", "charlie"),
            round=1,
            status="completed",
            winner="bob",
            scores={"bob": 2, "charlie": 0},
            session_id="t-cup-rr-2",
        ),
    }
    standings = {
        "alice": Standing(participant="alice", points=3, wins=1, losses=1),
        "bob": Standing(participant="bob", points=3, wins=1, losses=1),
        "charlie": Standing(participant="charlie", points=3, wins=1, losses=1),
    }
    return TournamentState(
        tournament_id="test-cup",
        tournament_type="round_robin",
        status="completed",
        host="host1",
        name="Test Cup",
        game_type="auction",
        participants=("alice", "bob", "charlie"),
        matches=matches,
        standings=standings,
        winner="alice",
        seed=42,
    )


# =========================================================================
# Archive creation
# =========================================================================


class TestTournamentArchiveCreation:
    def test_create_from_state(self):
        state = _make_completed_tournament()
        archive = create_tournament_archive(state)

        assert archive.tournament_id == "test-cup"
        assert archive.tournament_type == "round_robin"
        assert archive.game_type == "auction"
        assert archive.host == "host1"
        assert archive.name == "Test Cup"
        assert archive.winner == "alice"
        assert archive.seed == 42
        assert archive.participants == ("alice", "bob", "charlie")

    def test_matches_captured(self):
        state = _make_completed_tournament()
        archive = create_tournament_archive(state)

        assert len(archive.matches) == 3
        match_ids = {m["id"] for m in archive.matches}
        assert match_ids == {"rr-0", "rr-1", "rr-2"}

    def test_match_details(self):
        state = _make_completed_tournament()
        archive = create_tournament_archive(state)

        m0 = next(m for m in archive.matches if m["id"] == "rr-0")
        assert m0["winner"] == "alice"
        assert m0["participants"] == ["alice", "bob"]
        assert m0["session_id"] == "t-cup-rr-0"

    def test_standings_captured(self):
        state = _make_completed_tournament()
        archive = create_tournament_archive(state)

        assert "alice" in archive.standings
        assert archive.standings["alice"]["wins"] == 1
        assert archive.standings["alice"]["points"] == 3

    def test_match_archives_list(self):
        state = _make_completed_tournament()
        archive = create_tournament_archive(state)

        assert len(archive.match_archives) == 3
        assert "t-cup-rr-0" in archive.match_archives

    def test_matches_sorted_by_round_and_id(self):
        state = _make_completed_tournament()
        archive = create_tournament_archive(state)

        ids = [m["id"] for m in archive.matches]
        assert ids == sorted(ids)


# =========================================================================
# Chronicle generation
# =========================================================================


class TestTournamentChronicle:
    def test_has_header_and_end(self):
        state = _make_completed_tournament()
        chronicle = generate_tournament_chronicle(state)

        assert chronicle[0]["event"] == "header"
        assert chronicle[-1]["event"] == "end"

    def test_header_fields(self):
        state = _make_completed_tournament()
        chronicle = generate_tournament_chronicle(state)
        header = chronicle[0]

        assert header["tournament_id"] == "test-cup"
        assert header["type"] == "round_robin"
        assert header["game_type"] == "auction"
        assert header["participant_count"] == 3
        assert set(header["participants"]) == {"alice", "bob", "charlie"}

    def test_match_events(self):
        state = _make_completed_tournament()
        chronicle = generate_tournament_chronicle(state)

        matches = [e for e in chronicle if e["event"] == "match"]
        assert len(matches) == 3

        m0 = matches[0]
        assert "match_id" in m0
        assert "winner" in m0
        assert "participants" in m0
        assert "session_id" in m0

    def test_end_has_winner_and_standings(self):
        state = _make_completed_tournament()
        chronicle = generate_tournament_chronicle(state)
        end = chronicle[-1]

        assert end["winner"] == "alice"
        assert "standings" in end
        assert len(end["standings"]) == 3
        # Standings should have required fields
        for s in end["standings"]:
            assert "participant" in s
            assert "points" in s
            assert "wins" in s

    def test_standings_sorted_by_points(self):
        state = _make_completed_tournament()
        chronicle = generate_tournament_chronicle(state)
        end = chronicle[-1]

        points = [s["points"] for s in end["standings"]]
        assert points == sorted(points, reverse=True)


# =========================================================================
# Serialization
# =========================================================================


class TestTournamentArchiveSerialization:
    def test_dict_roundtrip(self):
        state = _make_completed_tournament()
        archive = create_tournament_archive(state)

        d = archive_to_dict(archive)
        restored = dict_to_archive(d)

        assert restored.tournament_id == archive.tournament_id
        assert restored.winner == archive.winner
        assert restored.participants == archive.participants
        assert len(restored.matches) == len(archive.matches)

    def test_json_serializable(self):
        state = _make_completed_tournament()
        archive = create_tournament_archive(state)
        d = archive_to_dict(archive)

        json_str = json.dumps(d)
        restored = json.loads(json_str)
        assert restored["tournament_id"] == "test-cup"

    def test_save_and_load(self):
        state = _make_completed_tournament()
        archive = create_tournament_archive(state)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tournament.json"
            save_tournament_archive(archive, path)
            loaded = load_tournament_archive(path)

            assert loaded.tournament_id == archive.tournament_id
            assert loaded.winner == archive.winner
            assert len(loaded.matches) == len(archive.matches)

    def test_save_default_path(self):
        state = _make_completed_tournament()
        archive = create_tournament_archive(state)

        with tempfile.TemporaryDirectory() as tmpdir:
            import tournament.archive as ta_mod

            orig_dir = ta_mod.TOURNAMENT_ARCHIVE_DIR
            ta_mod.TOURNAMENT_ARCHIVE_DIR = Path(tmpdir)
            try:
                path = save_tournament_archive(archive)
                assert path.exists()
                assert path.name == "test-cup.json"
            finally:
                ta_mod.TOURNAMENT_ARCHIVE_DIR = orig_dir

    def test_chronicle_save_as_jsonl(self):
        state = _make_completed_tournament()
        chronicle = generate_tournament_chronicle(state)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "chronicle.jsonl"
            save_tournament_chronicle(chronicle, path)

            # Verify JSONL format
            with open(path) as f:
                lines = [line.strip() for line in f if line.strip()]
            assert len(lines) == len(chronicle)
            for line in lines:
                parsed = json.loads(line)
                assert "event" in parsed
