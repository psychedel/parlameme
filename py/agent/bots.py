"""Simple bot players — random valid actions for non-agent players.

When testing a single LLM agent, the other players need to take actions
or the game stalls on phases requiring all players to act (e.g., format
votes, responses). BotRunner subscribes to a game session and makes
random valid moves for its assigned player IDs.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from engine.runtime.state import CompiledGame
    from server.sessions import GameSession

log = logging.getLogger(__name__)


class BotRunner:
    """Random-action bot for non-agent players in a game session.

    Subscribes to the session and, on every state change, checks if any
    of its assigned players have pending actions. If so, picks a random
    valid response and executes it via the session's async API.
    """

    def __init__(
        self,
        session: GameSession,
        compiled: CompiledGame,
        bot_player_ids: list[str],
        seed: int = 42,
    ):
        self.session = session
        self.compiled = compiled
        self.bot_player_ids = bot_player_ids
        self._rng = random.Random(seed)
        self._task: asyncio.Task | None = None
        self._stopped = False

    def start(self) -> None:
        """Start the bot runner as a background task."""
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        """Stop the bot runner."""
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run(self) -> None:
        """Main loop: periodically check for bot actions."""
        while not self._stopped:
            try:
                if self.session.state.status != "active":
                    break
                await self._act_for_bots()
                # Brief pause to let the game state settle
                await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                break
            except Exception:
                log.debug("Bot runner error", exc_info=True)
                await asyncio.sleep(0.5)

    async def _act_for_bots(self) -> None:
        """Check all bot players and take actions for any with pending work."""
        state = self.session.state
        if state.status != "active":
            return

        for player_id in self.bot_player_ids:
            entity = state.entities.get(player_id)
            if not entity or not entity.active:
                continue

            # 1. Respond to pending deals aimed at this player
            for instance_id, pd in list(state.pending_deals.items()):
                if player_id in pd.responders and pd.responders[player_id] is None:
                    await self._random_respond(player_id, instance_id, pd)

            # 2. Cast pending votes
            for instance_id, pv in list(state.pending_votes.items()):
                if player_id in pv.eligible and player_id not in pv.votes:
                    await self._random_vote(player_id, instance_id, pv)

            # Re-read state after responses/votes may have changed it
            state = self.session.state

    async def _random_respond(
        self, player_id: str, instance_id: str, pending_deal: Any
    ) -> None:
        """Respond randomly to a pending deal."""
        try:
            deal_def = self.compiled.deals.get(pending_deal.deal_id)
            if not deal_def or not deal_def.outcomes:
                return
            outcome_ids = list(deal_def.outcomes.keys())
            chosen = self._rng.choice(outcome_ids)
            result = await self.session.respond_deal(instance_id, player_id, chosen)
            if result.get("ok"):
                log.debug("Bot %s responded %s to %s", player_id, chosen, instance_id)
        except Exception:
            log.debug("Bot %s respond failed", player_id, exc_info=True)

    async def _random_vote(
        self, player_id: str, instance_id: str, pending_vote: Any
    ) -> None:
        """Cast a random vote."""
        try:
            vote_def = self.compiled.votes.get(pending_vote.vote_type)
            if not vote_def:
                return
            options = list(vote_def.options.keys())
            if not options:
                return
            chosen = self._rng.choice(options)
            result = await self.session.cast_vote(instance_id, player_id, chosen)
            if result.get("ok"):
                log.debug("Bot %s voted %s on %s", player_id, chosen, instance_id)
        except Exception:
            log.debug("Bot %s vote failed", player_id, exc_info=True)
