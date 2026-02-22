"""Parliament Arena: The Last Assembly — post-apocalyptic parliament simulation.

FLAGSHIP GAME — showcases ~88% of engine capabilities.

Game theory foundations:
- Distribution victory (ALL players scored, no single winner)
- Repeated game dynamics (reputation carries across rounds)
- Coalition formation with defection incentives
- Cheap talk vs costly signaling (promises vs handshakes vs blood oaths)
- Radiation as escalating threat (tragedy of the commons)
- Dead hand commitment (nuclear deterrence analogy)
- Information warfare (investigation, reveal, deception)
- Probabilistic mechanics (Maybe effects for espionage uncertainty)
- Conditional outcomes (Cond effects for hidden_type-dependent results)

Communication:
- Assembly: public channel — statements on record
- Faction caucus: group channels for coordination
- Backroom: private channel — costs suspicion per message
- Intelligence wire: broadcast channel for investigation results

Faction system with mechanical bonuses:
- Vault Dwellers: +intel per round (information network)
- Scrap Lords: +caps per round (trade network)
- Green Cult: -radiation per round (environmental healing)
- Iron Guard: +influence per round (military authority)
- Free Radicals: +intel per round (underground contacts)
- Old Timers: +reputation per round (respected elders)

Position system:
- Speaker: sets bill agenda, controls legislative flow
- Prime Minister: forms cabinet, revenue generation
- Opposition Leader: investigation powers, interrogation

Engine features showcased:
- Maybe (probabilistic effects), Cond (conditional branching)
- Reveal + RevealAs (fake), SendMessage, Emit
- Unrelate, DissolveGroup, JoinGroup
- Speech Acts (claim, accuse, promise, predict, inquire)
- Commitments (dead_hand, blood_oath_vengeance, cabinet_crisis, radiation_emergency)
- Channel effects (backroom suspicion cost)
- Multilateral deals (form_coalition)
- Each, When, Let, SetVar

Ported from: src/cljc/parlameme/v3/games/parliament_arena.cljc
"""

from engine.dsl.builder import Game
from engine.expr import Ref, actor, alive, count_where, game, has_relation, params, subject
from engine.runtime.effects import (
    AssignRoles,
    Boost,
    Broadcast,
    BurnStakes,
    Cond,
    CreateGroup,
    Damage,
    DissolveGroup,
    Each,
    Eliminate,
    Emit,
    JoinGroup,
    LeaveGroup,
    Maybe,
    Notify,
    Relate,
    ReturnStakes,
    Reveal,
    SendMessage,
    SetAttr,
    SetVar,
    Transfer,
    TransferStakes,
    Unrelate,
    When,
)
from engine.runtime.state import ChannelHint, OutcomeDef, PhaseHint, RoleHint, VarHint

parliament_arena = (
    Game("parliament_arena", "Parliament Arena: The Last Assembly", players=(6, 24))
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RESOURCES (8)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    .resource("caps", initial=100, visibility="private", bounds=(0, None))
    .resource("rations", initial=20, visibility="private", bounds=(0, 100))
    .resource("influence", initial=10, visibility="public", bounds=(0, 100))
    .resource("reputation", initial=50, visibility="public", bounds=(0, 100))
    .resource("intel", initial=0, visibility="private", bounds=(0, 50))
    .resource("radiation", initial=0, visibility="public", bounds=(0, 100))
    .resource("achievements", initial=0, visibility="public", bounds=(0, None))
    .resource("suspicion", initial=0, visibility="public", bounds=(0, 100))
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ATTRIBUTES (4) — all mechanically active
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    .attr(
        "faction",
        visibility="public",
        values=(
            "vault_dwellers",
            "scrap_lords",
            "green_cult",
            "iron_guard",
            "free_radicals",
            "old_timers",
        ),
    )
    .attr(
        "hidden_type",
        visibility="private",
        values=("loyalist", "opportunist", "ideologue", "chaotic"),
        distribute=True,
    )
    .attr(
        "role",
        visibility="public",
        values=("leader", "minister", "whip", "backbencher"),
        initial="backbencher",
    )
    .attr(
        "position",
        visibility="public",
        values=("speaker", "prime_minister", "opposition_leader", "none"),
        initial="none",
    )
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # GROUPS (3)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    .group("faction_group", visible=True, exclusive=True, knows_members=True)
    .group("coalition", visible=True, exclusive=False, knows_members=True)
    .group("cabinet", visible=True, exclusive=True, knows_members=True, max_size=5)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CHANNELS (4) — all mechanically active
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    .channel(
        "assembly",
        type="public",
        description="Parliament assembly floor — all statements on record",
    )
    .channel(
        "faction_caucus",
        type="group",
        group="faction_group",
        description="Private faction coordination",
    )
    .channel(
        "backroom",
        type="private",
        description="Private dealing — every message costs suspicion",
        effects=[Boost("actor", "suspicion", 3)],
    )
    .channel(
        "intelligence_wire",
        type="broadcast",
        description="Intelligence reports — investigation results posted here",
    )
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DEALS (12): 7 bilateral/multilateral + 5 unilateral
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- Promise: non-binding cheap talk ---
    .deal(
        "promise",
        proposer=alive(),
        responder=alive(),
        responses=["acknowledge", "dismiss"],
        outcomes={
            "acknowledge": OutcomeDef(
                effects=(
                    Boost("proposer", "reputation", 2),
                    Broadcast(
                        "{proposer} makes promise to {responder} — acknowledged."
                    ),
                ),
                doc="Promise acknowledged — slight reputation boost",
            ),
            "dismiss": OutcomeDef(
                effects=(Broadcast("{responder} dismisses {proposer}'s promise."),),
                doc="Promise dismissed — cheap talk revealed",
            ),
        },
        doc="Make a non-binding promise. Cheap talk — no enforcement mechanism.",
    )
    # --- Handshake: reputation-backed agreement ---
    .deal(
        "handshake",
        proposer=alive(),
        responder=alive(),
        stakes={"proposer": [("reputation", 10)], "responder": [("reputation", 10)]},
        responses=["accept", "reject"],
        outcomes={
            "accept": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Boost("proposer", "reputation", 5),
                    Boost("responder", "reputation", 5),
                    Relate("proposer", "responder", "handshake_partner"),
                    Broadcast("{proposer} and {responder} shake on it!"),
                ),
                doc="Handshake accepted — mutual reputation boost, creates relation",
            ),
            "reject": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Broadcast("{responder} refuses {proposer}'s handshake."),
                ),
                doc="Handshake refused",
            ),
        },
        doc="Reputation-backed agreement. Costs reputation to propose/accept.",
    )
    # --- Bribe: caps for votes, Cond on hidden_type ---
    .deal(
        "bribe",
        proposer=alive(),
        responder=alive(),
        params={
            "amount": {
                "type": "number",
                "min": 10,
                "max": 50,
                "label": "Bribe amount",
            }
        },
        stakes={"proposer": [("caps", "amount")]},
        responses=["accept", "reject", "expose"],
        outcomes={
            "accept": OutcomeDef(
                effects=(
                    TransferStakes("responder"),
                    Relate("proposer", "responder", "bribed"),
                    # Opportunists get extra caps from being bribed
                    Cond(
                        branches=(
                            (
                                Ref("responder", "hidden_type") == "opportunist",
                                (Boost("responder", "caps", 10),),
                            ),
                        )
                    ),
                    Notify("proposer", "{responder} accepts your offer."),
                    Emit("bribe_accepted", {}),
                ),
                doc="Bribe accepted — caps transferred, opportunists get bonus",
            ),
            "reject": OutcomeDef(
                effects=(ReturnStakes(),),
                doc="Bribe rejected — caps returned",
            ),
            "expose": OutcomeDef(
                effects=(
                    BurnStakes(),
                    Damage("proposer", "reputation", 20),
                    Boost("responder", "reputation", 10),
                    # Ideologues get extra reputation for exposing corruption
                    Cond(
                        branches=(
                            (
                                Ref("responder", "hidden_type") == "ideologue",
                                (Boost("responder", "reputation", 10),),
                            ),
                        )
                    ),
                    Broadcast("{responder} exposes bribery attempt by {proposer}!"),
                    Emit("bribe_exposed", {}),
                ),
                doc="Bribe exposed — devastating reputation damage, ideologues rewarded",
            ),
        },
        doc="Offer caps for political support. Hidden type affects outcomes.",
    )
    # --- Trade intel ---
    .deal(
        "trade_intel",
        proposer=alive() & (actor.intel > 0),
        responder=alive(),
        stakes={"proposer": [("intel", 1)]},
        responses=["trade", "accept", "reject"],
        outcomes={
            "trade": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Boost("proposer", "intel", 1),
                    Boost("responder", "intel", 1),
                    Notify("proposer", "Intel exchanged with {responder}."),
                    Notify("responder", "Intel exchanged with {proposer}."),
                ),
                doc="Mutual intel exchange",
            ),
            "accept": OutcomeDef(
                effects=(
                    Boost("responder", "intel", 1),
                    Notify("responder", "Intel received from {proposer}."),
                ),
                doc="Accept intel without reciprocating",
            ),
            "reject": OutcomeDef(
                effects=(ReturnStakes(),),
                doc="Reject intel exchange",
            ),
        },
        doc="Exchange intelligence tokens with another player.",
    )
    # --- Blood oath: maximum commitment, feeds commitment ---
    .deal(
        "blood_oath",
        proposer=alive(),
        responder=alive(),
        per_game=1,
        stakes={"proposer": [("reputation", 25)], "responder": [("reputation", 25)]},
        responses=["swear", "refuse"],
        outcomes={
            "swear": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Boost("proposer", "reputation", 10),
                    Boost("responder", "reputation", 10),
                    Boost("proposer", "influence", 5),
                    Boost("responder", "influence", 5),
                    Relate("proposer", "responder", "blood_oath"),
                    Broadcast("{proposer} and {responder} swear a BLOOD OATH!"),
                    Emit("blood_oath_sworn", {}),
                ),
                doc="Blood oath sworn — huge reputation/influence boost, triggers vengeance on death",
            ),
            "refuse": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Damage("proposer", "reputation", 5),
                    Broadcast("{responder} refuses blood oath with {proposer}."),
                ),
                doc="Refusing costs proposer some face",
            ),
        },
        doc="Maximum commitment deal. Huge stakes. Once per game. Triggers vengeance commitment on death.",
    )
    # --- Form coalition: multilateral alliance ---
    .deal(
        "form_coalition",
        proposer=alive(),
        responders={
            "filter": alive(),
            "count": (2, 3),
        },
        stakes={"proposer": [("influence", 5)]},
        responses=["join", "decline"],
        completion_rule="majority",
        outcomes={
            "join": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Boost("proposer", "influence", 5),
                    Broadcast("{proposer} forms a coalition!"),
                    Emit("coalition_formed", {}),
                ),
                doc="Coalition formed — influence bonus for proposer",
            ),
            "decline": OutcomeDef(
                effects=(ReturnStakes(),),
                doc="Coalition rejected by majority",
            ),
        },
        doc="Form political coalition with 2-3 allies. Majority must join.",
    )
    # --- Blackmail: intel for reputation damage, Maybe leak ---
    .deal(
        "blackmail",
        actor=alive() & (actor.intel >= 2),
        target=alive(),
        stakes={"actor": [("intel", 2)]},
        effects=[
            Damage("target", "reputation", 15),
            Boost("actor", "suspicion", 5),
            # 25% chance source identity leaks
            Maybe(
                probability=0.25,
                effects=(
                    Broadcast("{actor} exposed as blackmailer of {target}!"),
                    Damage("actor", "reputation", 10),
                    Emit("blackmail_leaked", {}),
                ),
            ),
            Broadcast("Compromising information surfaces about {target}!"),
            Emit("blackmail", {}),
        ],
        doc="Spend intel to damage target's reputation. 25% chance your identity leaks.",
    )
    # --- Investigate: reveal hidden_type, Maybe fake ---
    # Uses SetVar flag to ensure mutually exclusive outcomes (one RNG roll).
    .deal(
        "investigate",
        actor=alive() & (actor.intel >= 3),
        target=alive(),
        stakes={"actor": [("intel", 3)]},
        effects=[
            Boost("actor", "suspicion", 3),
            SetVar("investigation_hit", 0),
            # 60% chance: real reveal
            Maybe(
                probability=0.6,
                effects=(
                    SetVar("investigation_hit", 1),
                    Reveal("target", "hidden_type", to="actor"),
                    SendMessage(
                        "intelligence_wire",
                        "actor",
                        "Investigation of {target} completed — classified results.",
                    ),
                    Notify(
                        "actor", "Investigation successful: {target}'s type revealed."
                    ),
                    Emit("investigation_real", {}),
                ),
            ),
            # 40% chance: fake reveal (only if real reveal didn't fire)
            When(
                game.investigation_hit == 0,
                (
                    Reveal("target", "hidden_type", to="actor", fake="loyalist"),
                    Notify(
                        "actor",
                        "Investigation complete: {target} appears to be a loyalist.",
                    ),
                    Emit("investigation_fake", {}),
                ),
            ),
            Emit("investigation", {}),
        ],
        doc="Spend 3 intel to investigate target's hidden type. 60% real, 40% fake result.",
    )
    # --- Leak scandal: probabilistic reputation attack ---
    # Uses SetVar flag to ensure mutually exclusive outcomes (one RNG roll).
    .deal(
        "leak_scandal",
        actor=alive() & (actor.intel >= 2),
        target=alive(),
        stakes={"actor": [("intel", 2)]},
        effects=[
            SetVar("scandal_hit", 0),
            # 70% chance: scandal succeeds
            Maybe(
                probability=0.7,
                effects=(
                    SetVar("scandal_hit", 1),
                    Damage("target", "reputation", 20),
                    Boost("target", "suspicion", 5),
                    SendMessage(
                        "intelligence_wire",
                        "actor",
                        "SCANDAL: Damaging information about {target} surfaces!",
                    ),
                    Broadcast("A scandal rocks the Assembly involving {target}!"),
                    Emit("scandal_success", {}),
                ),
            ),
            # 30% chance: backfires (only if success didn't fire)
            When(
                game.scandal_hit == 0,
                (
                    Damage("actor", "reputation", 15),
                    Boost("actor", "suspicion", 8),
                    Broadcast(
                        "{actor}'s attempted smear campaign against {target} backfires!"
                    ),
                    Emit("scandal_backfire", {}),
                ),
            ),
            Emit("leak_scandal", {}),
        ],
        doc="Leak damaging info about target. 70% success, 30% backfires on you.",
    )
    # --- Appoint position: PM power to fill cabinet ---
    .deal(
        "appoint_position",
        actor=alive() & (Ref("actor", "position") == "prime_minister"),
        target=alive() & (Ref("target", "position") == "none"),
        effects=[
            SetAttr("target", "role", "minister"),
            JoinGroup("target", "cabinet"),
            Relate("actor", "target", "appointed"),
            Boost("target", "influence", 3),
            Broadcast("{actor} appoints {target} as Minister!"),
            Emit("appointment", {}),
        ],
        doc="Prime Minister appoints target as Minister and adds to cabinet.",
    )
    # --- Betray coalition: break all alliance relations ---
    .deal(
        "betray_coalition",
        actor=alive() & (actor.reputation >= 10),
        target=alive(),
        guard=has_relation("actor", "handshake_partner")
        | has_relation("actor", "blood_oath"),
        effects=[
            Unrelate("actor", "target", "handshake_partner"),
            Unrelate("actor", "target", "blood_oath"),
            Damage("actor", "reputation", 15),
            Boost("actor", "caps", 20),
            # Loyalists pay double reputation for betrayal
            Cond(
                branches=(
                    (
                        Ref("actor", "hidden_type") == "loyalist",
                        (Damage("actor", "reputation", 15),),
                    ),
                )
            ),
            Broadcast("{actor} betrays their alliance with {target}!"),
            Emit("betrayal", {}),
        ],
        doc="Break alliance relations for personal gain. Loyalists pay double reputation.",
    )
    # --- Speaker set agenda: control bill type ---
    .deal(
        "speaker_set_agenda",
        actor=alive() & (Ref("actor", "position") == "speaker"),
        params={
            "bill_type": {
                "type": "string",
                "options": (
                    "taxation",
                    "defense",
                    "welfare",
                    "radiation_cleanup",
                    "emergency_powers",
                ),
                "label": "Bill type for next vote",
            }
        },
        effects=[
            SetVar("current_bill_type", params.bill_type),
            SendMessage(
                "assembly",
                "actor",
                "The Speaker sets the agenda: {bill_type} bill for next vote.",
            ),
            Broadcast("Speaker {actor} sets agenda: {bill_type}"),
            Emit("agenda_set", {}),
        ],
        doc="Speaker sets the bill type for the next vote. Controls legislative direction.",
    )
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # VOTES (4)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- Bill vote: Cond on current_bill_type for different effects ---
    .vote(
        "bill_vote",
        voters=alive(),
        options=("support", "oppose", "abstain"),
        threshold="majority",
        outcomes={
            "support": OutcomeDef(
                doc="Bill passes — effect depends on bill type (taxation/defense/welfare/radiation/emergency)",
                effects=(
                    SetVar("bills_passed", game.bills_passed + 1),
                    Cond(
                        branches=(
                            # Taxation: redistribute caps from rich
                            (
                                game.current_bill_type == "taxation",
                                (
                                    Each(
                                        "p",
                                        alive(),
                                        [Boost("p", "caps", 10)],
                                    ),
                                    Broadcast(
                                        "Taxation bill passes! Caps redistributed."
                                    ),
                                ),
                            ),
                            # Defense: boost influence, Iron Guard bonus
                            (
                                game.current_bill_type == "defense",
                                (
                                    Each(
                                        "p",
                                        alive(),
                                        [
                                            Boost("p", "influence", 3),
                                            When(
                                                condition=Ref("p", "faction")
                                                == "iron_guard",
                                                effects=(Boost("p", "influence", 5),),
                                            ),
                                        ],
                                    ),
                                    Broadcast(
                                        "Defense bill passes! Iron Guard gains extra influence."
                                    ),
                                ),
                            ),
                            # Welfare: boost rations, Green Cult bonus
                            (
                                game.current_bill_type == "welfare",
                                (
                                    Each(
                                        "p",
                                        alive(),
                                        [
                                            Boost("p", "rations", 5),
                                            When(
                                                condition=Ref("p", "faction")
                                                == "green_cult",
                                                effects=(Boost("p", "rations", 5),),
                                            ),
                                        ],
                                    ),
                                    Broadcast(
                                        "Welfare bill passes! Green Cult gets extra rations."
                                    ),
                                ),
                            ),
                            # Radiation cleanup: reduce radiation
                            (
                                game.current_bill_type == "radiation_cleanup",
                                (
                                    Each(
                                        "p",
                                        alive(),
                                        [Damage("p", "radiation", 5)],
                                    ),
                                    Broadcast(
                                        "Radiation cleanup bill passes! Everyone healed."
                                    ),
                                ),
                            ),
                            # Emergency powers: PM gets massive boost
                            (
                                game.current_bill_type == "emergency_powers",
                                (
                                    Each(
                                        "p",
                                        alive()
                                        & (Ref("p", "position") == "prime_minister"),
                                        [
                                            Boost("p", "influence", 15),
                                            Boost("p", "caps", 30),
                                        ],
                                    ),
                                    Broadcast(
                                        "Emergency powers granted to the Prime Minister!"
                                    ),
                                ),
                            ),
                            # Default: basic achievement
                            (
                                None,
                                (
                                    Each(
                                        "p",
                                        alive(),
                                        [Boost("p", "achievements", 1)],
                                    ),
                                    Broadcast("Bill passes!"),
                                ),
                            ),
                        )
                    ),
                    Emit("bill_passed", {}),
                ),
            ),
            "oppose": OutcomeDef(
                doc="Bill defeated — no effect",
                effects=(Broadcast("Bill defeated."), Emit("bill_defeated", {})),
            ),
            "abstain": OutcomeDef(doc="Abstain — no effect", effects=()),
        },
        doc="Vote on legislation. Effect depends on bill type set by Speaker.",
    )
    # --- No confidence: remove official, Cond for PM dissolution ---
    .vote(
        "no_confidence",
        voters=alive(),
        subject={"type": "player", "filter": alive()},
        options=("remove", "keep"),
        threshold="supermajority",
        outcomes={
            "remove": OutcomeDef(
                doc="Official removed — PM removal dissolves cabinet",
                effects=(
                    # If subject is PM, dissolve cabinet
                    Cond(
                        branches=(
                            (
                                Ref("subject", "position") == "prime_minister",
                                (
                                    DissolveGroup("cabinet"),
                                    Each(
                                        "m",
                                        alive() & (Ref("m", "role") == "minister"),
                                        [SetAttr("m", "role", "backbencher")],
                                    ),
                                    SetVar("vacant_position", "prime_minister"),
                                    Broadcast("Cabinet dissolved!"),
                                ),
                            ),
                        )
                    ),
                    SetAttr("subject", "position", "none"),
                    Damage("subject", "influence", 10),
                    Broadcast(
                        "Vote of no confidence succeeds! {subject} removed from office."
                    ),
                    Emit("no_confidence_passed", {}),
                ),
            ),
            "keep": OutcomeDef(
                doc="Official survives — gains influence",
                effects=(
                    Boost("subject", "influence", 5),
                    Broadcast("{subject} survives no confidence vote."),
                ),
            ),
        },
        doc="Vote to remove an official. If PM removed, cabinet dissolves.",
    )
    # --- Expulsion: eliminate player ---
    .vote(
        "expulsion",
        voters=alive(),
        subject={"type": "player", "filter": alive()},
        options=("expel", "retain"),
        threshold="supermajority",
        outcomes={
            "expel": OutcomeDef(
                doc="Player expelled from the Assembly",
                effects=(
                    Eliminate("subject"),
                    Broadcast("{subject} is expelled from the Assembly!"),
                    Emit("expulsion", {}),
                ),
            ),
            "retain": OutcomeDef(
                doc="Player retained in the Assembly",
                effects=(Broadcast("{subject} is retained in the Assembly."),),
            ),
        },
        doc="Vote to expel a player. Requires 2/3 majority.",
    )
    # --- Elect position: fill vacant office ---
    .vote(
        "elect_position",
        voters=alive(),
        subject={"type": "player", "filter": alive()},
        options=("elect", "oppose"),
        threshold="majority",
        outcomes={
            "elect": OutcomeDef(
                doc="Elected to vacant position (Speaker/PM/Opposition Leader)",
                effects=(
                    Cond(
                        branches=(
                            # Speaker election
                            (
                                game.vacant_position == "speaker",
                                (
                                    SetAttr("subject", "position", "speaker"),
                                    Boost("subject", "influence", 5),
                                    Broadcast(
                                        "{subject} elected as Speaker of the Assembly!"
                                    ),
                                ),
                            ),
                            # PM election — create cabinet
                            (
                                game.vacant_position == "prime_minister",
                                (
                                    SetAttr("subject", "position", "prime_minister"),
                                    SetAttr("subject", "role", "leader"),
                                    Boost("subject", "influence", 10),
                                    Broadcast("{subject} elected as Prime Minister!"),
                                ),
                            ),
                            # Opposition Leader election
                            (
                                game.vacant_position == "opposition_leader",
                                (
                                    SetAttr("subject", "position", "opposition_leader"),
                                    Boost("subject", "intel", 3),
                                    Broadcast(
                                        "{subject} elected as Opposition Leader!"
                                    ),
                                ),
                            ),
                            (
                                None,
                                (
                                    Broadcast(
                                        "No position to fill — vote has no effect."
                                    ),
                                ),
                            ),
                        )
                    ),
                    # FIX-3: Auto-cycle vacant position after election
                    Cond(
                        branches=(
                            (
                                game.vacant_position == "speaker",
                                (SetVar("vacant_position", "prime_minister"),),
                            ),
                            (
                                game.vacant_position == "prime_minister",
                                (SetVar("vacant_position", "opposition_leader"),),
                            ),
                            (
                                None,
                                (SetVar("vacant_position", "none"),),
                            ),
                        )
                    ),
                    Emit("position_elected", {}),
                ),
            ),
            "oppose": OutcomeDef(
                doc="Election fails — no position filled",
                effects=(
                    Broadcast("{subject} fails to win the election."),
                    Emit("election_failed", {}),
                ),
            ),
        },
        doc="Elect a player to fill vacant position (Speaker/PM/Opposition Leader).",
    )
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SPEECH ACTS (5)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- Claim type: claim your hidden_type, verified on death/game_end ---
    .speech_act(
        "claim_type",
        act_type="claim",
        actor_filter=alive(),
        target_filter=None,
        cost={},
        verify_condition=Ref("actor", "hidden_type") == Ref("claim", "value"),
        verify_triggers=("eliminate", "game_end"),
        verify_true_effects=[
            Boost("actor", "reputation", 15),
            Broadcast("{actor}'s claim about their type was TRUE! +15 reputation."),
        ],
        verify_false_effects=[
            Damage("actor", "reputation", 20),
            Broadcast("{actor}'s claim about their type was FALSE! -20 reputation."),
        ],
        per_game=1,
        phase_filter=["caucus", "floor"],
        doc="Claim your hidden personality type. Verified on death or game end.",
    )
    # --- Accuse type: accuse a target's hidden_type ---
    .speech_act(
        "accuse_type",
        act_type="accuse",
        actor_filter=alive(),
        target_filter=alive(),
        cost={"influence": 5},
        verify_condition=Ref("target", "hidden_type") == Ref("claim", "value"),
        verify_triggers=("eliminate",),
        verify_true_effects=[
            Boost("actor", "reputation", 20),
            Boost("actor", "intel", 2),
            Broadcast(
                "{actor}'s accusation against {target} was CORRECT! +20 rep, +2 intel."
            ),
        ],
        verify_false_effects=[
            Damage("actor", "reputation", 15),
            Broadcast(
                "{actor}'s accusation against {target} was WRONG! -15 reputation."
            ),
        ],
        per_round=1,
        phase_filter=["floor"],
        doc="Accuse a target of having a specific hidden type. Costs 5 influence. Verified on target's elimination.",
    )
    # --- Promise vote: promise to vote a certain way ---
    .speech_act(
        "promise_vote",
        act_type="promise",
        actor_filter=alive(),
        cost={"reputation": 5},
        promise_action="bill_vote",
        promise_deadline=2,
        verify_true_effects=[
            Boost("actor", "reputation", 10),
            Broadcast("{actor} kept their voting promise! +10 reputation."),
        ],
        verify_false_effects=[
            Damage("actor", "reputation", 15),
            Broadcast("{actor} broke their voting promise! -15 reputation."),
        ],
        per_round=1,
        phase_filter=["caucus"],
        doc="Promise to vote a certain way on next bill. Tracked for 2 rounds. Costs 5 reputation.",
    )
    # --- Predict expulsion: predict who will be expelled ---
    .speech_act(
        "predict_expulsion",
        act_type="predict",
        actor_filter=alive(),
        target_filter=alive(),
        cost={"intel": 1},
        verify_condition=~alive(subject),
        verify_triggers=("phase_change",),
        verify_true_effects=[
            Boost("actor", "reputation", 15),
            Boost("actor", "influence", 5),
            Broadcast(
                "{actor}'s prediction about {target}'s expulsion came TRUE! +15 rep, +5 influence."
            ),
        ],
        verify_false_effects=[
            Damage("actor", "reputation", 5),
            Broadcast("{actor}'s prediction about {target} was wrong. -5 reputation."),
        ],
        per_round=1,
        phase_filter=["floor"],
        doc="Predict a player will be expelled. Costs 1 intel. Verified on phase change.",
    )
    # --- Interrogate: Opposition Leader forces a response ---
    .speech_act(
        "interrogate",
        act_type="inquire",
        actor_filter=alive()
        & ((Ref("actor", "position") == "opposition_leader") | (actor.influence >= 30)),
        target_filter=alive(),
        cost={"influence": 8},
        inquire_response_options=["answer_truthfully", "deflect", "refuse"],
        inquire_deadline=1,
        inquire_silence_effects=[
            Damage("target", "influence", 10),
            Boost("target", "suspicion", 5),
            Broadcast(
                "{target} refuses to answer interrogation! -10 influence, +5 suspicion."
            ),
        ],
        per_round=1,
        phase_filter=["floor"],
        doc="Opposition Leader or high-influence player interrogates target. Silence costs influence + suspicion.",
    )
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COMMITMENTS (4)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- Dead hand: revenge on elimination, Cond for chaotic bonus ---
    .commitment(
        "dead_hand",
        trigger="eliminate",
        effects=[
            Broadcast("{actor}'s dead hand triggers!"),
            Each(
                "p",
                alive(),
                [
                    Damage("p", "reputation", 5),
                ],
            ),
            # Chaotic types do extra damage
            Cond(
                branches=(
                    (
                        Ref("actor", "hidden_type") == "chaotic",
                        (
                            Each(
                                "p",
                                alive(),
                                [
                                    Damage("p", "influence", 3),
                                    Boost("p", "radiation", 2),
                                ],
                            ),
                            Broadcast("Chaotic dead hand — extra devastation!"),
                        ),
                    ),
                )
            ),
            Emit("dead_hand", {}),
        ],
        once=True,
        doc="When eliminated, damages everyone's reputation. Chaotic types cause extra harm.",
    )
    # --- Blood oath vengeance: surviving partner benefits ---
    .commitment(
        "blood_oath_vengeance",
        trigger="eliminate",
        guard=has_relation("actor", "blood_oath"),
        effects=[
            Each(
                "partner",
                alive() & has_relation("partner", "blood_oath"),
                [
                    Boost("partner", "influence", 10),
                    Boost("partner", "intel", 3),
                    Notify(
                        "partner",
                        "Your blood oath partner has fallen. You inherit their cause. +10 influence, +3 intel.",
                    ),
                ],
            ),
            Broadcast("{actor}'s blood oath partner inherits their legacy!"),
            Emit("blood_oath_vengeance", {}),
        ],
        once=True,
        doc="When a blood oath partner is eliminated, surviving partner gains influence and intel.",
    )
    # --- Cabinet crisis: PM death dissolves cabinet ---
    .commitment(
        "cabinet_crisis",
        trigger="eliminate",
        guard=Ref("actor", "position") == "prime_minister",
        effects=[
            DissolveGroup("cabinet"),
            Each(
                "m",
                alive() & (Ref("m", "role") == "minister"),
                [SetAttr("m", "role", "backbencher")],
            ),
            SetVar("vacant_position", "prime_minister"),
            Broadcast(
                "The Prime Minister has fallen! Cabinet dissolved. New election required."
            ),
            Emit("cabinet_crisis", {}),
        ],
        once=True,
        doc="When PM is eliminated, cabinet dissolves and new PM election is triggered.",
    )
    # --- Radiation emergency: recurring environmental crisis ---
    .commitment(
        "radiation_emergency",
        trigger="phase_change",
        guard=count_where(actor.radiation >= 60) > count_where(alive()) / 3,
        effects=[
            Each(
                "p",
                alive() & (Ref("p", "radiation") >= 60),
                [
                    Damage("p", "rations", 5),
                    Boost("p", "radiation", 3),
                    Notify(
                        "p",
                        "RADIATION EMERGENCY: You lose 5 rations and take +3 radiation!",
                    ),
                ],
            ),
            Broadcast(
                "RADIATION EMERGENCY! High-radiation players suffer ration loss."
            ),
            Emit("radiation_emergency", {}),
        ],
        once=False,
        doc="When >1/3 of players have radiation>=60, high-radiation players lose rations. Recurring.",
    )
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ROLES (factions)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    .roles(
        {
            "vault_dweller": {
                "team": "vault_dwellers",
                "count": 1,
                "doc": "+1 intel/round",
            },
            "scrap_lord": {
                "team": "scrap_lords",
                "count": 1,
                "doc": "+5 caps/round",
            },
            "green_cultist": {
                "team": "green_cult",
                "count": 1,
                "doc": "-2 radiation/round",
            },
            "iron_guard": {
                "team": "iron_guard",
                "count": 1,
                "doc": "+2 influence/round",
            },
            "free_radical": {
                "team": "free_radicals",
                "count": 1,
                "doc": "+1 intel/round, underground contacts",
            },
            "old_timer": {
                "team": "old_timers",
                "count": 1,
                "doc": "+3 reputation/round, respected elder",
            },
            "backbencher": {
                "team": "free_radicals",
                "filler": True,
                "doc": "Independent parliament member",
            },
        }
    )
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASES (7)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    .phase(
        "setup",
        category="setup",
        automatic=True,
        once=True,
        effects=[
            AssignRoles(),
            SetVar("bills_passed", 0),
            SetVar("current_bill_type", "taxation"),
            SetVar("vacant_position", "speaker"),
            # Set faction attr from team
            Each("p", alive(), [SetAttr("p", "faction", Ref("p", "team"))]),
            # Create faction groups
            CreateGroup("faction_group", members=(actor.team == "vault_dwellers")),
            CreateGroup("faction_group", members=(actor.team == "scrap_lords")),
            CreateGroup("faction_group", members=(actor.team == "green_cult")),
            CreateGroup("faction_group", members=(actor.team == "iron_guard")),
            CreateGroup("faction_group", members=(actor.team == "free_radicals")),
            CreateGroup("faction_group", members=(actor.team == "old_timers")),
            Broadcast("The Last Assembly convenes!"),
            Emit("game_start", {}),
        ],
    )
    # Election phase: elect Speaker, PM, Opposition Leader (once at start)
    .phase(
        "election",
        once=True,
        allows=["elect_position"],
        channels=["assembly"],
        duration=120,
        effects=[
            Broadcast(
                "Elections begin! Vote to fill: Speaker, Prime Minister, Opposition Leader."
            ),
        ],
    )
    # Caucus — faction coordination with faction-specific bonuses
    # starts_round=True: each caucus starts a new legislative round
    .phase(
        "caucus",
        allows=["promise", "claim_type", "promise_vote"],
        channels=["faction_caucus"],
        duration=120,
        starts_round=True,
        effects=[
            Each(
                "p",
                alive(),
                [
                    # Green Cult: -2 radiation
                    When(
                        condition=Ref("p", "faction") == "green_cult",
                        effects=(Damage("p", "radiation", 2),),
                    ),
                    # Vault Dwellers: +1 intel
                    When(
                        condition=Ref("p", "faction") == "vault_dwellers",
                        effects=(Boost("p", "intel", 1),),
                    ),
                    # Scrap Lords: +5 caps
                    When(
                        condition=Ref("p", "faction") == "scrap_lords",
                        effects=(Boost("p", "caps", 5),),
                    ),
                    # Iron Guard: +2 influence
                    When(
                        condition=Ref("p", "faction") == "iron_guard",
                        effects=(Boost("p", "influence", 2),),
                    ),
                    # Old Timers: +3 reputation
                    When(
                        condition=Ref("p", "faction") == "old_timers",
                        effects=(Boost("p", "reputation", 3),),
                    ),
                    # Free Radicals: +1 intel
                    When(
                        condition=Ref("p", "faction") == "free_radicals",
                        effects=(Boost("p", "intel", 1),),
                    ),
                    # Surviving another round is an achievement
                    Boost("p", "achievements", 1),
                ],
            ),
        ],
    )
    # Agenda — Speaker sets bill type
    .phase(
        "agenda",
        allows=["speaker_set_agenda"],
        channels=["assembly"],
        duration=60,
        effects=[
            Broadcast("The Speaker may now set the legislative agenda."),
        ],
    )
    # Floor — main negotiations, all deals + attack speech acts
    .phase(
        "floor",
        allows=[
            "promise",
            "handshake",
            "bribe",
            "trade_intel",
            "blood_oath",
            "form_coalition",
            "blackmail",
            "investigate",
            "leak_scandal",
            "appoint_position",
            "betray_coalition",
            "accuse_type",
            "predict_expulsion",
            "interrogate",
        ],
        channels=["assembly", "backroom", "intelligence_wire"],
        duration=300,
    )
    # Vote — voting on proposals
    .phase(
        "vote",
        allows=["bill_vote", "no_confidence", "expulsion", "elect_position"],
        channels=["assembly"],
        duration=90,
    )
    # Fallout — consequences (automatic)
    # next="caucus": explicit legislative cycle (fallout → caucus → agenda → ...)
    .phase(
        "fallout",
        category="resolution",
        automatic=True,
        next="caucus",
        effects=[
            # Radiation creep each round
            Each("p", alive(), [Boost("p", "radiation", 2)]),
            # Suspicion decay each round
            Each("p", alive(), [Damage("p", "suspicion", 2)]),
            # Cabinet members get bonus influence
            Each(
                "p",
                alive() & (Ref("p", "role") == "minister"),
                [Boost("p", "influence", 3)],
            ),
            # PM gets revenue
            Each(
                "p",
                alive() & (Ref("p", "position") == "prime_minister"),
                [Boost("p", "caps", 10), Boost("p", "influence", 2)],
            ),
            # Opposition Leader gets intel
            Each(
                "p",
                alive() & (Ref("p", "position") == "opposition_leader"),
                [Boost("p", "intel", 2)],
            ),
            Broadcast("The fallout settles... radiation rises."),
            Emit("round_end", {}),
        ],
    )
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # VICTORY CONDITIONS (3)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Session complete — 10 rounds, distribution scoring with suspicion/radiation as negatives
    .victory(
        "session_complete",
        when=game.round >= 10,
        type="distribution",
        score=(
            actor.caps * 25
            + actor.influence * 25
            + actor.reputation * 20
            + actor.achievements * 15
            + actor.intel * 10
            - actor.radiation * 15
            - actor.suspicion * 10
        ),
        priority=1,
        message="The Assembly concludes after 10 rounds! Final scores tallied.",
    )
    # Power consolidation — someone reaches 100 influence
    .victory(
        "power_consolidation",
        when=count_where(actor.influence >= 100) > 0,
        type="distribution",
        score=(
            actor.influence * 35
            + actor.caps * 20
            + actor.reputation * 20
            + actor.achievements * 10
            - actor.radiation * 10
            - actor.suspicion * 5
        ),
        priority=2,
        message="Power has been consolidated!",
    )
    # Apocalypse — average radiation too high
    .victory(
        "apocalypse",
        when=count_where(actor.radiation >= 80) > count_where(alive()) / 2,
        type="distribution",
        score=(
            actor.reputation * 40
            - actor.radiation * 30
            + actor.rations * 15
            + actor.achievements * 10
            - actor.suspicion * 5
        ),
        priority=3,
        message="The bunker falls to radiation...",
    )
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # AI AGENT CONTEXT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    .context(
        game_summary=(
            "Post-apocalyptic parliament with 6 factions. 10-round distribution scoring. "
            "Negotiate, legislate, investigate, and betray. Radiation rises each round."
        ),
        score_explanation=(
            "Score = caps*25 + influence*25 + reputation*20 + achievements*15 + intel*10 "
            "- radiation*15 - suspicion*10. Minimize radiation and suspicion."
        ),
        var_hints=[
            VarHint("round", "Round", format="default", priority=100),
            VarHint("bills_passed", "Bills passed", priority=80),
            VarHint("current_bill_type", "Bill type", priority=70),
            VarHint("vacant_position", "Vacant position", priority=60),
        ],
        phase_hints=[
            PhaseHint(
                "election",
                "Elect Speaker, PM, and Opposition Leader. Positions grant resources each round.",
                tips=(
                    "PM: +10 caps, +2 influence/round",
                    "Opposition Leader: +2 intel/round",
                ),
            ),
            PhaseHint(
                "caucus",
                "Faction coordination. Each faction gets a unique bonus this phase.",
                tips=(
                    "Green Cult: -2 radiation",
                    "Vault Dwellers/Free Radicals: +1 intel",
                    "Scrap Lords: +5 caps",
                    "Iron Guard: +2 influence",
                    "Old Timers: +3 reputation",
                ),
            ),
            PhaseHint(
                "agenda",
                "Speaker sets the bill type for voting. Only the Speaker can act.",
            ),
            PhaseHint(
                "floor",
                "Main negotiation phase. All deals available. Build alliances, investigate rivals, trade intel.",
                tips=(
                    "Bribe outcomes depend on hidden_type (opportunists accept more)",
                    "Investigation has 60% true / 40% false chance",
                    "Backroom messages cost +3 suspicion each",
                ),
                urgency="critical",
            ),
            PhaseHint(
                "vote",
                "Vote on bills, no-confidence motions, and expulsions.",
                tips=(
                    "Bills affect all players (taxation, defense, environment, etc.)",
                    "No-confidence can remove the PM",
                ),
            ),
        ],
        role_hints=[
            RoleHint(
                "vault_dweller",
                strategy="Information advantage: +1 intel/round. Use intel for investigation and blackmail. Trade intel for caps.",
                key_actions=("investigate", "trade_intel"),
                phase_tips={"floor": "Investigate rivals, trade intel for alliances"},
            ),
            RoleHint(
                "scrap_lord",
                strategy="Economic power: +5 caps/round. Use wealth for bribes and deals. Buy loyalty.",
                key_actions=("bribe", "trade_intel"),
                phase_tips={
                    "floor": "Bribe strategically — opportunists are easier to buy"
                },
            ),
            RoleHint(
                "green_cultist",
                strategy="Survival advantage: -2 radiation/round. Push environmental bills. Radiation kills everyone equally.",
                key_actions=("form_coalition",),
                phase_tips={"vote": "Vote for environment bills to protect everyone"},
            ),
            RoleHint(
                "iron_guard",
                strategy="Political power: +2 influence/round. Push for positions and military bills. Influence wins games.",
                key_actions=("appoint_position",),
                phase_tips={
                    "floor": "Use influence to claim positions and dominate votes"
                },
            ),
            RoleHint(
                "free_radical",
                strategy="Underground contacts: +1 intel/round. Disrupt coalitions, expose hidden types, play kingmaker.",
                key_actions=("investigate", "leak_scandal", "betray_coalition"),
                phase_tips={
                    "floor": "Investigate and expose — information is your weapon"
                },
            ),
            RoleHint(
                "old_timer",
                strategy="Reputation advantage: +3 reputation/round. Build trust, form lasting coalitions. Reputation is 20% of final score.",
                key_actions=("handshake", "form_coalition"),
                phase_tips={"floor": "Use reputation for coalition leadership"},
            ),
        ],
        channel_hints=[
            ChannelHint(
                "assembly",
                when_to_use="Public statements and formal proposals",
                strategy="Everything is on record — use for credible commitments",
            ),
            ChannelHint(
                "faction_caucus",
                when_to_use="Coordinate with your faction privately",
                strategy="Plan votes and strategies away from other factions",
            ),
            ChannelHint(
                "backroom",
                when_to_use="Secret deals and private negotiations",
                risk="Every message costs +3 suspicion",
                strategy="Use sparingly for high-value deals only",
            ),
            ChannelHint(
                "intelligence_wire",
                when_to_use="Read-only intelligence reports",
                strategy="Investigation results are broadcast here — watch for reveals",
            ),
        ],
        deal_priorities={
            "bribe": 90,
            "investigate": 85,
            "form_coalition": 80,
            "handshake": 75,
            "trade_intel": 70,
            "blackmail": 65,
            "blood_oath": 60,
            "leak_scandal": 55,
            "appoint_position": 50,
            "betray_coalition": 45,
            "promise": 40,
            "speaker_set_agenda": 95,
        },
    )
    .build()
)
