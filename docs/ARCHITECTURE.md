# ARCHITECTURE.md — Sieshka Food Delivery Platform
# nano-vm Ecosystem Edition · v1.3 · 2026-06-17
# Supersedes: v1.2 (2026-06-16)
#
# BASIS: llm-nano-vm v0.8.6 · nano-vm-mcp v0.4.5 · nano-vm-dev-agent v0.2.0
#
# DELTA vs v1.2:
#   - Провайдеры: YandexGPT Pro (primary) / GigaChat (fallback) — nano-vm-experiment подтвердил
#     что provider-fallback реализуется через TOOL(attempt)→CONDITION($ok<1)→TOOL(switch), НЕ gateway
#   - ASTEngine constraint: string literals как RHS в CONDITION НЕ поддерживаются; числовой sentinel (0/1)
#   - contains не поддерживается в v0.8.6 — использовать ==
#   - LiteLLMAdapter: прямой импорт nano_vm.adapters.litellm_adapter.LiteLLMAdapter
#   - nano-vm-mcp SSE + stdio — оба транспорта доступны
#   - ExecutionReceipt.trace_hash: frozen-model pattern детектируется через hash comparison across runs
#   - NarrativeReceipt → M4+, НЕ раньше ExecutionReceipt stable (M3)
#   - GovernanceEnvelope: НЕ стабить в M1/M2 — cargo cult (подтверждено CONSTRAINTS.md)
#   - AbstractStore PG backend: решение в M3 planning, не раньше

---

## 1. Архитектурные границы (hard, без исключений)

### 1.1 Инвариант ядра

```
nano-vm = transition executor
         NOT entity lifecycle container

Business state owner  = PostgreSQL
Execution state owner = nano-vm Trace + StateContext
```

### 1.2 Правила мутации состояния

**ЗАПРЕЩЕНО:**
```python
order.status = "PAID"                          # прямая мутация
order.status = new_state                        # прямая мутация
fsm.set_state(order_id, "COOKING")              # прямой set
order_fsm.transition(order_id, new_state="COOKING")  # new_state= ЗАПРЕЩЁН
```

**ОБЯЗАТЕЛЬНО:**
```python
order_fsm.transition(order_id, event="PAYMENT_CONFIRMED")
# или типизированный:
order_fsm.handle_event(order_id, event=PaymentConfirmed(...))
```

**CI gate (M3):**
```bash
grep -r 'status = ' --include='*.py' app/ | grep -v '# terminal-tool'
# → zero results (только terminal tools имеют право писать статус)
```

### 1.3 Граница персистентности

| Слой | Таблицы |
|---|---|
| PostgreSQL | orders, kitchen_tickets, delivery_tasks, inventory, promotions, customers, payments |
| SQLite WAL (nano-vm) | execution_traces, idempotency_keys, state_contexts, transition_stats |

**Правило изоляции:**
- nano-vm обращается к бизнес-данным ТОЛЬКО через Governed Tools (terminal tool read/write)
- Бизнес-слой НЕ читает/пишет nano-vm таблицы напрямую
- Прямые SQL/ORM вызовы из nano-vm кода ЗАПРЕЩЕНЫ

---

## 2. ADR — Architecture Decision Records

### ADR-001 — Scope ExecutionVM

**Решение:** ExecutionVM выполняет атомарные transition programs. Один program = один бизнес-переход. Жизненный цикл сущности = несколько programs.

**Паттерн:**
```
Order.state = PAID, event = START_COOKING
    ↓
Program: [validate_payment, reserve_inventory, create_kitchen_ticket, write_order_state]
    ↓
Order.state = COOKING (записывает terminal tool атомарно в PG)
```

**Инварианты:**
- Каждый Program ОБЯЗАН иметь ровно один terminal tool
- Terminal tool атомарно пишет новый state в PostgreSQL внутри единой транзакции
- Внешние вызовы (HTTP, MQ, third-party API) ЗАПРЕЩЕНЫ внутри PG-транзакции terminal tool (длинные локи + partial failure)
- ExecutionVM не завершается успешно если terminal tool упал

### ADR-002 — Receipt Split

ExecutionReceipt и NarrativeReceipt — строго разделены.

| | ExecutionReceipt | NarrativeReceipt |
|---|---|---|
| Слой | Infrastructure | Product |
| Источник | TraceAnalyzer.receipt() — lazy+cache | post-hoc из ExecutionReceipt + business rules |
| Поля | trace_id, trace_hash, final_status, resumable, replayable, blocked_actions, escalations, rejected_transitions: tuple[RejectedTransition, ...], health: TraceHealthReport | decision, reason, rules[], trace_ids[] |
| Хранение | SQLite (execution_traces) | PostgreSQL |
| Доступно | M3 | M4+ |

**rejected_transitions семантика:**
- Детерминированная проекция FAILED steps из Trace через TraceAnalyzer.receipt()
- FSM НЕ бросает исключение при rejected transition — завершается со статусом FAILED
- Реакция на rejected_transitions — ответственность Application Service (retry policy, escalation routing)
- Idempotency: UNIQUE(execution_id, step_index) на уровне storage

**Правило зависимости:** NarrativeReceipt зависит от ExecutionReceipt. Никак не наоборот.

### ADR-003 — Async External Systems (suspend/resume)

Все внешние async-подтверждения используют nano-vm suspend/resume. Polling ЗАПРЕЩЁН.

**Паттерн — YooKassa:**
```python
# Step 1: CreatePaymentTool
result = yookassa.create(
    metadata={"trace_id": trace.trace_id, "program_name": "payment_confirmation"}
)
return "PENDING"   # FSM → SUSPENDED

# Step 2: Webhook handler
trace_id = request.body["metadata"]["trace_id"]
# Guard: дубли, concurrent resume, trace not found
vm.resume_with_program(
    program=payment_confirmation_program,
    trace_id=trace_id,
    webhook_event=request.body
)
# FSM → RUNNING → SUCCESS
```

**Webhook safety:**
- Если program уже SUCCESS → log duplicate, return 200, НЕ re-execute
- Если program status == RUNNING → log "resume in progress", return 200, НЕ вызывать resume снова
- Если trace_id не найден → log suspicious, return 200 (никогда 4xx payment providers)

### ADR-004 — PolicyProvider Composition Order

Порядок: Application Service → PolicyProvider.check() → BaseFSM.transition()

- BaseFSM = graph-only: знает только разрешённые transitions из текущего state
- PolicyProvider вызывается Application Service ДО BaseFSM.transition()
- M3: GovernedToolExecutor (nano-vm-mcp) берёт на себя роль PolicyProvider на уровне Step

### ADR-005 — Provider Fallback (NEW, v1.3)

**Решение:** Provider fallback реализуется через FSM-переходы, НЕ через gateway layer.

```
TOOL(attempt_yandexgpt) → CONDITION($provider_ok < 1) → TOOL(switch_to_gigachat) → LLM(retry)
```

Обоснование: gateway fallback (LiteLLM/Bifrost) не знает о состоянии пайплайна. nano-vm решает на уровне FSM-перехода: ProviderUnavailable → δ(S,E) → SwitchProvider → resume. Receipt содержит switch_event + полный след.

**ASTEngine constraint:** числовой sentinel (0/1) через output_key TOOL → CONDITION $var < 1. Строковые литералы как RHS НЕ поддерживаются.

### ADR-006 — YandexGPT / GigaChat как primary stack (NEW, v1.3)

YandexGPT Pro — primary LLM для русскоязычного рынка. GigaChat — fallback.
Оба подключаются через LiteLLMAdapter с openai-compatible endpoint.

```python
from nano_vm.adapters.litellm_adapter import LiteLLMAdapter  # прямой импорт

adapter = LiteLLMAdapter(
    model="openai/yandexgpt-pro",
    api_base="https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
    api_key=YANDEX_API_KEY,
    extra_kwargs={"stream": True, "timeout": 300, "max_tokens": 8192},
)
```

---

## 3. Domain FSMs

| Domain | States |
|---|---|
| Order | DRAFT → CONFIRMED → PAYMENT_PENDING → PAID → COOKING → PACKING → COURIER_ASSIGNED → DELIVERING → DELIVERED → CLOSED |
| KitchenTicket | NEW → QUEUED → PREPARING → READY → HANDED_OFF |
| DeliveryTask | UNASSIGNED → ASSIGNED → PICKED_UP → ON_ROUTE → DELIVERED → FAILED |
| InventoryItem | AVAILABLE → LOW_STOCK → CRITICAL → OUT_OF_STOCK |
| Promotion | CREATED → ACTIVE → EXPIRED → ARCHIVED |
| BusinessSchedule | OPEN → CLOSING_SOON → CLOSED → OPEN |
| CustomerData | ACTIVE → RETAINED → ANONYMIZED → DELETED |

### 3.1 BaseFSM interface

```python
from dataclasses import dataclass
from typing import Generic, TypeVar

StateType = TypeVar("StateType")
EventType = TypeVar("EventType")

@dataclass(frozen=True)
class TransitionResult:
    success: bool
    new_state: StateType | None       # None если transition rejected
    rejected_event: EventType | None  # populated при rejection
    reason: str | None                # human-readable
    # M3: StepResult из nano-vm — natural successor TransitionResult

class BaseFSM(Generic[StateType, EventType]):
    def transition(self, entity_id: str, event: EventType) -> TransitionResult: ...
    def get_allowed_events(self, state: StateType) -> list[EventType]: ...
    # get_allowed_events = graph-level ONLY. Business rules → PolicyProvider.
```

### 3.2 Структура модулей

```
app/
├── fsm/
│   └── core/
│       ├── base.py       # BaseFSM, TransitionResult, EventType
│       └── registry.py   # FSM registration + lookup by domain
└── domains/
    ├── orders/fsm.py     # OrderFSM(BaseFSM[OrderState, OrderEvent])
    ├── kitchen/fsm.py
    ├── delivery/fsm.py
    ├── inventory/fsm.py
    ├── promotions/fsm.py
    ├── schedule/fsm.py
    └── privacy/fsm.py
```

---

## 4. Agent Layer (M4+)

| Agent | ALLOWED | FORBIDDEN |
|---|---|---|
| OrderAgent | collect input, structured command | modify order state |
| MenuAgent | descriptions, recommendations | modify inventory |
| PromotionAgent | campaign text, ideas | activate promotions |
| SupportAgent | answer questions, explain state | change order lifecycle |

Enforcement: GovernedToolExecutor + PolicySnapshot (M3+).

---

## 5. Milestones

### M1 — Foundation (текущий)

**Deliverables:**
- PostgreSQL schema + Alembic migrations
- FastAPI skeleton (health, orders CRUD)
- OrderFSM (custom BaseFSM) — accepts EVENT not state
- Lightweight Trace (custom, без nano-vm)
- Basic Admin (read-only endpoints)

**Запрещено в M1:**
- ExecutionVM, GovernanceEnvelope (не стабить — cargo cult)
- Прямые state assignments вне terminal tools
- Polling loops (ни одного `while not paid: sleep()`)

**Migration risk:** LOW при соблюдении `transition(event=...)` интерфейса. HIGH при любой прямой мутации.

### M2 — Business Operations

**Deliverables:**
- KitchenFSM, DeliveryFSM, InventoryFSM
- YooKassa suspend/resume (даже без ExecutionVM — wire trace_id в metadata)
- Telegram + SMS notifications
- Idempotency layer (trace_id + program_step)

### M3 — nano-vm Integration

**Deliverables:**
- Замена custom FSM на ExecutionVM
- nano-vm-mcp как gateway
- ExecutionReceipt через TraceAnalyzer.receipt()
- GovernanceEnvelope + PolicySnapshot
- Full audit trail

**Decision point:** AbstractStore с PG backend — оценить на M3 planning по реальной нагрузке.

**Migration gate:**
```bash
grep -r 'status = ' --include='*.py' app/ | grep -v '# terminal-tool'
# → zero results
```

### M4 — AI Layer

**Deliverables:**
- OrderAgent, MenuAgent, PromotionAgent, SupportAgent
- YandexGPT Pro primary + GigaChat fallback (ADR-006)
- Provider fallback через FSM-переходы (ADR-005)
- NarrativeReceipt (только после ExecutionReceipt stable)

### M5 — Observability

**Deliverables (частично DONE в nano-vm v0.8.5/v0.8.6):**
- OTel spans per FSM step (backlog sprint_6_core_otel)
- transition_entropy + transition_stats — DONE v0.8.4/v0.4.3
- ProgramValidator — DONE v0.8.5
- ExecutionReceipt + RejectedTransition — DONE v0.8.5
- Application-level wiring — M5

---

## 6. Anti-Patterns (CI-enforced от M3)

| Anti-pattern | Почему запрещено |
|---|---|
| `order.status = X` | прямая мутация — только terminal tool |
| `while not paid: sleep()` | polling — использовать suspend/resume |
| `fsm_instances` таблица | дублирует Trace — current_state в entity primary table |
| `GovernanceEnvelope` stub M1/M2 | cargo cult — реализовать в M3 или не создавать |
| NarrativeReceipt поля в ExecutionReceipt | смешение слоёв |
| Agent с прямым ORM/SQL | только через Governed Tool |
| `get_allowed_events()` с business logic | FSM = graph-only, PolicyProvider = rules |
| string literal как RHS в ASTEngine condition | не поддерживается v0.8.6 — числовой sentinel |
| `contains` в ASTEngine condition | не поддерживается v0.8.6 — использовать `==` |
| provider fallback через gateway | gateway не знает FSM state — использовать ADR-005 |

---

## 7. Open Questions

| ID | Статус |
|---|---|
| OQ-1 | YooKassa metadata ≤1KB — trace_id помещается. CLOSED. |
| OQ-2 | M1 custom FSM: app/fsm/core/base.py. CLOSED. |
| OQ-3 | nano-vm-mcp PG backend: не нужен M1-M2. Decision point: M3 planning. OPEN. |
| OQ-4 | AbstractStore interface: флаг known debt. Decision M3. OPEN. |

---

## Changelog

| Version | Changes |
|---|---|
| v1.3 | Provider fallback ADR-005 (FSM not gateway). YandexGPT/GigaChat ADR-006. ASTEngine constraints (string RHS, contains). LiteLLMAdapter прямой импорт. frozen-model pattern через trace_hash. NarrativeReceipt gate: только после ExecutionReceipt stable M3. AbstractStore decision deferred M3. |
| v1.2 | Basis nano-vm v0.8.6/v0.4.5. ADR-004 PolicyProvider. TransitionResult contract. ExecutionReceipt rejected_transitions semantics. |
| v1.1 | ADR-001 terminal tool invariant. Mutation rules. Persistence isolation. Anti-patterns. |
| v1.0 | Initial. Three ADRs. Milestone scoping. |
