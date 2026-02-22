"""Tests for hash-chain ledger."""

import json

from engine.ledger import FileLedger, LedgerEntry, MemoryLedger


class TestMemoryLedger:
    def test_deposit_and_balance(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 1000)
        assert ledger.balance("alice") == 1000

    def test_withdraw_reduces_balance(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 1000)
        ledger.append("withdraw", "alice", 300)
        assert ledger.balance("alice") == 700

    def test_game_credit_and_debit(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 500)
        ledger.append("game_debit", "alice", 100, ref="game-1")
        ledger.append("game_credit", "alice", 200, ref="game-1")
        assert ledger.balance("alice") == 600  # 500 - 100 + 200

    def test_multiple_players(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 1000)
        ledger.append("deposit", "bob", 500)
        ledger.append("game_debit", "alice", 100, ref="game-1")
        ledger.append("game_credit", "bob", 100, ref="game-1")

        assert ledger.balance("alice") == 900
        assert ledger.balance("bob") == 600

    def test_all_balances(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 1000)
        ledger.append("deposit", "bob", 500)

        balances = ledger.all_balances()
        assert balances == {"alice": 1000, "bob": 500}

    def test_unknown_player_zero_balance(self):
        ledger = MemoryLedger()
        assert ledger.balance("nobody") == 0

    def test_entries_all(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 100)
        ledger.append("deposit", "bob", 200)
        assert len(ledger.entries()) == 2

    def test_entries_by_player(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 100)
        ledger.append("deposit", "bob", 200)
        ledger.append("withdraw", "alice", 50)

        alice_entries = ledger.entries("alice")
        assert len(alice_entries) == 2
        assert all(e.player == "alice" for e in alice_entries)

    def test_hash_chain_integrity(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 1000)
        ledger.append("deposit", "bob", 500)
        ledger.append("game_debit", "alice", 100)
        assert ledger.verify()

    def test_chain_detects_tampering(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 1000)
        ledger.append("deposit", "bob", 500)

        # Tamper with first entry
        tampered = LedgerEntry(
            seq=ledger._entries[0].seq,
            type="deposit",
            player="alice",
            amount=9999,  # changed!
            ref=ledger._entries[0].ref,
            timestamp=ledger._entries[0].timestamp,
            prev_hash=ledger._entries[0].prev_hash,
            content_hash=ledger._entries[0].content_hash,  # hash won't match
        )
        ledger._entries[0] = tampered
        assert not ledger.verify()

    def test_sequential_hashing(self):
        """Each entry's prev_hash links to previous entry's content_hash."""
        ledger = MemoryLedger()
        e1 = ledger.append("deposit", "alice", 100)
        e2 = ledger.append("deposit", "bob", 200)
        e3 = ledger.append("withdraw", "alice", 50)

        assert e1.prev_hash == "genesis"
        assert e2.prev_hash == e1.content_hash
        assert e3.prev_hash == e2.content_hash

    def test_len(self):
        ledger = MemoryLedger()
        assert len(ledger) == 0
        ledger.append("deposit", "alice", 100)
        assert len(ledger) == 1

    def test_refund_is_credit(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 100)
        ledger.append("withdraw", "alice", 50)
        ledger.append("refund", "alice", 30)
        assert ledger.balance("alice") == 80  # 100 - 50 + 30

    def test_bonus_is_credit(self):
        ledger = MemoryLedger()
        ledger.append("bonus", "alice", 50)
        assert ledger.balance("alice") == 50


class TestFileLedger:
    def test_persist_and_reload(self, tmp_path):
        path = tmp_path / "ledger.json"

        # Create and populate
        ledger = FileLedger(path=path)
        ledger.append("deposit", "alice", 1000)
        ledger.append("game_debit", "alice", 200, ref="game-1")
        ledger.append("game_credit", "alice", 300, ref="game-1")

        # Reload from disk
        ledger2 = FileLedger(path=path)
        assert ledger2.balance("alice") == 1100  # 1000 - 200 + 300
        assert len(ledger2) == 3
        assert ledger2.verify()

    def test_chain_survives_reload(self, tmp_path):
        path = tmp_path / "ledger.json"

        ledger = FileLedger(path=path)
        ledger.append("deposit", "alice", 500)
        ledger.append("deposit", "bob", 300)

        # Reload and append more
        ledger2 = FileLedger(path=path)
        ledger2.append("game_debit", "alice", 100, ref="game-1")
        ledger2.append("game_credit", "bob", 100, ref="game-1")

        # Verify full chain
        assert ledger2.verify()
        assert len(ledger2) == 4
        assert ledger2.balance("alice") == 400
        assert ledger2.balance("bob") == 400

    def test_empty_file(self, tmp_path):
        path = tmp_path / "ledger.json"
        ledger = FileLedger(path=path)
        assert len(ledger) == 0
        assert ledger.verify()

    def test_detect_file_tampering(self, tmp_path):
        path = tmp_path / "ledger.json"

        ledger = FileLedger(path=path)
        ledger.append("deposit", "alice", 1000)
        ledger.append("deposit", "bob", 500)

        # Tamper with the file
        data = json.loads(path.read_text())
        data["entries"][0]["amount"] = 9999
        path.write_text(json.dumps(data))

        # Reload should detect corruption
        ledger2 = FileLedger(path=path)
        assert not ledger2.verify()
