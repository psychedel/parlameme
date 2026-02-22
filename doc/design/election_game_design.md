# Election Race: Game Design Document

## Overview

**Election Race** is a political-economic negotiation game based on game theory principles. Players compete in a multi-round election campaign where they must balance public image, campaign finances, coalition-building, and strategic attacks on opponents.

## Game Theory Foundation

The game incorporates several classic game theory concepts:

### 1. Prisoner's Dilemma (Attack vs Cooperate)
- Players can attack opponents (negative campaigning) or cooperate (policy alliances)
- Mutual cooperation benefits all, but defection is tempting
- Repeated game dynamics allow for reputation and retaliation

### 2. Auction Mechanics (Resource Allocation)
- Campaign funds must be allocated to different regions/demographics
- All-pay auction for key endorsements
- Budget constraints force strategic prioritization

### 3. Signaling Theory (Information Asymmetry)
- Secret agendas that affect scoring
- Public promises vs private intentions
- Endorsements as credible signals

### 4. Coalition Formation (Cooperative Game Theory)
- Form alliances for mutual benefit
- Shapley value-like distribution of coalition benefits
- Betrayal mechanics with reputation costs

### 5. Voting Theory (Mechanism Design)
- Primary elections within parties
- General election with regional dynamics
- Strategic voting and spoiler effects

## Core Mechanics

### Resources

| Resource | Visibility | Description | Mechanics |
|----------|------------|-------------|-----------|
| **Campaign Funds** | Private | Money for ads, rallies, attacks | Transferable, main economy |
| **Popularity** | Public | General public approval | 0-100, affects election |
| **Credibility** | Public | Trust from voters | Affects promise strength |
| **Influence** | Private | Political connections | Used for endorsements, deals |
| **Scandal Points** | Hidden | Accumulated dirt | Can be exposed by opponents |

### Attributes

| Attribute | Description |
|-----------|-------------|
| **Party** | Democrat, Republican, Independent, Third-Party |
| **Ideology** | Progressive, Moderate, Conservative (0-100 spectrum) |
| **Secret Agenda** | Hidden victory modifier (affects final scoring) |
| **Endorsements** | Set of acquired endorsements |
| **Promises** | Public commitments made during campaign |

### Phases

```
SETUP → PRIMARY_CAMPAIGN → PRIMARY_VOTE → 
      → GENERAL_CAMPAIGN (x3 rounds) → DEBATE → 
      → FINAL_PUSH → ELECTION_DAY → TRANSITION
```

#### 1. Setup Phase (Automatic)
- Assign party affiliations
- Distribute initial resources
- Reveal secret agendas to players

#### 2. Primary Campaign (Action)
- Negotiate with party members
- Make policy promises
- Seek early endorsements
- Duration: 5 minutes

#### 3. Primary Vote (Resolution)
- Party members vote for nominee
- Winner becomes general election candidate
- Losers can endorse or stay neutral

#### 4. General Campaign (Action, 3 rounds)
- **Round 1**: Regional focus (swing states)
- **Round 2**: Demographic appeal (key groups)
- **Round 3**: Final messaging
- Duration: 4 minutes per round

#### 5. Debate Phase (Action)
- Direct confrontation
- Expose scandals or defend
- High-stakes credibility game

#### 6. Final Push (Action)
- Last-minute deals
- GOTV (Get Out The Vote) allocation
- Duration: 3 minutes

#### 7. Election Day (Resolution)
- Regional votes tallied
- Electoral college calculation
- Winner determination

#### 8. Transition (Resolution)
- Score calculation
- Promise fulfillment check
- Final rankings

### Deals

#### Negotiation Deals

1. **Policy Alliance** (Two-party)
   - Agree on shared policy position
   - Both gain credibility, share voter base
   - Betrayal damages both reputations

2. **Endorsement Trade** (Two-party)
   - Trade endorsements for favors
   - Public commitment creates accountability
   - Stakes: Influence

3. **Opposition Research** (Single-party)
   - Invest funds to find scandals
   - Hidden action, delayed effect
   - Risk: can backfire

4. **Attack Ad** (Single-party)
   - Spend funds to damage opponent
   - Public, damages attacker's credibility too
   - "Going negative"

5. **Coalition Formation** (Multi-party)
   - Form voting bloc
   - Share campaign resources
   - Joint policy platform

6. **Secret Deal** (Two-party)
   - Private agreement
   - No public accountability
   - Higher betrayal risk, higher reward

7. **Debate Challenge** (Two-party)
   - Force direct confrontation
   - Winner gains, loser suffers
   - Stakes: Credibility

8. **Fundraising Event** (Single-party)
   - Convert influence to funds
   - Public event affects popularity

#### Voting Mechanisms

1. **Primary Vote**
   - Party members vote
   - Plurality wins nomination
   - Public voting

2. **Endorsement Vote**
   - Interest groups decide support
   - Weighted by group size
   - Private deliberation, public result

3. **Regional Election**
   - Per-region tallies
   - Electoral college points
   - Based on campaign investment + popularity

### Victory Conditions

#### Primary Victory
```clojure
{:type :single
 :when '(= :phase :post-primary)
 :for :party-nominee
 :message "{winner} wins the {party} nomination!"}
```

#### General Election Victory
```clojure
{:type :distribution
 :when '(= :phase :election-day)
 :score '(+ (* 0.40 [:actor :electoral-votes])
            (* 0.30 [:actor :popular-vote-share])
            (* 0.20 [:actor :promises-kept])
            (* 0.10 [:actor :credibility]))}
```

### Communication Channels

| Channel | Type | Description |
|---------|------|-------------|
| **Public Square** | Public | Open campaign discussion |
| **Party Caucus** | Group | Party-internal coordination |
| **Backroom** | Private | Secret negotiations |
| **Press Conference** | Broadcast | Official announcements |

### Unique Mechanics

#### 1. Promise System
Players make public promises during campaign:
- Promises are tracked and visible
- Breaking promises damages credibility
- Keeping promises gives bonus points
- Strategic ambiguity is valuable

#### 2. Scandal System
Hidden "dirt" accumulates:
- Opposition research reveals scandals
- Scandals reduce popularity when exposed
- Can preemptively "get ahead" of scandals
- Scandal immunity from early disclosure

#### 3. Regional Dynamics
Different regions have different values:
- Swing states worth more electoral votes
- Demographics within regions
- Campaign spending allocation per region

#### 4. Endorsement Market
Endorsements from interest groups:
- Labor unions, Business groups, Religious orgs
- Each endorsement has voter bloc attached
- Competitive bidding or negotiation

## Game Theory Analysis

### Nash Equilibria

1. **Attack Equilibrium**: When all players attack, everyone suffers (credibility damage), but no one can unilaterally switch to cooperation without losing ground.

2. **Cooperation Equilibrium**: Stable policy alliances can emerge if reputation costs for betrayal are high enough.

3. **Mixed Strategy**: Optimal play often involves probabilistic attacks, keeping opponents uncertain.

### Mechanism Design Goals

1. **Incentive Compatibility**: The game rewards honest signaling over pure bluffing
2. **Budget Balance**: Total resources are conserved (no money creation)
3. **Individual Rationality**: All players have positive expected value from participation
4. **Pareto Efficiency**: No way to make everyone better off

### Information Structure

| Information | Who Knows | When Revealed |
|-------------|-----------|---------------|
| Party affiliation | All | Start |
| Campaign funds | Self only | End |
| Scandal points | Opponent | On exposure |
| Secret agenda | Self only | End |
| Promises | All | When made |
| Private deals | Parties only | Never (unless broken) |

## Player Count Scaling

| Players | Parties | Primary | Mechanics |
|---------|---------|---------|-----------|
| 4-5 | 2 | 2 candidates each | Simple coalition |
| 6-8 | 2-3 | 3 candidates each | Full mechanics |
| 9-12 | 3-4 | Complex primary | Advanced coalition |
| 13-16 | 4 | Full simulation | Maximum chaos |

## AI Agent Strategy Guide

### For Moderate Candidates
- Form early coalitions
- Avoid extreme positions
- Build credibility over time
- Use influence for endorsements

### For Extreme Candidates
- Mobilize base voters
- Attack moderates
- Make bold promises
- High-risk, high-reward strategy

### For Frontrunners
- Protect lead
- Avoid risky deals
- Selective attacks
- Save resources for final push

### For Underdogs
- Take risks
- Seek kingmaker deals
- Expose scandals
- Disrupt status quo

## Implementation Notes

### Expression Patterns
```clojure
;; Count party members
'(count-where (= [:actor :party] :democrat))

;; Check if in coalition
'(in-group? :actor :coalition)

;; Electoral vote calculation
'(sum-resource :electoral-votes)
```

### Effect Patterns
```clojure
;; Scandal exposure
[:when '(> [:target :scandal-points] 0)
 [:damage :target :popularity 15]
 [:damage :target :credibility 10]
 [:set-attr :target :scandal-exposed true]]

;; Promise tracking
[:set-attr :actor :promises (conj [:actor :promises] :promise-id)]
```

## Comparison to Existing Games

| Aspect | Mafia | Parliament Arena | Election Race |
|--------|-------|------------------|---------------|
| Hidden info | Roles | Private resources | Scandals, agendas |
| Victory | Team | Distribution | Hybrid |
| Cooperation | Team-based | Coalition-based | Alliance-based |
| Communication | Day/Night split | Floor/Caucus | Multi-channel |
| Elimination | Yes | Via expulsion | Via primary loss |

## Open Questions

1. Should losing primary candidates become voters only, or have reduced agency?
2. How to balance attack incentives vs cooperation incentives?
3. Regional vs national popularity tracking complexity?
4. Real-time debate mechanics vs turn-based?
5. Third-party spoiler effect implementation?

## Next Steps

1. Implement basic version with 2 parties, 4-6 players
2. Test coalition dynamics
3. Add scandal/promise systems
4. Expand to full regional model
5. Balance through playtesting
