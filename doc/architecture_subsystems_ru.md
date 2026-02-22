# Архитектура подсистем Parlameme

**Дата:** 2 февраля 2026 г.  
**Версия:** Flow v3  
**Статус:** Production-ready с оговорками

---

## Обзор

Parlameme — это игровой движок для социальных игр с переговорами (Parliament of Fools, Mafia, Werewolf и др.). Архитектура построена на принципах:

- **Data-driven DSL** — игры описываются как чистые данные
- **Archive-first** — архивы (seed + decisions) являются источником истины
- **Deterministic replay** — любую игру можно воспроизвести из архива
- **Unified Flow** — игры и турниры построены на общем фундаменте

```
┌─────────────────────────────────────────────────────────────┐
│                     КЛИЕНТЫ                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │  Web UI  │  │ Riemann  │  │ History  │  │ AI Agent │    │
│  │ (re-frame)│ │(мониторинг)│ │(история) │  │  (MCP)   │    │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘    │
│       │             │             │             │           │
│       └─────────────┼─────────────┼─────────────┘           │
│                     ▼                                       │
├─────────────────────────────────────────────────────────────┤
│                     СЕРВЕР                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │   HTTP   │  │WebSocket │  │   MCP    │  │ Tournament│   │
│  │  (API)   │  │ (Sente)  │  │(AI tools)│  │   API    │    │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘    │
│       └─────────────┼─────────────┼─────────────┘           │
│                     ▼                                       │
│              ┌────────────────────────┐                     │
│              │    Unified Flow        │                     │
│              │  (games + tournaments) │                     │
│              └────────────┬───────────┘                     │
│         ┌─────────────────┼─────────────────┐               │
│         ▼                 ▼                 ▼               │
│    ┌─────────┐      ┌──────────┐      ┌─────────┐          │
│    │ Archive │      │  Ledger  │      │Sessions │          │
│    │  Store  │      │  + Stats │      │  Store  │          │
│    └─────────┘      └──────────┘      └─────────┘          │
└─────────────────────────────────────────────────────────────┘
```

---

## 1. V3 DSL & Compiler

**Расположение:** `src/cljc/parlameme/v3/`

### Назначение

Декларативный DSL для описания игр как чистых данных. Игра компилируется в исполняемую структуру без потери декларативности.

### Ключевые концепции

```clojure
;; Пример игры
(-> (game :parliament "Parliament of Fools" {:players {:min 3 :max 12}})
    (resource :caps {:initial 100 :visibility :private})
    (resource :votes {:initial 1 :visibility :public})
    (deal :bribe {:parties {:proposer {:filter '(active?)}
                            :responder {:filter '(active?)}}
                  :stakes {:proposer [[:caps :amount]]}
                  :outcomes {:accept {:effects [[:transfer-stakes :responder]]}
                             :reject {:effects [[:return-stakes]]}}})
    (phase :floor {:allows [:bribe :handshake]})
    (victory :richest {:type :highest :resource :caps}))
```

### Компоненты

| Файл | Назначение |
|------|------------|
| `dsl.cljc` | Макросы и функции DSL: `game`, `resource`, `deal`, `vote`, `phase`, `victory` |
| `schema.cljc` | Malli-схемы для валидации всех конструкций |
| `compiler/core.cljc` | 10-фазная компиляция с валидацией |

### 10 фаз компиляции

1. **parse** — разбор DSL в AST
2. **validate-structure** — проверка структуры
3. **resolve-refs** — разрешение ссылок между элементами
4. **validate-refs** — проверка корректности ссылок
5. **expand-macros** — раскрытие макросов
6. **validate-semantics** — семантическая валидация
7. **optimize** — оптимизация
8. **generate-schemas** — генерация runtime-схем
9. **generate-tools** — генерация MCP tools
10. **finalize** — финальная сборка

### Сильные стороны

- Чистое разделение данных и поведения
- Композабельность через threading macros
- Полная валидация на этапе компиляции
- Генерация MCP tools из DSL

### Известные ограничения

- Выражения не поддерживают вложенные `quote` — используй `[:actor :team]` вместо `'(:team actor)`

---

## 2. V3 Runtime

**Расположение:** `src/cljc/parlameme/v3/runtime/`

### Назначение

Исполнение скомпилированных игр. Обрабатывает deals, votes, фазы, победные условия.

### Архитектура

```
                    ┌─────────────────┐
                    │   Compiled      │
                    │     Game        │
                    └────────┬────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                      Runtime                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │  State   │  │ Effects  │  │   Expr   │  │  Core    │    │
│  │(entities)│  │(execute) │  │  (eval)  │  │(orchestr)│    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### Ключевые файлы

| Файл | Назначение |
|------|------------|
| `core.cljc` | Оркестрация: `start-game`, `start-deal`, `respond-to-deal`, `advance-phase` |
| `state.cljc` | Управление состоянием: entities, resources, groups |
| `effects.cljc` | Исполнение эффектов: `[:transfer]`, `[:eliminate]`, `[:boost]` |
| `expr.cljc` | Вычисление выражений в контексте состояния |

### Эффекты

Эффекты — это векторы, описывающие изменения состояния:

```clojure
[:transfer :alice :bob :caps 10]      ; Передать 10 caps от alice к bob
[:boost :alice :votes 2]              ; Увеличить votes у alice на 2
[:eliminate :bob]                     ; Исключить bob из игры
[:set :alice :caps 50]                ; Установить caps у alice в 50
[:transfer-stakes :responder]         ; Передать залог респонденту
[:return-stakes]                      ; Вернуть залог пропоненту
```

### Stakes (залоги)

Ресурсы блокируются при создании deal и разблокируются при ответе:

```clojure
:stakes {:proposer [[:caps :amount]]}  ; Заблокировать :amount caps у proposer
:outcomes {:accept {:effects [[:transfer-stakes :responder]]}  ; При accept — передать
           :reject {:effects [[:return-stakes]]}}              ; При reject — вернуть
```

### Multilateral Deals

Поддерживаются сделки с несколькими участниками:

```clojure
(deal :coalition
  {:parties {:leader {:count 1}
             :members {:count [2 5]}}  ; От 2 до 5 участников
   :completion {:rule :threshold :value 0.6}  ; 60% должны ответить
   :outcomes {...}})
```

Правила завершения: `:all`, `:threshold`, `:majority`, `:any-reject`

### Сильные стороны

- Чистые функции, нет side effects
- Детерминистичное исполнение для replay
- Расширяемость через multimethods

---

## 3. Sessions

**Расположение:** `src/clj/parlameme/v3/sessions.clj`

### Назначение

Управление активными игровыми сессиями. Связывает runtime с WebSocket и persistence.

### Состояние

```clojure
;; Атом с активными сессиями
{:sessions
 {"session-id" {:flow-id "session-id"
                :game-id :parliament
                :game-version "abc123"
                :runtime <runtime-state>
                :players [:alice :bob :carol]
                :host :alice
                :status :in-progress
                :created-at #inst "2026-02-02T..."
                :started-at #inst "2026-02-02T..."}}}
```

### Ключевые операции

```clojure
;; Создание сессии
(create-session! :parliament "my-game" :host :alice)

;; Присоединение игрока
(join-session! "my-game" :bob)

;; Старт игры
(start-session! "my-game")

;; Действие игрока
(player-action! "my-game" :alice :bribe {:responder :bob :amount 10})

;; Завершение
(complete-session! "my-game" :reason :victory)
```

### Membership Protocol

**Правило "One Root Flow":** Игрок может быть только в одной активной сессии верхнего уровня.

```clojure
;; При попытке присоединиться ко второй игре:
{:ok? false
 :error {:code :already-in-game
         :message "Player already in active game"
         :current-session "other-game"}}
```

Исключение: дочерние flows (матчи турнира) не блокируют участие в родительском flow.

### CAS-паттерн

Все изменения состояния используют `compare-and-set!` для атомарности:

```clojure
(defn- update-session! [session-id f]
  (loop []
    (let [old-state @state
          new-state (update-in old-state [:sessions session-id] f)]
      (if (compare-and-set! state old-state new-state)
        (get-in new-state [:sessions session-id])
        (recur)))))
```

### Персистентность

Двухуровневая персистентность для полного восстановления:

**1. Метаданные сессий** (`sessions/store.clj`):
```clojure
;; data/sessions.edn - минимальные метаданные для refund decisions
{:sessions {"game-123" {:game-type :parliament
                        :players #{:alice :bob}
                        :escrow-locked? true
                        :stakes {:alice 100 :bob 100}
                        :last-activity 1706889600000}}}
```

**2. Hash-chain событий** (`flow/log.clj`):
```clojure
;; data/flows/active/{session-id}.transit - полный лог для replay
{:v 4 :seq 0 :type :meta :data {:flow-type :game :players [:alice :bob] :seed 12345} ...}
{:v 4 :seq 1 :type :event :data {:event/type :game/deal-started ...} :prev "abc..." :hash "def..."}
```

**Восстановление при рестарте** (`v3/sessions.clj:init!`):
1. Загружает stale sessions из `sessions/store`
2. Рефандит escrow для stale sessions (>1 час без активности)
3. Восстанавливает fresh flows через `flow/recovery.clj:recover-game`
4. Replay событий воссоздаёт полный runtime

### Сильные стороны

- Атомарные операции через CAS
- Защита от race conditions
- Полное восстановление через replay
- Автоматический refund при stale sessions

### Известные ограничения

- При высокой конкурентности CAS может создавать contention
- Stale sessions (>1 час) удаляются с refund, а не восстанавливаются

---

## 4. Archive

**Расположение:** `src/cljc/parlameme/archive.cljc`, `src/clj/parlameme/flow/archive.clj`

### Назначение

Хранение завершённых игр в формате, пригодном для детерминистичного воспроизведения.

### Философия Archive-First

```
┌─────────────────────────────────────────────────────────────┐
│                    ARCHIVE-FIRST                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  МИНИМАЛЬНЫЙ АРХИВ (~1-2KB на игру):                        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ {:version 1                                          │   │
│  │  :rules-hash "sha256-abc..."                         │   │
│  │  :seed 12345                                         │   │
│  │  :players [:alice :bob]                              │   │
│  │  :decisions [                                        │   │
│  │    [:deal :alice :bribe :bob {:amount 10}]           │   │
│  │    [:respond :bob "deal-0" :accept]]}                │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│                          ▼                                  │
│                 ДЕТЕРМИНИСТИЧНЫЙ REPLAY                     │
│    (replay archive compiled-game) → идентичное состояние    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Структура архива

```clojure
{:flow-id "game-123"
 :flow-type :game
 :game-id :parliament
 :rules-hash "sha256-..."        ; Хеш скомпилированной игры
 :seed 12345                     ; Seed для RNG
 :players [:alice :bob :carol]
 :decisions [...]                ; Все решения игроков
 :winner :alice                  ; Победитель (если есть)
 :completed-at #inst "2026-..."
 :duration-ms 45000}
```

### TTL-кеш

Для производительности используется кеш с TTL 5 секунд:

```clojure
(defonce ^:private cache (atom nil))
(def ^:private cache-ttl-ms 5000)

(defn- cache-valid? []
  (when-let [{:keys [loaded-at]} @cache]
    (< (- (System/currentTimeMillis) loaded-at) cache-ttl-ms)))

(defn- load-all-entries []
  (if (cache-valid?)
    (:entries @cache)
    (let [entries (load-all-entries-from-disk)]
      (reset! cache {:entries entries :loaded-at (System/currentTimeMillis)})
      entries)))
```

Кеш инвалидируется при `notify-flow-completed!`.

### API

```clojure
;; Сохранение архива
(save-archive! archive)

;; Получение архива
(get-archive "game-123")

;; Список архивов с фильтрацией
(list-archives {:game-type :parliament :limit 10})

;; История игрока
(player-history :alice {:limit 20})

;; Replay
(replay archive compiled-game)  ; => итоговое состояние
```

### Детерминистичный RNG

Для воспроизводимости используется seed-based RNG:

```clojure
;; parlameme.rng
(defn create-rng [seed] ...)
(defn next-int [rng max] ...)   ; Возвращает [value new-rng]
(defn shuffle [rng coll] ...)   ; Детерминистичный shuffle
```

### Сильные стороны

- Компактный формат (~1-2KB на игру)
- Полная воспроизводимость
- Подходит для blockchain-анкоринга

### Известные ограничения

- `list-archives` читает все файлы — O(n)
- При большом количестве игр нужна индексация

---

## 5. Ledger

**Расположение:** `src/cljc/parlameme/ledger/`

### Назначение

Учёт балансов игроков с hash-chain для аудита.

### Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│                      LEDGER                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                   Public API                          │  │
│  │  credit, debit, transfer, balance, history            │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                  │
│                          ▼                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                   Hash Chain                          │  │
│  │  entry₀ ──hash──► entry₁ ──hash──► entry₂ ──► ...    │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                  │
│                          ▼                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                   Atom Store                          │  │
│  │  {:balances {...} :entries [...] :merkle-root "..."}  │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Структура записи

```clojure
{:id "entry-123"
 :type :credit           ; :credit, :debit, :transfer
 :account :alice
 :amount 100
 :currency :caps
 :reason "game-reward"
 :ref "game-456"         ; Ссылка на источник
 :timestamp #inst "..."
 :prev-hash "sha256-..." ; Хеш предыдущей записи
 :hash "sha256-..."}     ; Хеш текущей записи
```

### API

```clojure
;; Пополнение
(ledger/credit :alice 100 :caps {:reason "deposit"})

;; Списание
(ledger/debit :alice 50 :caps {:reason "bet"})

;; Перевод
(ledger/transfer :alice :bob 25 :caps {:reason "deal"})

;; Баланс
(ledger/balance :alice :caps)  ; => 75

;; История
(ledger/history :alice {:limit 10})
```

### Hash Chain

Каждая запись содержит хеш предыдущей:

```clojure
(defn- compute-hash [entry prev-hash]
  (let [data (pr-str (assoc entry :prev-hash prev-hash))]
    (sha256 data)))
```

### Merkle Tree

Для эффективной верификации строится Merkle tree:

```clojure
;; parlameme.ledger.merkle
(defn build-tree [entries] ...)
(defn root-hash [tree] ...)
(defn proof [tree entry-id] ...)
(defn verify [proof root-hash entry] ...)
```

### Персистентность

```clojure
;; data/ledger.edn
{:balances {:alice {:caps 100 :votes 5}
            :bob {:caps 75 :votes 3}}
 :entries [...]
 :merkle-root "sha256-..."}
```

### Сильные стороны

- Аудируемость через hash-chain
- Merkle proofs для верификации
- Простой API

### Известные ограничения

- **Нет атомарных транзакций** между аккаунтами
- `transfer` = `debit` + `credit` — не атомарно
- При сбое между операциями возможна inconsistency
- Нет блокировок для concurrent access

---

## 6. Spaces

**Расположение:** `src/clj/parlameme/spaces/core.clj`

### Назначение

Пространства — контексты видимости и доступа для игр и турниров.

### Типы пространств

| Тип | Доступ | Создание игр |
|-----|--------|--------------|
| `:public` | Все | Все |
| `:private` | Только члены | Члены |
| `:system` | Только члены | Только система (для турниров) |

### API

```clojure
;; Проверка доступа
(spaces/can-access? :alice :elite-club)
(spaces/member? :alice :elite-club)
(spaces/can-create-game? :alice :elite-club)

;; Пространства агента
(spaces/agent-spaces :alice)
(spaces/agent-space-ids :alice)

;; Создание приватного пространства
(spaces/create-space! {:name "My Club" :type :private :owner :alice})

;; Управление членством
(spaces/add-member! :elite-club :bob)
(spaces/remove-member! :elite-club :bob)
(spaces/promote-to-admin! :elite-club :bob)
```

### Персистентность

Пространства и членства сохраняются в `data/spaces.edn`:

```clojure
{:spaces {:public {...}
          :elite-club-abc123 {:space/id :elite-club-abc123
                              :space/name "Elite Club"
                              :space/type :private
                              :space/owner :alice
                              :space/members #{:alice :bob}
                              :space/admins #{:alice}
                              ...}}
 :memberships {:alice #{:elite-club-abc123}
               :bob #{:elite-club-abc123}}
 :saved-at 1706889600000}
```

- Debounced сохранение (2 секунды)
- Загрузка при `init!`
- Flush при `shutdown!`

### Сильные стороны

- Простая модель доступа
- Интеграция с играми и турнирами
- Персистентность между рестартами

### Известные ограничения

- Нет иерархии пространств
- Нет ролей кроме owner/admin/member

---

## 7. Tournament

**Расположение:** `src/cljc/parlameme/tournament/`, `src/clj/parlameme/tournament/`

### Назначение

Проведение турниров с различными форматами: round-robin, single-elimination, swiss.

### Жизненный цикл

```
┌─────────────┐  register   ┌─────────────┐  start    ┌─────────────┐
│ REGISTRATION│ ───────────►│ IN_PROGRESS │ ────────► │  COMPLETED  │
│             │             │             │           │             │
│ участники   │             │   матчи     │           │  победитель │
│ регистрируются            │   играются  │           │  standings  │
└─────────────┘             └─────────────┘           └─────────────┘
```

### Форматы турниров

| Формат | Описание |
|--------|----------|
| `:round-robin` | Каждый играет с каждым |
| `:single-elimination` | Выбывание после поражения |
| `:swiss` | Swiss-система пар |
| `:groups-and-knockout` | Групповой этап + плей-офф |

### Связь с Flow

Турниры используют тот же Flow foundation:

```
┌─────────────────────────────────────────────────────────────┐
│                     flow/ (Foundation)                      │
│  state.cljc · effects.cljc · expr.cljc · events.cljc       │
└─────────────────────────────────────────────────────────────┘
        ▲                                    ▲
        │ extends                            │ extends
┌───────┴───────────┐              ┌─────────┴─────────┐
│  v3/runtime/      │              │  tournament/      │
│  Game layer       │              │  Tournament layer │
└───────────────────┘              └───────────────────┘
```

### Матчи как дочерние flows

```clojure
;; Турнир создаёт матчи как дочерние flows
{:flow-id "cup-match-0"
 :flow-type :game
 :parent-flow "my-cup"           ; Ссылка на родительский турнир
 :parent-match-id :round-1-match-0
 :players [:alice :bob]}
```

### API

```clojure
;; Создание турнира
(t-sessions/create-tournament! :round-robin "my-cup"
                                :host :host-id
                                :config {:name "My Cup" :max-participants 8})

;; Регистрация
(t-sessions/register-participant! "my-cup" :alice)

;; Старт
(t-sessions/start-tournament! "my-cup")

;; Результат матча
(t-sessions/report-match-result! "my-cup" :match-0 :alice
                                  :scores {:alice 3 :bob 1})
```

### HTTP API

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/tournaments` | Список турниров |
| GET | `/api/tournaments/:id` | Детали турнира |
| POST | `/api/tournaments` | Создание турнира |
| POST | `/api/tournaments/:id/register` | Регистрация |
| POST | `/api/tournaments/:id/start` | Старт |
| POST | `/api/tournaments/:id/results` | Результат матча |

### WebSocket события

```clojure
;; Клиент → Сервер
:tournament/create
:tournament/register
:tournament/start
:tournament/report-result

;; Сервер → Клиент
:tournament/state
:tournament/started
:tournament/match-ready
:tournament/completed
```

### Сильные стороны

- Унификация с игровым Flow
- Поддержка разных форматов
- Интеграция с MCP для AI-участников

### Персистентность

Турниры персистентны через `flow/log`:
- События записываются в `data/flows/active/{tournament-id}.transit`
- При рестарте: `tournament/sessions.clj:init!` → `recover-tournaments!`
- Восстанавливается полное состояние через replay событий

### Известные ограничения

- Stale турниры (>2 часов без активности) удаляются при рестарте
- Нет отдельного `tournament-store.clj` для метаданных (используется только flow/log)

---

## 8. MCP (Model Context Protocol)

**Расположение:** `src/clj/parlameme/mcp/`, `src/cljc/parlameme/mcp/`

### Назначение

Интеграция AI-агентов через стандартный MCP протокол.

### Stateful State Machine

```
LOBBY ──(activate_game)──► IN_GAME ──(leave_game)──► LOBBY
  │                            │
  │ Platform tools:            │ Game-specific tools:
  │ - list_available_games     │ - {game}/bribe
  │ - list_open_sessions       │ - {game}/vote_bill
  │ - my_pending_invites       │ - {game}/respond
  │ - activate_game            │ - {game}/get_status
  │ - my_status                │ - {game}/get_history
  └────────────────────────────┴─────────────────────
```

### Token-based Authentication

```clojure
;; Создание invite-токена
(mcp-tokens/create-invite :session-id "game-123"
                          :player-id :bob
                          :host-id :alice)
;; => "hmac-signed-token..."

;; Валидация токена
(mcp-tokens/validate-invite "hmac-signed-token...")
;; => {:session-id "game-123" :player-id :bob :host-id :alice}
```

### Динамическая генерация tools

Tools генерируются из скомпилированной игры:

```clojure
;; parlameme.mcp.schema
(defn generate-tools [compiled-game phase]
  ;; Возвращает MCP tools для текущей фазы
  [{:name "parliament/bribe"
    :description "Propose a bribe deal"
    :inputSchema {:type "object"
                  :properties {:responder {:type "string"}
                               :amount {:type "integer"}}
                  :required ["responder" "amount"]}}
   ...])
```

### Состояние агента

```clojure
;; parlameme.mcp.state
{:agent-id {:state :in-game        ; :lobby или :in-game
            :session-id "game-123"
            :player-id :bob
            :connected-at #inst "..."}}
```

### Tool execution

```clojure
;; parlameme.mcp.server
(defn execute-tool [agent-id tool-name params]
  (let [agent-state (get-agent-state agent-id)]
    (case (:state agent-state)
      :lobby (execute-lobby-tool tool-name params)
      :in-game (execute-game-tool agent-state tool-name params))))
```

### HTTP endpoints

```
POST /mcp/agent/:agent-id
  Content-Type: application/json
  Body: {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
```

### Персистентность

**MCP agent state НЕ персистится** — это **by design**:
- Агенты эфемерны — подключаются по HTTP
- При рестарте сервера агенты переподключаются
- Состояние (lobby/in-game) восстанавливается через `activate_game` с токеном
- Игровая сессия восстанавливается из `flow/log` (см. Sessions)

### Сильные стороны

- Стандартный MCP протокол
- Динамические tools из DSL
- State machine предотвращает invalid actions
- Stateless reconnect — агент просто переподключается

### Известные ограничения

- Token без ротации и expiry
- Нет rate limiting
- Один endpoint на агента (нет WebSocket MCP)

---

## 9. Escrow

**Расположение:** `src/clj/parlameme/escrow/`

### Назначение

Интеграция с Base L2 для эскроу ставок с криптовалютой.

### Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│                      ESCROW                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                   Protocol                            │  │
│  │  IEscrow: lock, release, refund, balance              │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                  │
│          ┌───────────────┼───────────────┐                  │
│          ▼               ▼               ▼                  │
│     ┌─────────┐    ┌──────────┐    ┌──────────┐            │
│     │  Mock   │    │  Base L2 │    │  Future  │            │
│     │ (test)  │    │ (prod)   │    │  chains  │            │
│     └─────────┘    └──────────┘    └──────────┘            │
└─────────────────────────────────────────────────────────────┘
```

### Protocol

```clojure
(defprotocol IEscrow
  (lock [this session-id player-id amount]
    "Lock funds for a game session")
  (release [this session-id winner-id]
    "Release funds to winner")
  (refund [this session-id]
    "Refund all participants")
  (balance [this session-id]
    "Get escrow balance for session"))
```

### Mock Implementation

Для тестирования без blockchain:

```clojure
(defrecord MockEscrow [state]
  IEscrow
  (lock [_ session-id player-id amount]
    (swap! state update-in [session-id :locked player-id] (fnil + 0) amount)
    {:ok? true :tx-hash (str "mock-" (random-uuid))})
  ...)
```

### Base L2 Implementation

```clojure
;; parlameme.escrow.base
(defrecord BaseEscrow [contract-address rpc-url private-key]
  IEscrow
  (lock [this session-id player-id amount]
    ;; Call smart contract
    (eth/send-tx {:to contract-address
                  :data (encode-lock session-id player-id amount)}))
  ...)
```

### HTTP API

| Метод | Путь | Назначение |
|-------|------|------------|
| POST | `/api/escrow/lock` | Заблокировать средства |
| POST | `/api/escrow/release` | Выплатить победителю |
| POST | `/api/escrow/refund` | Вернуть всем участникам |
| GET | `/api/escrow/:session-id` | Баланс эскроу |

### MCP Tools

```clojure
;; Доступны агентам в IN_GAME состоянии
"escrow/lock" - Lock funds
"escrow/status" - Check escrow status
```

### Сильные стороны

- Protocol abstraction позволяет легко менять backend
- Mock для разработки и тестов
- Интеграция с MCP

### Известные ограничения

- Base L2 реализация — "чёрный ящик", нужен аудит
- Нет retry logic для failed transactions
- Gas estimation hardcoded

---

## 10. Stats

**Расположение:** `src/clj/parlameme/stats/core.clj`

### Назначение

Статистика платформы и игроков, вычисляемая из архивов.

### API

```clojure
;; Статистика платформы
(stats/platform-stats)
;; => {:total-games 150
;;     :total-players 45
;;     :games-by-type {:parliament 80 :mafia 50 :werewolf 20}
;;     :avg-game-duration-ms 42000}

;; Лидерборд
(stats/leaderboard :limit 10)
;; => [{:player :alice :games 25 :wins 15 :win-rate 0.6}
;;     {:player :bob :games 20 :wins 10 :win-rate 0.5}
;;     ...]

;; Лидерборд по типу игры
(stats/leaderboard :game-type :mafia :limit 10)

;; Статистика игрока
(stats/player-stats :alice)
;; => {:games-played 25
;;     :wins 15
;;     :by-game-type {:parliament {:games 15 :wins 10}
;;                    :mafia {:games 10 :wins 5}}}
```

### Кеширование

Stats используют тот же TTL-кеш, что и Archive:

```clojure
;; При запросе stats, если кеш валиден — используем его
;; При notify-flow-completed! — кеш инвалидируется
```

### HTTP API

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/stats` | Статистика платформы |
| GET | `/api/leaderboard` | Лидерборд (с фильтрами) |
| GET | `/api/players/:id/stats` | Статистика игрока |

### Сильные стороны

- Вычисляется из архивов — всегда консистентно
- Простой API
- Фильтрация по типу игры

### Известные ограничения

- При большом количестве архивов — медленно
- Нет предвычисленных агрегатов

---

## 11. WebSocket (Sente)

**Расположение:** `src/clj/parlameme/v3/sente.clj`

### Назначение

Real-time коммуникация между клиентами и сервером.

### События

```clojure
;; Клиент → Сервер
:game/create {:game-id :parliament :session-id "my-game"}
:game/join {:session-id "my-game"}
:game/start {:session-id "my-game"}
:game/action {:session-id "my-game" :action :bribe :params {...}}

;; Сервер → Клиент
:game/state <full-state>
:game/update <delta>
:game/error {:code :invalid-action :message "..."}
:game/ended {:winner :alice}
```

### Broadcast

```clojure
;; Отправить всем участникам сессии
(sente/broadcast! session-id :game/update {:phase :voting})

;; Отправить конкретному игроку (private info)
(sente/send! player-id :game/private {:hand [...]})
```

### Сильные стороны

- Автоматический reconnect
- Fallback на long-polling
- Интеграция с Ring middleware

---

## Заключение

### Общая оценка: Production-Ready с оговорками

**Сильные стороны архитектуры:**
- Чистый data-driven подход
- Детерминистичность и воспроизводимость
- Расширяемость через protocols и multimethods
- Хорошее разделение ответственности

**Требуют внимания:**
1. **Ledger** — нужны атомарные транзакции между аккаунтами
2. **Archive** — нужна индексация при росте (сейчас O(n) для list-archives)
3. **MCP tokens** — нужен expiry и ротация

**Рекомендации по масштабированию:**
- При >10K игр — добавить индексацию архивов (SQLite или Datomic)
- При >100 concurrent сессий — рассмотреть шардирование по session-id
- При production escrow — аудит Base L2 контракта
- При высокой конкурентности — профилировать CAS contention
