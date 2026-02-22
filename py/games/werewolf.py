"""Werewolf: Strategic Edition — advanced social deduction with complex roles.

Game theory foundations:
- Information asymmetry (wolves know each other, village doesn't)
- Bayesian updating (using night action results to infer roles)
- Mechanism design (seer/witch as information mechanisms)
- Commitment devices (hunter revenge, lover heartbreak as credible threats)
- Signaling (role claims, whispers as public signals)

Communication:
- Village square: public daytime discussion
- Wolf den: private wolf coordination at night
- Whisper channel: private but OBSERVABLE (others see it happened → +suspicion)

Key mechanics:
- Cupid links two lovers (third team with linked fate)
- Hunter's revenge on death (deterrence)
- Elder's vengeance if lynched by village (curse)
- Alpha wolf conversion (asymmetric info: converted knows wolves, wolves don't know!)
- Witch's dual potions (heal vs poison allocation)
- Tanner wins ONLY if lynched (creates conflict of interest)

Ported from: src/cljc/parlameme/v3/games/werewolf.cljc
"""

from engine.dsl.builder import Game
from engine.expr import Lit, Ref, actor, alive, count_where, game, params, target
from engine.runtime.effects import (
    AssignRoles,
    Boost,
    Broadcast,
    CreateGroup,
    Damage,
    Each,
    Eliminate,
    JoinGroup,
    Notify,
    Relate,
    ResolveMarked,
    ReturnStakes,
    Reveal,
    SetAttr,
    SetupVisibility,
    SetVar,
    When,
)
from engine.runtime.state import ChannelHint, OutcomeDef, ParamDef, PhaseHint, RoleHint

werewolf = (
    Game("werewolf", "Werewolf: Strategic Edition", players=(8, 24))
    # ── Resources ─────────────────────────────────────
    .resource("trust", initial=50, visibility="public", bounds=(0, 100))
    .resource("suspicion", initial=0, visibility="public", bounds=(0, 100))
    .resource("influence", initial=10, visibility="public", bounds=(0, 50))
    # Witch potions — hidden consumables
    .resource("heal_potion", initial=0, visibility="hidden", bounds=(0, 1))
    .resource("poison_potion", initial=0, visibility="hidden", bounds=(0, 1))
    # Alpha wolf conversion power
    .resource("convert_power", initial=0, visibility="hidden", bounds=(0, 1))
    # ── Attributes ────────────────────────────────────
    .attr("role", visibility="private")
    .attr("team", visibility="private")
    .attr("marked", initial=False, visibility="hidden")
    .attr("protected", initial=False, visibility="hidden")
    .attr("cursed", initial=False, visibility="hidden")
    .attr("lover", initial=None, visibility="hidden")
    .attr("aimed_at", initial=None, visibility="hidden")
    .attr("gun_loaded", initial=True, visibility="hidden")
    .attr("elder_lives", initial=0, visibility="hidden")  # elder gets 1 extra life
    # ── Roles ─────────────────────────────────────────
    .roles(
        {
            # Village
            "villager": {
                "team": "village",
                "filler": True,
                "doc": "No abilities, just voting",
            },
            "seer": {
                "team": "village",
                "count": 1,
                "doc": "See one player's true role per night",
            },
            "witch": {
                "team": "village",
                "count": 1,
                "doc": "One heal potion, one poison potion",
            },
            "hunter": {
                "team": "village",
                "count": 1,
                "doc": "Kill one player on death (revenge)",
            },
            "bodyguard": {
                "team": "village",
                "count": 1,
                "doc": "Protect player, may sacrifice self",
                "min_players": 10,
            },
            "cupid": {
                "team": "village",
                "count": 1,
                "doc": "Link two lovers at game start",
                "min_players": 10,
            },
            "elder": {
                "team": "village",
                "count": 1,
                "doc": "Survives first wolf attack, curses village if lynched",
                "min_players": 12,
            },
            # Wolves
            "werewolf": {"team": "wolves", "count": 2, "doc": "Standard wolf"},
            "alpha_wolf": {
                "team": "wolves",
                "count": 1,
                "doc": "Can convert a villager to wolf (once)",
                "min_players": 12,
            },
            # Neutral
            "tanner": {
                "team": "neutral",
                "count": 1,
                "doc": "Wins ONLY if lynched by village",
                "min_players": 10,
            },
        }
    )
    # ── Groups ────────────────────────────────────────
    .group("wolf_pack", visible=False, exclusive=True, knows_members=True)
    .group(
        "lovers", visible=False, exclusive=True, knows_members=True, linked_fate=True
    )
    .group("village_council", visible=True, exclusive=False, knows_members=True)
    # ── Channels ──────────────────────────────────────
    .channel("village_square", type="public", description="Public daytime discussion")
    .channel(
        "wolf_den",
        type="group",
        group="wolf_pack",
        description="Private wolf coordination at night",
    )
    .channel("announcements", type="broadcast", description="Deaths, events, narration")
    # Whisper channel — private but OBSERVABLE
    .channel(
        "whisper",
        type="private",
        effects=[Boost("actor", "suspicion", 2)],  # whispering raises suspicion!
        description="Private whisper — others see it happened, +2 suspicion",
    )
    # ── Night Action Deals ────────────────────────────
    # Seer vision — see player's true role
    .deal(
        "seer_vision",
        actor=alive() & (actor.role == "seer"),
        target=alive() & (actor.role != "seer"),
        per_round=1,
        effects=[
            Reveal("target", "role", to="actor"),
            Notify("actor", "Your vision reveals: {target} is {target.role}."),
        ],
        doc="Seer sees one player's true role per night.",
    )
    # Witch heal — save wolf victim
    .deal(
        "witch_heal",
        actor=alive() & (actor.role == "witch") & (actor.heal_potion > 0),
        target=alive() & (actor.marked == True),
        per_game=1,
        stakes={"actor": [("heal_potion", 1)]},
        effects=[
            SetAttr("target", "protected", True),
            Notify("actor", "You use your heal potion on {target}."),
        ],
        doc="Witch uses heal potion to save tonight's wolf victim. Once per game.",
    )
    # Witch poison — kill any player
    .deal(
        "witch_poison",
        actor=alive() & (actor.role == "witch") & (actor.poison_potion > 0),
        target=alive() & (actor.role != "witch"),
        per_game=1,
        stakes={"actor": [("poison_potion", 1)]},
        effects=[
            SetAttr("target", "marked", True),
            Notify("actor", "You poison {target}. They won't survive the night."),
        ],
        doc="Witch uses poison potion to kill any player. Once per game.",
    )
    # Bodyguard protect — normal or sacrifice
    .deal(
        "bodyguard_protect",
        actor=alive() & (actor.role == "bodyguard"),
        target=alive() & (actor.role != "bodyguard"),
        per_round=1,
        effects=[
            SetAttr("target", "protected", True),
            Notify("actor", "You guard {target} tonight."),
        ],
        doc="Bodyguard protects a player. If target is attacked, bodyguard may die instead.",
    )
    # Alpha wolf convert — turn villager into wolf
    .deal(
        "alpha_convert",
        actor=alive() & (actor.role == "alpha_wolf") & (actor.convert_power > 0),
        target=alive() & (actor.team == "village"),
        per_game=1,
        stakes={"actor": [("convert_power", 1)]},
        effects=[
            SetAttr("target", "team", "wolves"),
            JoinGroup("target", "wolf_pack"),
            Reveal("target", "team", to="actor"),  # converted knows about wolves
            # But wolves DON'T know about converted! Asymmetric info.
            Notify("actor", "You convert {target} to the wolf side."),
            Notify("target", "You have been bitten... you are now a werewolf."),
        ],
        doc="Alpha wolf converts a villager. Converted knows wolves, wolves don't know converted!",
    )
    # Cupid link — bind two lovers at game start
    # Cupid picks a target; cupid and target become linked lovers.
    .deal(
        "cupid_link",
        actor=alive() & (actor.role == "cupid"),
        target=alive() & (actor.role != "cupid"),
        per_game=1,
        effects=[
            # Both cupid and target become lovers, linked by each other's ID
            SetAttr("actor", "lover", target.id),
            SetAttr("target", "lover", actor.id),
            CreateGroup("lovers", members=["actor", "target"]),
            Notify("actor", "You link yourself with {target}. Your fates are bound."),
            Notify("target", "Cupid has linked your fate to another..."),
        ],
        doc="Cupid links self with target as lovers. If one dies, the other dies of heartbreak.",
    )
    # Hunter aim — pre-target for revenge
    .deal(
        "hunter_aim",
        actor=alive() & (actor.role == "hunter"),
        target=alive() & (actor.role != "hunter"),
        effects=[
            SetAttr("actor", "aimed_at", target.id),
            Notify("actor", "You aim at {target}. If you die, they die too."),
        ],
        doc="Hunter pre-aims at a target. On death, hunter takes them down.",
    )
    # Mafia-style wolf mark (simplified wolf kill)
    .deal(
        "wolf_mark",
        actor=alive() & (actor.team == "wolves"),
        target=alive() & (actor.team != "wolves"),
        per_round=1,
        effects=[
            SetAttr("target", "marked", True),
            Notify("actor", "The pack marks {target} for elimination."),
        ],
        doc="Wolves mark a target for elimination tonight.",
    )
    # ── Day Action Deals ──────────────────────────────
    # Accuse — public accusation
    .deal(
        "accuse",
        proposer=alive(),
        responder=alive(),
        stakes={"proposer": [("trust", 5)]},
        responses=["second", "defend"],
        outcomes={
            "second": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Boost("responder", "suspicion", 10),
                    Broadcast("{proposer} accuses {responder} — seconded!"),
                ),
                doc="Accusation seconded — target becomes suspect",
            ),
            "defend": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Boost("responder", "trust", 3),
                    Broadcast("{responder} defends against {proposer}'s accusation."),
                ),
                doc="Defense — small trust gain",
            ),
        },
        doc="Publicly accuse someone of being a wolf.",
    )
    # Claim role — casual role claim (cheap talk, no verification)
    # Compare with declare_role speech act which costs reputation and IS verified on death
    .deal(
        "claim_role",
        actor=alive(),
        per_game=2,
        params={
            "claimed_role": {
                "type": "keyword",
                "options": (
                    "villager",
                    "seer",
                    "witch",
                    "hunter",
                    "bodyguard",
                    "cupid",
                    "elder",
                ),
                "label": "Role you claim",
            }
        },
        effects=[
            Broadcast("{actor} casually claims to be a {claimed_role}."),
        ],
        doc="Casual role claim (cheap talk). Use 'declare_role' speech act for verified claims under oath.",
    )
    # Form village council
    .deal(
        "form_council",
        proposer=alive() & (actor.team == "village"),
        responder=alive(),
        responses=["join", "decline"],
        outcomes={
            "join": OutcomeDef(
                effects=(
                    CreateGroup("village_council", members=["proposer", "responder"]),
                    Broadcast("{proposer} and {responder} form a village council."),
                ),
                doc="Council formed — visible alliance",
            ),
            "decline": OutcomeDef(
                effects=(Broadcast("{responder} declines the council invitation."),),
                doc="Declined — no effect",
            ),
        },
        doc="Form a village council for coordinated voting.",
    )
    # ── Votes ─────────────────────────────────────────
    # Lynch vote — weighted by influence
    .vote(
        "lynch",
        voters=alive(),
        subject={"type": "player", "filter": alive()},
        options=("lynch", "spare"),
        threshold="majority",
        outcomes={
            "lynch": OutcomeDef(
                effects=(
                    SetVar("last_lynched_role", Ref("subject", "role")),
                    Eliminate("subject"),
                    Reveal("subject", "role", to="public"),
                    Broadcast("{subject} is lynched! They were a {subject.role}."),
                ),
                doc="Lynch the accused — role revealed on death",
            ),
            "spare": OutcomeDef(
                effects=(Broadcast("The village spares {subject}."),),
                doc="Target is spared",
            ),
        },
        doc="Village votes to lynch or spare the accused.",
    )
    # ── Commitments ───────────────────────────────────
    # Hunter's revenge — automatic on death
    .commitment(
        "hunter_revenge",
        trigger="eliminate",
        guard=actor.role == "hunter",
        effects=[
            When(
                condition=actor.aimed_at != None,
                effects=(
                    Eliminate(actor.aimed_at),
                    Broadcast("The Hunter takes their final shot at {actor.aimed_at}!"),
                ),
            ),
        ],
        once=True,
        doc="When Hunter dies, kills their aimed target.",
    )
    # Lover heartbreak — die if partner dies
    # When a lover is eliminated, eliminate their specific partner (via lover attr)
    .commitment(
        "lover_heartbreak",
        trigger="eliminate",
        guard=actor.lover != None,
        effects=[
            # actor.lover holds the partner's entity ID — eliminate them directly
            Eliminate(actor.lover),
            Broadcast("A lover dies of heartbreak!"),
        ],
        once=True,
        doc="When a lover dies, the other dies of heartbreak.",
    )
    # Elder's vengeance — if elder is eliminated, all villagers lose trust
    .commitment(
        "elder_vengeance",
        trigger="eliminate",
        guard=(actor.role == "elder"),
        effects=[
            Broadcast("The Elder's dying curse falls upon the village!"),
            Each(
                "p",
                alive() & (actor.team == "village"),
                [
                    Damage("p", "trust", 15),
                    Damage("p", "influence", 5),
                ],
            ),
        ],
        once=True,
        doc="When the Elder dies, all surviving villagers lose trust and influence.",
    )
    # ── Speech Acts ───────────────────────────────────
    # Declare role: "I am seer" — verified on death (distinct from claim_role deal)
    .speech_act(
        "declare_role",
        act_type="claim",
        actor_filter=alive(),
        params={"role": ParamDef(type="keyword", label="Role you claim to be")},
        verify_condition=(actor.role == params.role),
        verify_triggers=("eliminate",),
        verify_true_effects=[Boost("actor", "trust", 20)],
        verify_false_effects=[
            Damage("actor", "trust", 25),
            Boost("actor", "suspicion", 15),
        ],
        endorsement_cost={"trust": 5},
        per_game=2,
        doc="Declare your role under oath. Verified on death — truth gains trust, lies destroy it. Endorsing costs 5 trust.",
    )
    # Predict death: "Dave will die tonight" — auto-checked at dawn
    .speech_act(
        "predict_death",
        act_type="predict",
        actor_filter=alive(),
        target_filter=alive(),
        cost={"influence": 3},
        verify_condition=~alive(
            target
        ),  # target is dead (not alive) at verification time
        verify_triggers=("phase_change",),
        verify_true_effects=[
            Boost("actor", "trust", 15),
            Boost("actor", "influence", 5),
        ],
        verify_false_effects=[Damage("actor", "trust", 5)],
        per_round=1,
        doc="Predict a player will die this night. Costs 3 influence. Checked at dawn.",
    )
    # ── Phases ────────────────────────────────────────
    # Setup — assign roles, create wolf pack
    .phase(
        "setup",
        category="setup",
        automatic=True,
        once=True,
        effects=[
            AssignRoles(),
            SetVar("last_lynched_role", ""),
            CreateGroup("wolf_pack", members=(actor.team == "wolves")),
            SetupVisibility(),
            # Give witch potions
            Each(
                "p",
                actor.role == "witch",
                [
                    Boost("p", "heal_potion", 1),
                    Boost("p", "poison_potion", 1),
                ],
            ),
            # Give alpha wolf convert power
            Each(
                "p",
                actor.role == "alpha_wolf",
                [
                    Boost("p", "convert_power", 1),
                ],
            ),
            # Give elder extra life
            Each(
                "p",
                actor.role == "elder",
                [
                    SetAttr("p", "elder_lives", 1),
                ],
            ),
        ],
    )
    # First night — cupid links lovers
    .phase(
        "first_night",
        once=True,
        allows=["cupid_link", "seer_vision", "hunter_aim"],
        channels=["wolf_den"],
        duration=60,
    )
    # Night — all night actions
    # starts_round=True: each night starts a new round
    .phase(
        "night",
        allows=[
            "seer_vision",
            "witch_heal",
            "witch_poison",
            "bodyguard_protect",
            "alpha_convert",
            "hunter_aim",
            "wolf_mark",
        ],
        channels=["wolf_den"],
        duration=90,
        starts_round=True,
        effects=[
            # Reset protection and marks at night start
            Each(
                "p",
                alive(),
                [
                    SetAttr("p", "protected", False),
                    SetAttr("p", "marked", False),
                ],
            ),
        ],
    )
    # Dawn — resolve deaths
    .phase(
        "dawn",
        category="resolution",
        automatic=True,
        effects=[ResolveMarked()],
    )
    # Day — discussion, accusations, and speech acts
    .phase(
        "day",
        allows=[
            "accuse",
            "claim_role",
            "form_council",
            "declare_role",
            "predict_death",
        ],
        channels=["village_square", "whisper", "announcements"],
        duration=240,
    )
    # Trial — lynch voting
    .phase(
        "trial",
        allows=["lynch"],
        channels=["village_square"],
        duration=60,
    )
    # Dusk — check victory, advance round
    # next="night": explicit day-night cycle (dusk always leads to night)
    .phase(
        "dusk",
        category="resolution",
        automatic=True,
        next="night",
        effects=[
            # Decay suspicion slightly each round
            Each("p", alive(), [Damage("p", "suspicion", 5)]),
        ],
    )
    # ── Victory Conditions ────────────────────────────
    # Tanner wins if lynched (highest priority — checked via last_lynched_role)
    .victory(
        "tanner_wins",
        when=game.last_lynched_role == "tanner",
        type="single",
        team="neutral",
        priority=1,
        message="The Tanner was lynched — they win!",
    )
    # Village wins — all wolves eliminated
    .victory(
        "village_wins",
        when=count_where(alive() & (actor.team == "wolves")) == 0,
        type="single",
        team="village",
        priority=2,
        message="All wolves eliminated! Village wins!",
    )
    # Wolves win — outnumber village
    .victory(
        "wolves_win",
        when=count_where(alive() & (actor.team == "wolves"))
        >= count_where(alive() & (actor.team == "village")),
        type="single",
        team="wolves",
        priority=3,
        message="Wolves outnumber the village! Wolves win!",
    )
    # ── AI Agent Context ──────────────────────────────
    .context(
        game_summary=(
            "Social deduction: village vs werewolves. Wolves kill at night, "
            "village votes to lynch by day. Special roles create information asymmetry."
        ),
        score_explanation=(
            "Team victory: village wins when all wolves dead; "
            "wolves win when they equal or outnumber village."
        ),
        role_hints=[
            RoleHint(
                "seer",
                strategy="Investigate the most suspicious or influential player each night. Share findings carefully — wolves will target you.",
                allies=("bodyguard",),
                threats=("werewolf", "alpha_wolf"),
                key_actions=("seer_vision",),
                phase_tips={
                    "night": "Investigate the player with most suspicious behavior",
                    "day": "Share vision results but consider if wolves are watching",
                },
            ),
            RoleHint(
                "werewolf",
                strategy="Coordinate kills in wolf den. Deflect suspicion during day. Target information roles first.",
                allies=("alpha_wolf",),
                threats=("seer", "witch"),
                key_actions=("wolf_mark",),
                phase_tips={
                    "night": "Kill the seer or witch if identified",
                    "day": "Accuse others, avoid being too quiet or too loud",
                },
            ),
            RoleHint(
                "witch",
                strategy="Save heal potion for confirmed village; poison a confirmed wolf. Both potions are one-use.",
                threats=("werewolf",),
                key_actions=("witch_heal", "witch_poison"),
                phase_tips={
                    "night": "Heal the wolf target if you trust them, or save potion for later"
                },
            ),
            RoleHint(
                "bodyguard",
                strategy="Protect the most valuable village role (seer, witch).",
                allies=("seer", "witch"),
                key_actions=("bodyguard_protect",),
                phase_tips={"night": "Protect whoever the seer is, if known"},
            ),
            RoleHint(
                "hunter",
                strategy="Your gun fires on death — aim at the most suspicious player. This makes you a deterrent against lynching.",
                key_actions=("hunter_aim",),
                phase_tips={
                    "day": "Announce your role if under threat — deters wolves"
                },
            ),
            RoleHint(
                "alpha_wolf",
                strategy="Convert a trusted villager to your side. Conversion is hidden from other wolves initially.",
                allies=("werewolf",),
                key_actions=("alpha_convert",),
                phase_tips={
                    "night": "Convert a player others trust — they become your spy"
                },
            ),
            RoleHint(
                "tanner",
                strategy="Get yourself lynched to win. Act suspicious but not obviously so. The tanner wins alone.",
                phase_tips={"day": "Act just suspicious enough to get voted out"},
            ),
            RoleHint(
                "villager",
                strategy="Observe behavior, coordinate votes, share suspicions. Your power is collective.",
                phase_tips={
                    "day": "Watch for inconsistencies in claims and voting patterns"
                },
            ),
            RoleHint(
                "cupid",
                strategy="Link two lovers on night 1. If one dies, the other dies too. Choose wisely.",
                key_actions=("cupid_link",),
                phase_tips={
                    "first_night": "Link two players who will protect each other"
                },
            ),
            RoleHint(
                "elder",
                strategy="You survive one extra lynch. Use this to make bold claims — you have a safety net.",
                phase_tips={
                    "day": "You can afford to be vocal — the first lynch won't kill you"
                },
            ),
        ],
        phase_hints=[
            PhaseHint(
                "night",
                "Wolves choose a target. Special roles act. Stay alert for information.",
                urgency="critical",
            ),
            PhaseHint(
                "first_night",
                "Cupid links lovers. Seer gets first vision. Hunter aims.",
                urgency="critical",
            ),
            PhaseHint(
                "day",
                "Discuss, accuse, and share information. Use speech acts to build credibility.",
                tips=(
                    "Claims cost trust — only claim if you can back it up",
                    "Watch who votes for whom",
                ),
            ),
            PhaseHint(
                "trial",
                "Lynch vote. Majority required to eliminate.",
                urgency="critical",
                tips=("Tanner wants to be lynched — watch for reverse psychology",),
            ),
        ],
        channel_hints=[
            ChannelHint(
                "village_square",
                when_to_use="Public discussion during day",
                strategy="Build coalitions, share information carefully",
            ),
            ChannelHint(
                "wolf_den",
                when_to_use="Wolf coordination at night",
                strategy="Agree on target, plan daytime alibis",
                risk="Only accessible to wolves",
            ),
            ChannelHint(
                "whisper",
                when_to_use="Private 1-on-1 communication",
                risk="Others see you whispered — adds +2 suspicion",
                strategy="Use sparingly for critical info exchange",
            ),
            ChannelHint(
                "announcements",
                when_to_use="Read-only official announcements",
            ),
        ],
    )
    .build()
)
