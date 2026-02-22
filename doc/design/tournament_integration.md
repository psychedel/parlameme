# Tournament System Integration

**Status:** Implemented
**Updated:** 2026-01-24

## Overview

Tournament system integrates with the unified Flow architecture. Tournaments use `flow/` as foundation layer, same as games.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         flow/ (foundation)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ load-flow    │  │ start-flow   │  │ advance-phase│              │
│  │ check-compl. │  │ end-flow     │  │ spawn-child  │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
│         ▲                 ▲                 ▲                       │
│         │                 │                 │                       │
│  ┌──────┴─────────────────┴─────────────────┴──────┐               │
│  │              Polymorphic dispatch               │               │
│  │         (multimethods by :flow/type)            │               │
│  └──────┬─────────────────┬─────────────────┬──────┘               │
│         │                 │                 │                       │
│    ┌────▼────┐       ┌────▼────┐       ┌────▼────┐                 │
│    │  Game   │       │Tournament│       │ Future  │                 │
│    │ :game   │       │:tournament│      │ Types   │                 │
│    └─────────┘       └──────────┘       └─────────┘                 │
└─────────────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

### 1. Tournament = Flow

Tournaments use the same runtime infrastructure as games:
- `:flow/type :tournament` 
- Same state primitives (entities, resources, phases)
- Same effect system (multimethod extension)

### 2. Match Template with Version

```clojure
:match-template {:flow-id :parliament-arena
                 :version "abc123"}  ;; content-hash from loader
```

### 3. Matches as Child Flows

Matches spawn as child flows with parent reference:

```clojure
{:flow/type :game
 :flow/parent {:tournament-id :tournament-001
               :stage :group-a
               :round 1}
 :flow/instance-of {:flow-id :parliament-arena
                    :version "abc123"}}
```

## Stage Types

| Stage Type | Description |
|------------|-------------|
| `:round-robin` | Each vs each |
| `:single-elimination` | Bracket, loss = out |

## Related Documents

- `unified_flow_decisions.md` — Architecture decisions
- `implementation_status.md` — Overall progress
