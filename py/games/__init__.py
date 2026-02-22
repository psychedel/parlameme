"""Game registry — all compiled games available for play."""

from engine.runtime.state import CompiledGame

from .auction import auction
from .exchange import exchange
from .parliament_arena import parliament_arena
from .werewolf import werewolf

REGISTRY: dict[str, CompiledGame] = {
    "auction": auction,
    "exchange": exchange,
    "werewolf": werewolf,
    "parliament_arena": parliament_arena,
}

__all__ = [
    "REGISTRY",
    "auction",
    "exchange",
    "werewolf",
    "parliament_arena",
]
