# SPRINTS_SIESHKA.md — backlog для nano-vm-dev-agent + opencode
# Базис: ARCHITECTURE.md v1.3, AGENTS.md (nano-vm ecosystem), CONSTRAINTS.md
#
# Использование:
#   opencode читает этот файл как roadmap.
#   nano-vm-dev-agent выполняет один sprint_* за прогон через PROGRAM_SPRINT.
#   Gate каждого спринта: pytest GREEN + mypy 0 errors — без исключений.
#   После каждого спринта: обновить state_variables в AGENTS.md (sieshka секция).

## Принцип декомпозиции
Каждый sprint — одна транзакция знания: реализация → тест → gate → commit.
Sprint НЕ переходит к следующему файлу пока текущий тест не зелёный.
Транзакционный патчинг (DA-4 паттерн) обязателен: stage → validate_staged_mypy(tmpdir) →
commit_patches() → run_pytest → [git_checkout_files() при provале].

---

## M1 — Foundation (текущий milestone)

### sprint_m1_scaffold — DONE (этот пакет файлов)
```
Deliverables:
  app/fsm/core/base.py        — BaseFSM, TransitionResult (frozen dataclass)
  app/fsm/core/registry.py    — FSM registry
  app/domains/orders/{models,fsm}.py
  app/domains/kitchen/fsm.py
  app/domains/delivery/fsm.py
  app/trace.py                 — LightweightTrace (M1/M2 custom)
  app/config.py                 — Settings (pydantic-settings)
  app/main.py                   — FastAPI skeleton + /health
  app/webhooks/yookassa.py      — ADR-003 suspend/resume webhook handler
  migrations/001_initial_schema.sql
  tests/unit/fsm/*, tests/unit/test_trace.py — 17/17 PASS
gate: pytest GREEN (17/17), mypy 0 errors
status: DONE
```

### sprint_m1_inventory_promotions — NEXT
```
Deliverables:
  app/domains/inventory/fsm.py   — InventoryFSM (AVAILABLE→LOW_STOCK→CRITICAL→OUT_OF_STOCK)
  app/domains/promotions/fsm.py  — PromotionFSM (CREATED→ACTIVE→EXPIRED→ARCHIVED)
  app/domains/schedule/fsm.py    — BusinessScheduleFSM (OPEN→CLOSING_SOON→CLOSED→OPEN, cyclic)
  app/domains/privacy/fsm.py     — CustomerDataFSM (ACTIVE→RETAINED→ANONYMIZED→DELETED)
  tests/unit/fsm/test_inventory_fsm.py
  tests/unit/fsm/test_promotions_fsm.py
  tests/unit/fsm/test_schedule_fsm.py
  tests/unit/fsm/test_privacy_fsm.py
constraints:
  - Same BaseFSM[StateType, EventType] pattern as OrderFSM
  - BusinessScheduleFSM: cyclic graph OPEN→CLOSING_SOON→CLOSED→OPEN — verify no false "terminal state" assumption
  - CustomerDataFSM: ANONYMIZED/DELETED states map to future GdprEraseEvent (nano-vm core has this — DO NOT reimplement, just align naming)
gate: pytest GREEN, mypy 0 errors
```

### sprint_m1_postgres_wiring
```
Deliverables:
  app/db.py                      — SQLAlchemy async engine + session factory
  app/repositories/order_repo.py — OrderRepository(get_state, write_state) — feeds OrderFSM callbacks
  app/repositories/__init__.py
  alembic.ini + migrations/env.py — wrap 001_initial_schema.sql as proper Alembic migration
  tests/integration/test_order_repo.py — uses pytest-postgresql or testcontainers
constraints:
  - OrderRepository.write_state() is the ONLY method that executes UPDATE orders SET state=...
  - This method becomes the terminal-tool equivalent for M1 (pre-nano-vm)
  - state_reader/state_writer callbacks passed into OrderFSM.__init__ wrap repo methods
gate: pytest GREEN (unit + integration), mypy 0 errors
```

### sprint_m1_api_routes
```
Deliverables:
  app/api/routes/orders.py  — POST /orders (create DRAFT), POST /orders/{id}/events (trigger transition)
  app/api/routes/admin.py   — GET /admin/orders (read-only list + state filter)
  app/services/order_service.py — Application Service: PolicyProvider stub (always-allow for M1) → OrderFSM.transition()
  tests/unit/test_order_service.py
  tests/integration/test_orders_api.py — httpx AsyncClient against FastAPI app
constraints:
  - order_service.py is where PolicyProvider WOULD be called (ADR-004) — for M1, stub returns always-allow
  - DO NOT put business rules inside OrderFSM — only inside order_service / PolicyProvider stub
  - API routes never call OrderFSM directly — always through order_service
gate: pytest GREEN, mypy 0 errors
```

---

## M2 — Business Operations

### sprint_m2_kitchen_delivery_wiring
```
Deliverables:
  app/repositories/kitchen_repo.py, delivery_repo.py
  app/services/kitchen_service.py, delivery_service.py
  app/api/routes/kitchen.py, delivery.py
  Cross-domain orchestration: order COOKING event → auto-create kitchen_ticket (NEW state)
  tests/integration/test_kitchen_flow.py, test_delivery_flow.py
constraints:
  - Cross-domain writes (order→kitchen_ticket) still single PG transaction, NO external calls inside
  - This is the M1/M2 analog of ADR-001 terminal tool atomicity — enforce even before nano-vm
gate: pytest GREEN, mypy 0 errors
```

### sprint_m2_yookassa_integration
```
Deliverables:
  app/services/payment_service.py    — yookassa SDK wrapper, create_payment() embeds trace_id in metadata
  app/repositories/payment_repo.py
  Wire app/webhooks/yookassa.py TODO → payment_service.confirm_payment()
  tests/unit/test_payment_service.py (mocked YooKassa client)
  tests/integration/test_yookassa_webhook.py — duplicate/concurrent/not-found guard tests (ADR-003)
constraints:
  - NO polling — suspend (PENDING placeholder order state) / resume (webhook) only
  - Webhook handler MUST return 200 in all branches (never 4xx to YooKassa)
  - idempotency_keys table used to dedupe webhook deliveries by YooKassa event id
gate: pytest GREEN (incl. 3 ADR-003 safety branches), mypy 0 errors
```

### sprint_m2_notifications
```
Deliverables:
  app/services/notification_service.py — Telegram (aiogram or raw Bot API) + SMS stub
  app/tools/notification_tools.py       — pre-shaped for M3 nano-vm TOOL steps
  tests/unit/test_notification_service.py
constraints:
  - Notification calls are fire-and-forget from Application Service — NOT inside PG transactions
  - Shape functions as async def fn(*, order_id: str, **kwargs) -> str to match future nano-vm TOOL signature
gate: pytest GREEN, mypy 0 errors
```

### sprint_m2_idempotency_layer
```
Deliverables:
  app/services/idempotency.py — check_and_record(key) using idempotency_keys table
  Wire into yookassa webhook + any future async resume points
  tests/unit/test_idempotency.py
constraints:
  - trace_id + program_step level granularity (per ARCHITECTURE.md M2 deliverables)
  - This table's schema intentionally mirrors nano-vm-mcp idempotency_keys for M3 migration ease
gate: pytest GREEN, mypy 0 errors
```

---

## M3 — nano-vm Integration (gated — requires M1+M2 DONE)

### sprint_m3_program_validator_gate
```
Deliverables:
  app/programs/order_programs.py — already drafted (this package); run ProgramValidator at startup
  app/startup.py — validate_all_programs() called from FastAPI lifespan
  tests/unit/test_program_validation.py — every Program passes ProgramValidator.is_valid()
constraints:
  - Use nano_vm.validator.ProgramValidator — DO NOT reimplement
  - Every program must have exactly one terminal=True step (ADR-001 invariant) — write explicit test for this
  - PV-13 no_failure_terminal is WARNING not ERROR — do not treat as gate blocker, but review each WARNING
gate: pytest GREEN, mypy 0 errors, ProgramValidator.is_valid()==True for all programs
prerequisite: M1 + M2 DONE
```

### sprint_m3_tools_implementation
```
Deliverables:
  app/tools/order_tools.py — already drafted (this package); replace TODO stubs with real repo/PG calls
  app/tools/kitchen_tools.py, inventory_tools.py
  tests/unit/test_order_tools.py — MockLLMAdapter pattern, no real API in CI
constraints:
  - Terminal tools (write_order_state_*): single PG transaction, NO external HTTP/MQ calls inside (ADR-001)
  - Numeric sentinel returns (0/1) for CONDITION-feeding tools — NOT string literals (ASTEngine constraint)
  - Always read current PG row before writing — never reconstruct from cached state
gate: pytest GREEN, mypy 0 errors
```

### sprint_m3_execution_vm_swap
```
Deliverables:
  app/services/order_service.py — swap OrderFSM.transition() calls → ExecutionVM.run(program=...)
  app/db_nano.py — nano-vm-mcp SQLite WAL store init (separate from PG engine)
  Migration gate script: scripts/check_no_direct_mutation.sh (grep CI gate from ARCHITECTURE.md §6)
  tests/integration/test_execution_vm_orders.py — full order lifecycle through ExecutionVM
constraints:
  - grep -r 'status = ' --include='*.py' app/ | grep -v '# terminal-tool' → zero results — CI HARD GATE
  - OrderFSM class retained but unused/deprecated — do not delete (rollback safety), mark @deprecated
  - StateContext persistence wired per nano-vm RFC (already implemented in nano-vm core — just integrate)
gate: pytest GREEN, mypy 0 errors, migration gate script returns 0
prerequisite: sprint_m3_program_validator_gate + sprint_m3_tools_implementation DONE
```

### sprint_m3_governance_envelope
```
Deliverables:
  app/policy/policy_snapshot.py — PolicySnapshot definitions per domain (orders, kitchen, delivery)
  app/services/order_service.py — wire GovernedToolExecutor for tool calls (ADR-004 M3 migration)
  tests/unit/test_governance.py
constraints:
  - GovernanceEnvelope from nano-vm core — DO NOT reimplement, import and configure only
  - This is the FIRST point GovernanceEnvelope is allowed to exist (forbidden in M1/M2 per CONSTRAINTS.md)
gate: pytest GREEN, mypy 0 errors
prerequisite: sprint_m3_execution_vm_swap DONE
```

### sprint_m3_execution_receipt_wiring
```
Deliverables:
  app/api/routes/admin.py — GET /admin/orders/{id}/receipt → TraceAnalyzer.receipt()
  tests/integration/test_receipt_endpoint.py
constraints:
  - ExecutionReceipt fields exactly as nano_vm v0.8.5: trace_id, trace_hash, final_status, resumable,
    replayable, blocked_actions, escalations, rejected_transitions, health
  - NO custom fields added at this layer — NarrativeReceipt (M4) is the place for product-layer fields
gate: pytest GREEN, mypy 0 errors
prerequisite: sprint_m3_execution_vm_swap DONE
```

---

## M4 — AI Layer (gated — requires M3 DONE)

### sprint_m4_yandexgpt_gigachat_adapters
```
Deliverables:
  app/llm/providers.py — LiteLLMAdapter configs for YandexGPT Pro (primary) + GigaChat (fallback)
  tests/unit/test_llm_providers.py — config validation only, no real API calls in CI
constraints:
  - from nano_vm.adapters.litellm_adapter import LiteLLMAdapter — direct import (CONSTRAINTS.md)
  - OPENAI_API_KEY + OPENAI_API_BASE env vars set BEFORE litellm import (module load order matters)
  - stream=True, timeout=300, max_tokens=8192 per Vibecode-validated pattern
gate: pytest GREEN, mypy 0 errors
```

### sprint_m4_provider_fallback_fsm
```
Deliverables:
  app/programs/llm_fallback_program.py — TOOL(attempt_yandexgpt)→CONDITION($ok<1)→TOOL(switch_gigachat) (ADR-005)
  app/tools/llm_fallback_tools.py — numeric sentinel (0/1) output, NOT string literal RHS
  tests/integration/test_provider_fallback.py — simulate YandexGPT failure → verify GigaChat switch in Trace
constraints:
  - Reuse provider-fallback-demo pattern (already validated, see DECISIONS.md 2026-06-17 entry)
  - ASTEngine: $provider_ok < 1, NOT $provider_status == "FAILED" (string RHS unsupported)
  - Receipt must contain switch_event evidence — verify via ExecutionReceipt.rejected_transitions or custom output_key trace
gate: pytest GREEN, mypy 0 errors
prerequisite: sprint_m4_yandexgpt_gigachat_adapters DONE
```

### sprint_m4_order_agent
```
Deliverables:
  app/agents/order_agent.py — collects order input, generates structured command (NOT state mutation)
  app/programs/order_agent_program.py — LLM step + CONDITION validation + terminal tool (human/system confirms)
  tests/unit/test_order_agent.py — MockLLMAdapter
constraints:
  - OrderAgent ALLOWED: collect input, generate command. FORBIDDEN: modify order state directly (table §4)
  - Agent output goes through GovernedToolExecutor — never directly to repository/PG
gate: pytest GREEN, mypy 0 errors
prerequisite: sprint_m3_governance_envelope DONE
```

### sprint_m4_menu_promotion_support_agents
```
Deliverables:
  app/agents/menu_agent.py, promotion_agent.py, support_agent.py
  tests/unit/test_menu_agent.py, test_promotion_agent.py, test_support_agent.py
constraints: same GovernedToolExecutor boundary as order_agent; respect per-agent ALLOWED/FORBIDDEN table
gate: pytest GREEN, mypy 0 errors
```

### sprint_m4_narrative_receipt
```
Deliverables:
  app/services/narrative_receipt_service.py — generates NarrativeReceipt from ExecutionReceipt + business rules
  app/repositories/narrative_receipt_repo.py — PostgreSQL storage (NOT SQLite — separate layer per ADR-002)
  tests/unit/test_narrative_receipt.py
constraints:
  - HARD gate: ExecutionReceipt must be stable in M3 before this sprint starts (ADR-002 dependency rule)
  - NO mixing fields: NarrativeReceipt = {decision, reason, rules[], trace_ids[]} only
  - NarrativeReceipt.trace_ids[] references ExecutionReceipt.trace_id — read-only reference, no FK duplication of Trace content
gate: pytest GREEN, mypy 0 errors
prerequisite: sprint_m3_execution_receipt_wiring DONE
```

---

## M5 — Observability (gated — requires M3 DONE, parallel to M4)

### sprint_m5_otel_wiring
```
Deliverables:
  app/telemetry.py — OTel span per FSM step, wraps ExecutionVM hooks
  tests/unit/test_telemetry.py
constraints:
  - nano_vm/telemetry.py + vm.py hooks already on nano-vm backlog (sprint_6_core_otel) — this sprint
    is the APPLICATION-level wiring once that backlog item lands; check nano_vm version supports hooks
    before starting — otherwise this sprint blocks on upstream nano-vm release
gate: pytest GREEN, mypy 0 errors
prerequisite: sprint_m3_execution_vm_swap DONE; nano_vm OTel hooks available upstream
```

### sprint_m5_program_validator_startup_gate
```
Deliverables:
  app/startup.py — extend validate_all_programs() to run on every deploy, fail-fast on ERROR severity
  CI step: validate-programs job in .github/workflows/ci.yml
constraints: IssueSeverity.ERROR blocks deploy; WARNING logged but non-blocking (per nano_vm v0.8.5 semantics)
gate: pytest GREEN, mypy 0 errors, CI validate-programs job GREEN
prerequisite: sprint_m3_program_validator_gate DONE
```

### sprint_m5_transition_stats_dashboard
```
Deliverables:
  app/api/routes/admin.py — GET /admin/transitions?program_name=... → nano_vm_mcp.store.get_transitions()
  tests/integration/test_transition_stats_endpoint.py
constraints: read-only — this endpoint MUST NOT write to transition_stats, only nano-vm-mcp wiring does
gate: pytest GREEN, mypy 0 errors
prerequisite: sprint_m3_execution_vm_swap DONE (nano-vm-mcp transition_stats already exists upstream v0.4.3)
```

---

## Sprint dependency graph (summary)

```
M1: scaffold(DONE) → inventory_promotions → postgres_wiring → api_routes
                                                                    ↓
M2: kitchen_delivery_wiring → yookassa_integration → notifications → idempotency_layer
                                                                    ↓
M3: program_validator_gate ─┬→ tools_implementation ─┬→ execution_vm_swap ─┬→ governance_envelope
                             └───────────────────────┘                     └→ execution_receipt_wiring
                                                                                       ↓
M4: yandexgpt_gigachat_adapters → provider_fallback_fsm     order_agent → menu_promotion_support_agents
                                                                  ↓                      ↓
                                                          narrative_receipt (needs execution_receipt_wiring)

M5: otel_wiring (needs upstream nano_vm hooks)   program_validator_startup_gate   transition_stats_dashboard
    (all M5 sprints parallel to M4, gated on M3 only)
```

## Gate policy (non-negotiable, inherited from CONSTRAINTS.md)
- pytest GREEN + mypy 0 errors — no exceptions, every sprint
- ruff check . (no --fix in CI — recursive trigger risk)
- Transactional patching for dev-agent runs: stage_patch → validate_staged_mypy(tmpdir) → commit_patches → run_pytest → git_checkout_files on pytest failure
- S&R patches: |matches|=1 invariant — read current file before patching, never reconstruct
- FailureFingerprint bounded retry: same fingerprint twice → escalate, not retry
