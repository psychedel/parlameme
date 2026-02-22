# Tournament Hub Design

## Overview

The Tournament Hub is a comprehensive UI system for browsing, viewing, and interacting with tournaments. It integrates with the existing game client and leverages the spec-driven tournament components already built.

## Goals

1. **Browse tournaments** - Filter by status, type, search by name
2. **View tournament details** - Bracket, standings, matches, participants
3. **Real-time updates** - WebSocket-driven live state
4. **Seamless integration** - Works within game client, not separate app
5. **Mobile-first** - Responsive design matching game UI

## Architecture Decision: Single SPA vs Multi-Page

**Decision: Extend existing game client with view routing**

Rationale:
- History browser (`/history.html`) already exists as separate app for admin analytics
- Tournament Hub is player-facing, should be integrated with game flow
- Player can browse → register → join match seamlessly
- Share WebSocket connection and auth state
- Reuse existing UI components and theme

## View Structure

```
┌─────────────────────────────────────────────────────────────┐
│                     GAME CLIENT                              │
├─────────────────────────────────────────────────────────────┤
│  View Router (new)                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐│
│  │   Lobby     │  │  Tournament  │  │      Game           ││
│  │   View      │  │    Hub       │  │      View           ││
│  │  (existing) │  │   (new)      │  │    (existing)       ││
│  └─────────────┘  └──────────────┘  └─────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

### Tournament Hub Views

```
/tournaments                    → Tournament list (browse/filter)
/tournaments/:id                → Tournament details (bracket, standings)
/tournaments/:id/match/:mid     → Match detail modal
```

## State Management

### New DB Keys

```clojure
;; In game/db.cljs, extend default-db:

:tournaments {
  ;; List view state
  :list []                       ; [{:tournament-id :name :status :type :host :participant-count}]
  :list-loading? false
  :list-error nil
  
  ;; Filters
  :filters {
    :status nil                  ; :registration | :in-progress | :completed | nil (all)
    :type nil                    ; :round-robin | :single-elimination | etc | nil
    :search ""                   ; text search
  }
  
  ;; Selected tournament detail
  :selected-id nil
  :detail nil                    ; Full tournament state when viewing details
  :detail-loading? false
  
  ;; Registration state
  :registration {
    :submitting? false
    :error nil
  }
}

;; View routing
:view :lobby                     ; :lobby | :tournaments | :tournament-detail | :game
```

### Subscriptions (new)

```clojure
;; List view
:tournaments/list               ; filtered tournament list
:tournaments/list-loading?
:tournaments/filters
:tournaments/has-more?          ; pagination

;; Detail view  
:tournaments/selected-id
:tournaments/detail             ; full tournament state
:tournaments/detail-loading?
:tournaments/bracket-spec       ; UI spec for bracket
:tournaments/match-spec         ; UI spec for match cards
:tournaments/standings-spec     ; UI spec for standings
:tournaments/participants
:tournaments/matches
:tournaments/my-registration    ; current player's registration status

;; Navigation
:app/view                       ; current view
:app/can-go-back?
```

### Events (new)

```clojure
;; Navigation
:app/navigate                   ; [_ view & params]
:app/go-back

;; Tournament list
:tournaments/fetch              ; fetch list with current filters
:tournaments/fetch-success      ; [_ tournaments]
:tournaments/fetch-error        ; [_ error]
:tournaments/set-filter         ; [_ filter-key value]
:tournaments/clear-filters

;; Tournament detail
:tournaments/select             ; [_ tournament-id] - navigate to detail
:tournaments/load-detail        ; [_ tournament-id]
:tournaments/detail-loaded      ; [_ tournament-data]
:tournaments/clear-selection

;; Registration
:tournaments/register           ; [_ tournament-id]
:tournaments/register-success
:tournaments/register-error

;; WebSocket handlers
:tournament/state-update        ; real-time state push
:tournament/match-update        ; match result push
```

## Component Structure

### New Files

```
src/cljs/parlameme/
├── game/
│   ├── router.cljs             ; View routing logic
│   └── ...
├── tournaments/
│   ├── views.cljs              ; Tournament Hub views
│   ├── subs.cljs               ; Subscriptions
│   ├── events.cljs             ; Event handlers
│   └── sente.cljs              ; WebSocket handlers
```

### Component Hierarchy

```
[root-view]
  ├── [lobby-view]              ; existing
  ├── [tournament-hub]          ; NEW - list view
  │     ├── [hub-header]
  │     ├── [tournament-filters]
  │     └── [tournament-list]
  │           └── [tournament-card] (per item)
  ├── [tournament-detail-view]  ; NEW - detail view
  │     ├── [detail-header]
  │     ├── [tournament-info-panel]
  │     ├── [registration-panel]
  │     ├── [bracket-view]      ; existing from ui/spec/tournament
  │     ├── [standings-table]   ; existing from ui/spec/tournament
  │     └── [matches-list]
  └── [game-view]               ; existing
```

## UI Components

### Tournament Card

```
┌────────────────────────────────────────────┐
│ 🏆 Weekly Duel Championship                │
│ ┌──────┐                                   │
│ │ SE   │  Single Elimination               │
│ └──────┘  Host: alice                      │
│                                            │
│ 👥 8/16 participants    📅 Starts in 2h   │
│                                            │
│ [Registration Open]              [View →]  │
└────────────────────────────────────────────┘
```

Props:
- `tournament`: tournament summary data
- `on-click`: navigate to detail
- `on-register`: quick register action

### Tournament Filters

```
┌────────────────────────────────────────────┐
│ 🔍 [Search tournaments...              ]   │
│                                            │
│ Status: [All ▼]  Type: [All ▼]  [Clear]   │
│                                            │
│ ○ All  ● Registration  ○ In Progress       │
└────────────────────────────────────────────┘
```

### Tournament Detail Header

```
┌────────────────────────────────────────────────────────────┐
│ ← Back                                                     │
│                                                            │
│ 🏆 Weekly Duel Championship                                │
│ Single Elimination • Hosted by alice                       │
│                                                            │
│ ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│ │    8     │  │    3     │  │   12     │  │  45min   │   │
│ │ Players  │  │ Rounds   │  │ Matches  │  │ Est.Time │   │
│ └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│                                                            │
│ [Register Now]                              [Spectate]     │
└────────────────────────────────────────────────────────────┘
```

### Registration Panel

Shown when tournament is in `:registration` status:

```
┌────────────────────────────────────────────┐
│ Registration Open                          │
│                                            │
│ 8 of 16 spots filled                       │
│ ████████░░░░░░░░                          │
│                                            │
│ Closes in: 1h 23m                         │
│                                            │
│ Entry fee: 100 caps                       │
│                                            │
│ [Register] ← disabled if already in       │
│ ✓ You are registered                      │
└────────────────────────────────────────────┘
```

### Match Detail Modal

Overlay when clicking a match in bracket:

```
┌────────────────────────────────────────────┐
│ Match #3 - Semifinals              [Close] │
├────────────────────────────────────────────┤
│                                            │
│   alice        2  ━━━┓                     │
│                     ┃                      │
│   bob          1  ━━━┛                     │
│                                            │
│ Status: Completed                          │
│ Duration: 12m 34s                          │
│ Game: Duel                                 │
│                                            │
│ [View Game Replay]    [View Game History]  │
└────────────────────────────────────────────┘
```

## Data Flow

### Fetching Tournament List

```
User opens Tournament Hub
  │
  ▼
dispatch [:tournaments/fetch]
  │
  ├─► HTTP GET /api/tournaments?status=...&type=...
  │     │
  │     ▼
  │   [:tournaments/fetch-success tournaments]
  │     │
  │     ▼
  │   Update :tournaments/list in db
  │
  └─► Subscribe to WebSocket :tournament/* events
        for real-time updates
```

### Viewing Tournament Detail

```
User clicks tournament card
  │
  ▼
dispatch [:tournaments/select tournament-id]
  │
  ├─► Update :view to :tournament-detail
  ├─► Update :tournaments/selected-id
  │
  ▼
dispatch [:tournaments/load-detail tournament-id]
  │
  ├─► HTTP GET /api/tournaments/:id
  │     │
  │     ▼
  │   [:tournaments/detail-loaded data]
  │
  └─► WebSocket :tournament/subscribe tournament-id
        for real-time match updates
```

### Registration Flow

```
User clicks Register
  │
  ▼
dispatch [:tournaments/register tournament-id]
  │
  ├─► Set :tournaments/registration/submitting? true
  │
  ├─► WebSocket send :tournament/register
  │     │
  │     ▼
  │   Server validates:
  │   - Tournament in registration
  │   - Player not already registered
  │   - Player meets requirements
  │     │
  │     ▼
  │   Server responds :tournament/registered
  │     or :tournament/register-error
  │
  └─► Update local state
```

## WebSocket Integration

### New Client Handlers

```clojure
;; In tournaments/sente.cljs

(defmethod handle-event :tournament/list-update [[_ tournaments]]
  ;; Bulk update when list changes
  (rf/dispatch [:tournaments/list-updated tournaments]))

(defmethod handle-event :tournament/state [[_ {:keys [tournament-id] :as state}]]
  ;; Real-time state update for viewed tournament
  (rf/dispatch [:tournaments/state-update tournament-id state]))

(defmethod handle-event :tournament/match-result [[_ {:keys [tournament-id match-id] :as result}]]
  ;; Match completed notification
  (rf/dispatch [:tournaments/match-update tournament-id match-id result]))

(defmethod handle-event :tournament/registered [[_ {:keys [tournament-id participant-id]}]]
  ;; Registration confirmed
  (rf/dispatch [:tournaments/register-success tournament-id participant-id]))

(defmethod handle-event :tournament/started [[_ {:keys [tournament-id]}]]
  ;; Tournament began
  (rf/dispatch [:tournaments/started tournament-id])
  (rf/dispatch [:ui/notify {:type :info :text "Tournament has started!"}]))
```

### Subscribing to Tournament Updates

When viewing a tournament detail, subscribe for real-time updates:

```clojure
(rf/reg-event-fx
 :tournaments/load-detail
 (fn [{:keys [db]} [_ tournament-id]]
   {:db (assoc-in db [:tournaments :detail-loading?] true)
    :http-xhrio {:method :get
                 :uri (str "/api/tournaments/" (name tournament-id))
                 :on-success [:tournaments/detail-loaded]
                 :on-failure [:tournaments/detail-error]}
    ;; Subscribe to real-time updates
    :ws-send [:tournament/subscribe {:tournament-id tournament-id}]}))
```

## Navigation Integration

### Extending root-view

```clojure
(defn root-view []
  (let [view (<sub [:app/view])
        modal (<sub [:ui/modal])]
    [:div {:class "min-h-screen bg-space-950"}
     ;; Main content based on view
     (case view
       :lobby [lobby-view]
       :tournaments [tournament-hub]
       :tournament-detail [tournament-detail-view]
       :game [game-view]
       [lobby-view])  ; default
     
     ;; Modal overlay
     (when modal
       [modal-container])]))
```

### Adding Hub Access from Lobby

```clojure
;; In lobby-view, add button to access Tournament Hub
[:button {:class "..."
          :on-click #(rf/dispatch [:app/navigate :tournaments])}
 "🏆 Tournaments"]
```

## Mobile Considerations

### Responsive Layout

- Tournament list: Single column cards on mobile
- Tournament detail: Stacked sections (info → bracket → standings)
- Bracket: Horizontal scroll with swipe gestures
- Filters: Collapsible panel / bottom sheet on mobile

### Touch Interactions

- Swipe left on tournament card → Quick actions (register, spectate)
- Pull to refresh on list
- Tap match in bracket → Show detail modal
- Long press → Context menu (share, report)

## Implementation Order

### Phase 1: Core Structure (Day 1)
1. Create `tournaments/` directory with events.cljs, subs.cljs
2. Extend game/db.cljs with tournament state
3. Add view routing to root-view
4. Create basic tournament-hub view with placeholder

### Phase 2: List View (Day 1-2)
1. Implement tournament-card component
2. Implement filters component
3. Connect to HTTP API for fetching
4. Add real-time list updates via WebSocket

### Phase 3: Detail View (Day 2)
1. Implement detail-header component
2. Integrate existing bracket-view and standings-table
3. Implement registration-panel
4. Connect to detail API

### Phase 4: Real-time & Polish (Day 3)
1. WebSocket subscriptions for live updates
2. Match detail modal with game links
3. Loading states and error handling
4. Mobile responsive tweaks
5. Animations and transitions

## Testing Strategy

### Unit Tests
- Subscriptions return correct filtered data
- Events update state correctly
- Component rendering with various props

### Integration Tests
- Full flow: browse → select → register
- WebSocket event handling
- Navigation state management

### E2E Tests (Playwright)
- Browse tournaments with filters
- View tournament detail
- Register for tournament
- See live bracket updates

## Dependencies

### Existing Components (reuse)
- `parlameme.ui.spec.tournament/bracket-view`
- `parlameme.ui.spec.tournament/standings-table`
- `parlameme.ui.spec.tournament/match-card`
- `parlameme.ui.spec.tournament/tournament-header`
- `parlameme.ui/stat-card`
- `parlameme.ui/keyword->label`

### Backend Endpoints (existing)
- `GET /api/tournaments` - list with filters
- `GET /api/tournaments/:id` - detail
- `POST /api/tournaments/:id/register` - registration

### WebSocket Messages (existing)
- `:tournament/create`, `:tournament/register`, `:tournament/start`
- Need to add: `:tournament/subscribe`, `:tournament/list-update`

## Open Questions

1. **Pagination vs infinite scroll?** - Recommend infinite scroll for mobile-friendly UX
2. **Cache strategy?** - Cache list for 30s, detail for 10s, invalidate on WS update
3. **Offline support?** - Not for v1, could add later with service worker
