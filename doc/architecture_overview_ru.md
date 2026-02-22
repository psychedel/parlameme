# Parlameme: Архитектурный обзор для разработчиков

Это руководство поможет новому разработчику понять как устроена система Parlameme — data-driven движок для стратегических игр на основе теории игр.

---

## Что это такое?

Parlameme — это платформа для запуска многопользовательских игр, где:
- Игры определяются как **чистые данные** (не код)
- AI-агенты могут играть наравне с людьми через **MCP протокол**
- Все действия записываются в **детерминированные архивы** для воспроизведения
- Игроки могут ставить реальные **USDC через escrow**

Система построена на Clojure/ClojureScript с функциональной архитектурой.

---

## Высокоуровневая картина

```
┌─────────────────────────────────────────────────────────────┐
│                    БРАУЗЕР (ClojureScript)                  │
│  ┌──────────────┐  ┌───────────┐  ┌────────────────┐       │
│  │ Game UI      │  │ Riemann   │  │ History        │       │
│  │ (re-frame)   │  │ Мониторинг│  │ Просмотр игр   │       │
│  └──────────────┘  └───────────┘  └────────────────┘       │
│                           │                                 │
│                    WebSocket (Sente)                        │
└───────────────────────────┼─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                     HTTP СЕРВЕР                             │
│  /chsk        → WebSocket для браузеров                     │
│  /mcp/agent/* → MCP для AI-агентов                         │
│  /api/*       → REST API                                    │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                    v3 RUNTIME                               │
│  Компилятор → Состояние игры → Эффекты → История           │
└───────────────────────────┬─────────────────────────────────┘
                            │
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
    ┌─────────┐       ┌──────────┐       ┌──────────┐
    │ Archive │       │  Ledger  │       │  Escrow  │
    │   EDN   │       │ Балансы  │       │ USDC L2  │
    └─────────┘       └──────────┘       └──────────┘
```

---

## Три слоя кода

Код разделён на три директории по месту выполнения:

| Директория | Где работает | Назначение |
|------------|--------------|------------|
| `src/cljc/` | Везде | Общая логика (DSL, компилятор, runtime) |
| `src/clj/` | Только сервер | HTTP, WebSocket, персистентность |
| `src/cljs/` | Только браузер | React UI, re-frame |

**Ключевой момент:** Вся игровая логика живёт в `src/cljc/` и может выполняться как на сервере, так и в браузере. Это позволяет делать оптимистичные обновления UI.

---

## Ядро системы: Unified Flow Architecture

### Flow как универсальная абстракция

Система построена на **унифицированной архитектуре Flow**. И игры, и турниры — это Flow с разными типами:

```
┌─────────────────────────────────────────────────────────────┐
│                     flow/ (Фундамент)                       │
│  state.cljc · effects.cljc · expr.cljc · events.cljc       │
└─────────────────────────────────────────────────────────────┘
        ▲                                    ▲
        │ расширяет                          │ расширяет
┌───────┴───────────┐              ┌─────────┴─────────┐
│  v3/runtime/      │              │  tournament/      │
│  Игровой слой     │              │  Турнирный слой   │
│  (сделки, голоса) │              │  (матчи, сетки)   │
└───────────────────┘              └───────────────────┘
```

**Ключевые принципы:**
- Всё является Flow с `:flow/type` (`:game` или `:tournament`)
- `v3/runtime` расширяет `flow/` игровыми эффектами через multimethod
- Сущности используют ключ `:active` (единообразно для всех типов)
- Турнирные матчи порождаются как дочерние flow с ссылкой на родителя

**Файлы фундамента:**
- `src/cljc/parlameme/flow/state.cljc` — сущности, ресурсы, группы
- `src/cljc/parlameme/flow/effects.cljc` — базовые эффекты (multimethod)
- `src/cljc/parlameme/flow/expr.cljc` — вычисление выражений
- `src/cljc/parlameme/flow/events.cljc` — event sourcing

### Как определяются игры

Игры описываются через **Flow v3 DSL** — специальный язык на основе данных. Никакого императивного кода, только декларации:

```clojure
(-> (game :parliament "Parliament of Fools"
          {:players {:min 5 :max 15}})
    
    ;; Ресурсы — то, чем владеют игроки
    (resource :wealth {:initial 1000 :visibility :private})
    (resource :reputation {:initial 50 :visibility :public})
    
    ;; Сделки — структурированные переговоры
    (deal :bribe
          {:parties {:proposer {} :responder {}}
           :params {:amount {:type :number :min 50}}
           :stakes {:proposer [[:wealth :amount]]}
           :outcomes
           {:accept {:effects [[:transfer-stakes :responder]]}
            :reject {:effects [[:return-stakes]]}}})
    
    ;; Фазы — порядок игры
    (phase :floor {:allows [:bribe]})
    (phase :vote {:allows [:bill-vote]})
    
    ;; Условия победы
    (victory :wealth-domination
             {:when '(>= [:actor :wealth] 5000)}))
```

**Файл:** `src/cljc/parlameme/v3/dsl.cljc`

### Компиляция

Определение игры проходит через **10-фазный компилятор**:

1. **collect** — собрать все ресурсы, сделки, фазы
2. **validate** — проверить схемы через Malli
3. **resolve** — связать ссылки между компонентами
4. **expand** — раскрыть шаблоны
5. **analyze** — семантический анализ
6. **optimize** — оптимизации (сворачивание констант)
7. **conflicts** — найти конфликты правил
8. **indices** — построить lookup-таблицы для быстрого доступа
9. **generate** — сгенерировать UI-спецификации
10. **emit** — выдать финальный результат

**Файл:** `src/cljc/parlameme/v3/compiler/core.cljc`

На выходе — оптимизированная структура с индексами, готовая к исполнению.

### Runtime — исполнение игры

Runtime — это чистые функции, которые принимают состояние и возвращают новое:

```clojure
;; Загрузить скомпилированную игру
(def rt (runtime/load-game compiled-game))

;; Начать игру с игроками
(def rt2 (runtime/start-game rt [:alice :bob :carol]))

;; Предложить сделку
(def result (runtime/start-deal rt2 :bribe 
              {:proposer :alice :responder :bob :params {:amount 100}}))

;; Результат всегда имеет форму:
;; {:ok? true :runtime новое-состояние}
;; {:ok? false :error {:code :ошибка :details {...}}}
```

**Ключевое свойство:** Все функции **чистые** — они не изменяют исходное состояние, а возвращают новое. Это делает систему предсказуемой и легко тестируемой.

**Файл:** `src/cljc/parlameme/v3/runtime/core.cljc`

### Система эффектов

Эффекты — это маленькие команды, которые изменяют состояние игры:

```clojure
;; Примитивные эффекты
[:transfer :alice :bob :wealth 100]     ;; Перевод между игроками
[:boost :alice :reputation 10]          ;; Увеличить ресурс
[:damage :bob :wealth 50]               ;; Уменьшить ресурс
[:eliminate :carol]                     ;; Исключить из игры
[:broadcast "Alice wins!"]              ;; Объявление всем

;; Мета-эффекты для логики
[:when (> amount 100)                   ;; Условие
  [:damage :actor :reputation 5]]
  
[:each :player (alive?)                 ;; Цикл по игрокам
  [:boost :player :caps 10]]
```

**Файл:** `src/cljc/parlameme/v3/runtime/effects.cljc`

### Stakes — блокировка ресурсов

Когда игрок предлагает сделку, его ресурсы **блокируются** (stakes). Это гарантирует, что у него есть чем заплатить:

```clojure
:stakes {:proposer [[:wealth :amount]]}  ;; Блокируем wealth у предлагающего

:outcomes
{:accept {:effects [[:transfer-stakes :responder]]}  ;; При принятии — перевод
 :reject {:effects [[:return-stakes]]}}              ;; При отказе — возврат
```

---

## Турнирная система

Турниры — это Flow типа `:tournament`, которые порождают игры как дочерние Flow:

```clojure
;; Определение турнира
(-> (tournament :cup "World Cup" {:min-participants 4 :max-participants 16})
    (stage :group {:format :round-robin :groups 4})
    (stage :knockout {:format :single-elimination})
    (match-template {:game-id :parliament-arena :version "abc123"}))
```

**Жизненный цикл:**

```
REGISTRATION → IN_PROGRESS → COMPLETED
     ↓              ↓            ↓
  участники     матчи        победитель
  регистрируются порождаются   определён
```

**Поддерживаемые форматы:**
- `:round-robin` — каждый с каждым
- `:single-elimination` — выбывание после поражения
- `:swiss` — швейцарская система
- `:groups-and-knockout` — групповой этап + плей-офф

**Матчи как дочерние Flow:**
```clojure
{:flow/type :game
 :flow/parent {:tournament-id :world-cup
               :stage :group-a
               :round 1}
 :flow/instance-of {:flow-id :parliament-arena
                    :version "abc123"}}  ;; content-hash игры
```

**Файлы:**
- `src/cljc/parlameme/tournament/dsl.cljc` — турнирный DSL
- `src/cljc/parlameme/tournament/runtime.cljc` — runtime турниров
- `src/cljc/parlameme/tournament/effects.cljc` — эффекты (порождение матчей)

---

## Серверная часть

### HTTP-сервер

Единая точка входа — `src/clj/parlameme/server.clj`. Маршруты:

| Путь | Назначение |
|------|------------|
| `/chsk` | WebSocket для браузеров |
| `/mcp/agent/:id` | MCP для AI-агентов |
| `/api/v3/*` | REST API для игр |
| `/api/ledger/*` | API балансов |
| `/api/platform/*` | Статистика платформы |

### WebSocket (Sente)

Двусторонняя связь браузер ↔ сервер. Клиент отправляет:

```clojure
[:v3/join {:game-id :my-game :player-name "Alice"}]
[:v3/start-deal {:deal-id :bribe :responder :bob :params {:amount 100}}]
[:v3/respond-deal {:deal-instance-id "d1" :option :accept}]
```

Сервер отвечает всем участникам:

```clojure
[:v3/state {...}]           ;; Текущее состояние
[:v3/deal-started {...}]    ;; Новая сделка
[:v3/deal-resolved {...}]   ;; Сделка завершена
[:v3/win {:winner :alice}]  ;; Победа
```

**Файл:** `src/clj/parlameme/v3/sente.clj`

### MCP — протокол для AI-агентов

AI-агенты (Claude, GPT) подключаются через **Model Context Protocol**. Это HTTP-based протокол, где агент получает список доступных инструментов и вызывает их.

Агент проходит через **два состояния**:

```
LOBBY → (activate_game с токеном) → IN_GAME → (leave_game) → LOBBY
```

В лобби доступны:
- `list_available_games` — какие игры есть
- `my_pending_invites` — приглашения в игры
- `activate_game` — войти в игру по токену

В игре доступны инструменты конкретной игры:
- `parliament-arena/start_deal` — предложить сделку
- `parliament-arena/respond_deal` — ответить на сделку
- `parliament-arena/get_status` — получить состояние

**Файлы:** `src/clj/parlameme/mcp/stateful.clj`, `src/clj/parlameme/mcp/server.clj`

---

## Хранение данных: Archive-First

### Философия

Архивы (seed + decisions) — это **источник правды**, не базы данных.

```
┌─────────────────────────────────────────────────────────────┐
│                    ARCHIVE-FIRST PHILOSOPHY                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  MINIMAL ARCHIVE (~1-2KB per game):                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ {:version 1                                          │   │
│  │  :rules-hash "sha256-abc..."  ;; Хеш правил          │   │
│  │  :seed 12345                  ;; Seed для RNG        │   │
│  │  :players [:alice :bob]       ;; Игроки              │   │
│  │  :decisions [                 ;; Все решения         │   │
│  │    [:deal :alice :bribe :bob {:amount 10}]           │   │
│  │    [:respond :bob "deal-0" :accept]]}                │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│                          ▼                                  │
│                 DETERMINISTIC REPLAY                        │
│    (replay archive compiled-game) → identical final state   │
│                                                             │
│  Преимущества:                                              │
│  • Blockchain-friendly: ~400 bytes compressed               │
│  • Verifiable: replay даёт идентичное состояние             │
│  • Auditable: decisions — полный аудит-трейл                │
│  • Simple: не нужна БД для воспроизведения                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Детерминированный RNG

Для воспроизводимости используется детерминированный генератор случайных чисел:

```clojure
(require '[parlameme.rng :as rng])

;; Создать RNG с seed
(def rng (rng/create 12345))

;; Генерировать числа
(rng/next-int rng 100)  ; => [42, new-rng]
(rng/shuffle rng items) ; => [shuffled-items, new-rng]
```

**Файл:** `src/cljc/parlameme/rng.cljc`

### Replay из архива

```clojure
(require '[parlameme.archive :as archive])

;; Воспроизвести игру из архива
(archive/replay archive compiled-game)
;; => {:ok? true :runtime final-state}

;; Верифицировать архив
(archive/verify archive compiled-game expected-hash)
;; => {:ok? true :verified true}
```

**Файл:** `src/cljc/parlameme/archive.cljc`

### Персистентность

| Данные | Хранилище | Восстановление |
|--------|-----------|----------------|
| Балансы игроков | `data/ledger.edn` | Загрузка при старте |
| Архивы игр | `data/archives/*.edn` | Replay для любой игры |
| Метаданные сессий | `data/sessions.edn` | Возобновление активных |
| Кеш статистики | `data/stats.edn` | Пересчёт из архивов |

**Файлы:**
- `src/clj/parlameme/persistence/edn.clj` — утилиты для EDN
- `src/clj/parlameme/archive/store.clj` — хранение архивов
- `src/clj/parlameme/sessions/store.clj` — персистентность сессий
- `src/clj/parlameme/stats/core.clj` — статистика из архивов

### Ledger — off-chain балансы

Ledger хранит балансы игроков в виде **hash-chain**:

```
Deposit 1000 → Hash1 → Transfer 50 → Hash2 → Withdraw 500 → Hash3
```

Каждая запись подписана HMAC-SHA256, что делает историю неизменяемой.

**Файлы:** `src/clj/parlameme/ledger/core.clj`, `src/cljc/parlameme/ledger/chain.cljc`

### Escrow — реальные USDC

Для игр на реальные деньги используется **Base L2** (Coinbase Layer 2). Игроки депозитят USDC в смарт-контракт, играют, а потом выводят.

**Файл:** `src/clj/parlameme/escrow/base.clj`

---

## Клиентская часть

### Re-frame архитектура

Клиент построен на **re-frame** — предсказуемая архитектура для React:

```
События → Обработчики → Состояние (app-db) → Подписки → Компоненты
```

- `game/events.cljs` — обработчики событий
- `game/subs.cljs` — подписки на данные
- `game/views.cljs` — React-компоненты

### Три UI-приложения

1. **Game** (`/game.html`) — интерфейс игры
2. **Riemann** (`/riemann.html`) — мониторинг системы
3. **History** (`/history.html`) — просмотр истории игр

---

## Готовые игры

В системе 6 реализованных игр:

| Игра | Механика | Файл |
|------|----------|------|
| **Parliament of Fools** | Политическая экономика, коалиции | `games/parliament.cljc` |
| **Parliament Arena** | Упрощённая версия Parliament | `games/parliament_arena.cljc` |
| **Mafia** | Social deduction с Bayesian inference | `games/mafia.cljc` |
| **Werewolf** | Скрытые роли, ночные действия | `games/werewolf.cljc` |
| **Resistance** | Команды, миссии, предатели | `games/resistance.cljc` |
| **Auction** | Mechanism design, аукционы | `games/auction.cljc` |

---

## Паттерны кодирования

### Result Type

Все операции возвращают единообразный результат:

```clojure
{:ok? true :runtime новое-состояние}
{:ok? false :error {:code :not-enough-funds :details {...}}}
```

Обработка:
```clojure
(let [result (runtime/start-deal rt :bribe {...})]
  (if (:ok? result)
    (handle-success (:runtime result))
    (handle-error (:error result))))
```

### Threading Macros

Clojure-идиоматика для цепочки преобразований:

```clojure
(-> game
    (resource :wealth {...})
    (resource :reputation {...})
    (deal :bribe {...})
    (phase :floor {...})
    (compile!))
```

### Мультиметоды

Расширяемый dispatch по типу:

```clojure
(defmulti execute-effect (fn [state [effect-type & _]] effect-type))

(defmethod execute-effect :transfer [state [_ from to resource amount]]
  ...)

(defmethod execute-effect :boost [state [_ entity resource amount]]
  ...)
```

---

## С чего начать изучение

Рекомендуемый порядок файлов:

1. **`src/cljc/parlameme/v3/dsl.cljc`** — как определяются игры
2. **`src/cljc/parlameme/v3/games/parliament_arena.cljc`** — пример игры
3. **`src/cljc/parlameme/v3/runtime/core.cljc`** — как выполняются игры
4. **`src/cljc/parlameme/v3/runtime/effects.cljc`** — система эффектов
5. **`src/clj/parlameme/server.clj`** — HTTP-сервер
6. **`src/clj/parlameme/v3/sente.clj`** — WebSocket-обработчики
7. **`src/clj/parlameme/mcp/stateful.clj`** — MCP для AI

---

## Команды для разработки

```bash
# Проверить статус nREPL
nrepl status

# Запустить сервер
nrepl '(repl/go)'

# CLJS hot-reload
npx shadow-cljs watch game riemann

# Перезагрузить namespace после изменений
nrepl reload parlameme.v3.runtime.core

# Запустить тесты
GAME_TOKEN_SECRET=test-secret lein test
```

**URLs:**
- Игра: http://localhost:3000/game.html
- Мониторинг: http://localhost:3000/riemann.html
- История: http://localhost:3000/history.html

---

## Итого

Parlameme — это **функциональный движок** для стратегических игр:

- **Data-driven**: игры — это данные, не код
- **Pure functions**: вся логика предсказуема и тестируема
- **AI-ready**: MCP интеграция для Claude/GPT
- **Archive-first**: детерминированные архивы для воспроизведения
- **Real stakes**: Ledger + Escrow для USDC

Архитектура чётко разделена на слои, что позволяет работать с каждой частью независимо.
