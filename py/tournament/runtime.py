"""Tournament runtime — stateless, operates on TournamentState."""

from __future__ import annotations

import attrs

from engine.errors import E, Result, fail, ok

from .config import TournamentConfig
from .generator import MatchGenerator
from .state import Match, Standing, TournamentState


class TournamentRuntime:
    """Manages tournament lifecycle. Stateless — all methods are pure."""

    def create(
        self,
        tournament_id: str,
        tournament_type: str,
        host: str,
        game_type: str,
        *,
        name: str = "",
        min_participants: int = 2,
        max_participants: int = 16,
        match_size: int = 2,
        rounds: int | None = None,
        seed: int = 42,
        config: TournamentConfig | None = None,
    ) -> TournamentState:
        return TournamentState(
            tournament_id=tournament_id,
            tournament_type=tournament_type,
            host=host,
            game_type=game_type,
            name=name or f"{tournament_type} tournament",
            min_participants=min_participants,
            max_participants=max_participants,
            match_size=match_size,
            rounds=rounds,
            seed=seed,
            config=config or TournamentConfig(),
        )

    def cancel(self, state: TournamentState, requester: str) -> Result:
        if state.status in ("completed", "cancelled"):
            return fail(E.TOURNAMENT_CANCELLED)
        if requester != state.host:
            return fail(E.NOT_HOST)
        return ok(attrs.evolve(state, status="cancelled"))

    def register(self, state: TournamentState, participant: str) -> Result:
        if state.status != "registration":
            return fail(E.REGISTRATION_CLOSED)
        if len(state.participants) >= state.max_participants:
            return fail(E.TOURNAMENT_FULL)
        if participant in state.participants:
            return fail(E.ALREADY_REGISTERED, participant)

        new_state = attrs.evolve(
            state,
            participants=(*state.participants, participant),
            standings={
                **state.standings,
                participant: Standing(participant=participant),
            },
        )
        return ok(new_state)

    def unregister(self, state: TournamentState, participant: str) -> Result:
        if state.status != "registration":
            return fail(E.REGISTRATION_CLOSED)
        if participant not in state.participants:
            return fail(E.NOT_REGISTERED, participant)

        new_participants = tuple(p for p in state.participants if p != participant)
        new_standings = {k: v for k, v in state.standings.items() if k != participant}
        return ok(
            attrs.evolve(state, participants=new_participants, standings=new_standings)
        )

    def start(self, state: TournamentState) -> Result:
        if state.status != "registration":
            return fail(E.TOURNAMENT_STARTED)
        if len(state.participants) < state.min_participants:
            return fail(
                E.NOT_ENOUGH_PARTICIPANTS,
                state.min_participants,
                len(state.participants),
            )

        matches = self._generate_initial_matches(state)

        state = attrs.evolve(
            state,
            status="in_progress",
            matches={m.id: m for m in matches},
        )
        state = self._auto_complete_byes(state)
        return ok(state)

    def report_result(
        self,
        state: TournamentState,
        match_id: str,
        winner: str,
        scores: dict[str, int] | None = None,
    ) -> Result:
        if state.status == "cancelled":
            return fail(E.TOURNAMENT_CANCELLED)
        match = state.matches.get(match_id)
        if not match:
            return fail(E.MATCH_NOT_FOUND, match_id)
        if match.status == "completed":
            return fail(E.MATCH_COMPLETED, match_id)
        if winner not in match.participants:
            return fail(E.WINNER_NOT_IN_MATCH, winner, match_id)

        # Update match
        updated_match = attrs.evolve(
            match,
            status="completed",
            winner=winner,
            scores=scores or {},
        )
        new_matches = {**state.matches, match_id: updated_match}

        # Update standings (supports 2+ player matches)
        new_standings = dict(state.standings)
        losers = [p for p in match.participants if p != winner]

        w_score = (scores or {}).get(winner, 1)
        ws = new_standings[winner]
        total_loser_score = sum((scores or {}).get(l, 0) for l in losers)
        diff = w_score - (total_loser_score // max(len(losers), 1))

        new_standings[winner] = attrs.evolve(
            ws,
            points=ws.points + state.config.win_points,
            wins=ws.wins + 1,
            goal_diff=ws.goal_diff + diff,
        )
        for loser in losers:
            l_score = (scores or {}).get(loser, 0)
            ls = new_standings[loser]
            new_standings[loser] = attrs.evolve(
                ls,
                losses=ls.losses + 1,
                goal_diff=ls.goal_diff - (w_score - l_score),
            )

        state = attrs.evolve(state, matches=new_matches, standings=new_standings)
        state = self._update_buchholz(state)

        # Progress: generate next matches if needed
        state = self._progress(state)

        # Check completion
        state = self._check_completion(state)

        return ok(state)

    def report_draw(
        self,
        state: TournamentState,
        match_id: str,
        scores: dict[str, int] | None = None,
    ) -> Result:
        """Report a draw (no winner). Each participant gets draw_points."""
        if state.status == "cancelled":
            return fail(E.TOURNAMENT_CANCELLED)
        match = state.matches.get(match_id)
        if not match:
            return fail(E.MATCH_NOT_FOUND, match_id)
        if match.status == "completed":
            return fail(E.MATCH_COMPLETED, match_id)

        updated_match = attrs.evolve(
            match,
            status="completed",
            winner=None,
            scores=scores or {},
        )
        new_matches = {**state.matches, match_id: updated_match}

        # Each participant gets 1 point (draw)
        new_standings = dict(state.standings)
        for p in match.participants:
            s = new_standings[p]
            new_standings[p] = attrs.evolve(
                s,
                draws=s.draws + 1,
                points=s.points + state.config.draw_points,
            )

        state = attrs.evolve(state, matches=new_matches, standings=new_standings)
        state = self._update_buchholz(state)
        state = self._progress(state)
        state = self._check_completion(state)

        return ok(state)

    def get_pending_matches(self, state: TournamentState) -> list[Match]:
        return [m for m in state.matches.values() if m.status == "pending"]

    def get_active_matches(self, state: TournamentState) -> list[Match]:
        return [m for m in state.matches.values() if m.status == "active"]

    def get_standings_sorted(self, state: TournamentState) -> list[Standing]:
        fields = state.config.tiebreaker
        return sorted(
            state.standings.values(),
            key=lambda s: tuple(-getattr(s, f) for f in fields),
        )

    # ------------------------------------------------------------------
    # Internal: Buchholz (strength of schedule)
    # ------------------------------------------------------------------

    def _update_buchholz(self, state: TournamentState) -> TournamentState:
        """Recompute Buchholz score for all participants.

        Buchholz = sum of opponents' points across completed matches.
        """
        # Build opponent map from completed matches
        opponents: dict[str, list[str]] = {p: [] for p in state.participants}
        for m in state.matches.values():
            if m.status != "completed":
                continue
            for p in m.participants:
                for other in m.participants:
                    if other != p:
                        opponents.setdefault(p, []).append(other)

        new_standings = dict(state.standings)
        for pid, opps in opponents.items():
            if pid not in new_standings:
                continue
            buch = sum(new_standings[o].points for o in opps if o in new_standings)
            s = new_standings[pid]
            if s.buchholz != buch:
                new_standings[pid] = attrs.evolve(s, buchholz=float(buch))

        return attrs.evolve(state, standings=new_standings)

    # ------------------------------------------------------------------
    # Internal: bye handling
    # ------------------------------------------------------------------

    def _auto_complete_byes(self, state: TournamentState) -> TournamentState:
        """Auto-complete single-participant bye matches with win points."""
        new_matches = dict(state.matches)
        new_standings = dict(state.standings)
        changed = False
        for mid, m in state.matches.items():
            if len(m.participants) == 1 and m.status == "pending":
                bye_player = m.participants[0]
                new_matches[mid] = attrs.evolve(
                    m, status="completed", winner=bye_player
                )
                s = new_standings.get(bye_player)
                if s:
                    new_standings[bye_player] = attrs.evolve(
                        s,
                        points=s.points + state.config.win_points,
                        wins=s.wins + 1,
                    )
                changed = True
        if not changed:
            return state
        return attrs.evolve(state, matches=new_matches, standings=new_standings)

    # ------------------------------------------------------------------
    # Internal: match generation
    # ------------------------------------------------------------------

    def _generate_initial_matches(self, state: TournamentState) -> list[Match]:
        participants = list(state.participants)
        match state.tournament_type:
            case "round_robin":
                return MatchGenerator.round_robin(
                    participants, state.seed, match_size=state.match_size
                )
            case "single_elimination":
                return MatchGenerator.single_elimination(
                    participants, state.seed, match_size=state.match_size
                )
            case "swiss":
                return MatchGenerator.swiss_pairing(
                    state.standings, set(), 1, match_size=state.match_size
                )
            case _:
                return []

    # ------------------------------------------------------------------
    # Internal: progression
    # ------------------------------------------------------------------

    def _progress(self, state: TournamentState) -> TournamentState:
        match state.tournament_type:
            case "single_elimination":
                return self._progress_elimination(state)
            case "swiss":
                return self._progress_swiss(state)
            case _:
                return state  # round_robin has all matches from start

    def _progress_elimination(self, state: TournamentState) -> TournamentState:
        """Advance bracket when a round completes.

        Collects winners from the latest round plus any bye players who
        haven't yet been placed in a match, then groups them into next-round pods.
        """
        ms = state.match_size
        by_round: dict[int, list[Match]] = {}
        for m in state.matches.values():
            by_round.setdefault(m.round, []).append(m)

        if not by_round:
            return state

        max_round = max(by_round.keys())
        current_round = by_round[max_round]

        # Check if current round is complete
        if not all(m.status == "completed" for m in current_round):
            return state

        # Winners from the just-completed round
        winners: list[str] = [m.winner for m in current_round if m.winner]

        # Players who have appeared in any match so far
        played: set[str] = set()
        for m in state.matches.values():
            played.update(m.participants)

        # Add bye players who haven't been placed in any match yet
        bye_advances = MatchGenerator.get_bye_advances(
            list(state.participants), state.seed, match_size=ms
        )
        for p in bye_advances:
            if p not in played:
                winners.append(p)

        if len(winners) < 2:
            return state  # Final already played or only 1 left

        # Generate next round from winners pool
        new_matches: list[Match] = []
        for i in range(0, len(winners), ms):
            pod = tuple(winners[i : i + ms])
            if len(pod) >= 2:
                new_matches.append(
                    Match(
                        id=f"se-r{max_round + 1}-{i // ms}",
                        participants=pod,
                        round=max_round + 1,
                    )
                )

        return attrs.evolve(
            state,
            matches={
                **state.matches,
                **{m.id: m for m in new_matches},
            },
        )

    def _swiss_max_rounds(self, state: TournamentState) -> int:
        """Resolve swiss max rounds: explicit > config > formula."""
        if state.rounds is not None:
            return state.rounds
        if state.config.swiss_max_rounds is not None:
            return state.config.swiss_max_rounds
        return max(3, int(len(state.participants) ** 0.5 * 2))

    def _progress_swiss(self, state: TournamentState) -> TournamentState:
        """Generate next swiss round when current completes."""
        max_rounds = self._swiss_max_rounds(state)

        # Find highest completed round
        completed_rounds: set[int] = set()
        for m in state.matches.values():
            if m.status == "completed":
                completed_rounds.add(m.round)

        if not completed_rounds:
            return state

        current_round = max(completed_rounds)

        # Check if all matches in current round are done
        current_matches = [
            m for m in state.matches.values() if m.round == current_round
        ]
        if not all(m.status == "completed" for m in current_matches):
            return state

        if current_round >= max_rounds:
            return state

        # Build match history (frozenset for any match_size)
        history: set[frozenset[str]] = set()
        for m in state.matches.values():
            if len(m.participants) >= 2:
                history.add(frozenset(m.participants))

        new_matches = MatchGenerator.swiss_pairing(
            state.standings,
            history,
            current_round + 1,
            match_size=state.match_size,
        )

        state = attrs.evolve(
            state,
            matches={
                **state.matches,
                **{m.id: m for m in new_matches},
            },
        )
        return self._auto_complete_byes(state)

    # ------------------------------------------------------------------
    # Internal: completion check
    # ------------------------------------------------------------------

    def is_completed(self, state: TournamentState) -> str | None:
        """Pure query: return winner if tournament is finished, else None.

        For SE, winner is the final match winner.
        For RR/Swiss, winner is the top-ranked participant by standings.
        Returns None if tournament is not yet complete.
        """
        if state.status != "in_progress" or not state.matches:
            return None

        match state.tournament_type:
            case "round_robin":
                if all(m.status == "completed" for m in state.matches.values()):
                    return self._determine_winner(state)
            case "single_elimination":
                max_round = max(m.round for m in state.matches.values())
                final_matches = [
                    m for m in state.matches.values() if m.round == max_round
                ]
                if len(final_matches) == 1 and final_matches[0].status == "completed":
                    return final_matches[0].winner
            case "swiss":
                max_rounds = self._swiss_max_rounds(state)
                completed_rounds = {
                    m.round for m in state.matches.values() if m.status == "completed"
                }
                if len(completed_rounds) >= max_rounds:
                    last_round = [
                        m for m in state.matches.values() if m.round == max_rounds
                    ]
                    if all(m.status == "completed" for m in last_round):
                        return self._determine_winner(state)
        return None

    def _check_completion(self, state: TournamentState) -> TournamentState:
        """Check if tournament is finished and mark completed."""
        winner = self.is_completed(state)
        if winner is not None:
            return attrs.evolve(state, status="completed", winner=winner)
        return state

    def _determine_winner(self, state: TournamentState) -> str | None:
        standings = self.get_standings_sorted(state)
        return standings[0].participant if standings else None
