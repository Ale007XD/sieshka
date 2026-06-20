# DEPLOYMENT.md — Sieshka: развёртывание и итерационная работа с opencode + nano-vm-dev-agent

Это инструкция для Алекса: как поднять проект локально, как прогонять спринты из
`SPRINTS_SIESHKA.md` силами `nano-vm-dev-agent`, и как использовать `opencode` как
интерфейс для агента вместо ручного вызова Python-скриптов.

---

## 0. Что в этом пакете

```
sieshka/
├── docs/
│   ├── ARCHITECTURE.md         ← спека v1.3, актуализирована под текущий стек
│   ├── SPRINTS_SIESHKA.md      ← backlog: 17 спринтов M1-M5, готовых для dev-agent
│   └── AGENTS_SIESHKA.json     ← state snapshot — context pack для следующей сессии
├── app/                         ← M1 scaffold (DONE, 17/17 pytest)
│   ├── fsm/core/                ← BaseFSM, TransitionResult, registry
│   ├── domains/{orders,kitchen,delivery}/  ← 3 FSM реализованы
│   ├── programs/order_programs.py          ← DRAFT nano-vm Programs (M3 reference)
│   ├── tools/order_tools.py                ← DRAFT nano-vm Tools (M3 reference)
│   ├── webhooks/yookassa.py                ← ADR-003 suspend/resume skeleton
│   ├── trace.py, config.py, main.py
├── migrations/001_initial_schema.sql
├── tests/unit/                  ← 17 тестов, все зелёные
├── .github/workflows/ci.yml
├── docker-compose.yml, Dockerfile, .env.example
└── pyproject.toml
```

Статус: M1 scaffold готов и протестирован (`sprint_m1_scaffold` = DONE в `SPRINTS_SIESHKA.md`).
Следующий шаг — `sprint_m1_inventory_promotions`.

---

## 1. Локальное развёртывание (без nano-vm, чистый M1)

```bash
# 1. Распаковать проект
mkdir -p ~/projects/sieshka
cd ~/projects/sieshka
# скопировать содержимое этого пакета сюда

# 2. Виртуальное окружение
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Установка зависимостей (dev extras включают nano-vm для M3+ заранее)
pip install -e ".[dev]"

# 4. Поднять PostgreSQL локально (для M1 пока не обязательно — FSM тестируется in-memory)
docker compose up -d postgres

# 5. Переменные окружения
cp .env.example .env
# заполнить DATABASE_URL если меняли пароль/порт

# 6. Прогнать тесты — должно быть 17/17 GREEN
pytest tests/unit/ -v

# 7. mypy gate
mypy app/ --ignore-missing-imports

# 8. ruff gate
ruff check .

# 9. Запустить приложение
uvicorn app.main:app --reload
# → http://localhost:8000/health
# → http://localhost:8000/  (milestone overview)
```

Если все три gate (pytest/mypy/ruff) зелёные — можно переходить к итерационной работе агентом.

---

## 2. Полный Docker-стек (PostgreSQL + приложение)

```bash
docker compose up -d --build
curl http://localhost:8000/health
```

`docker-compose.yml` монтирует `migrations/` в `docker-entrypoint-initdb.d` — схема
применяется автоматически при первом старте контейнера Postgres.

---

## 3. Установка opencode + nano-vm-dev-agent

Если `opencode` ещё не установлен:

```bash
# проверить наличие
which opencode || echo "не установлен — см. документацию opencode для установки в твоей среде"
```

`nano-vm-dev-agent` — твой локальный репозиторий (v0.2.0, DA-1..4 DONE). Предполагается,
что он уже развёрнут в `~/projects/nano-vm-dev-agent` со своим venv и доступом к Vibecode proxy.

Убедись, что переменные окружения для LLM-прокси выставлены ДО любого импорта litellm
(это уже зафиксированный constraint в твоей экосистеме):

```bash
export OPENAI_API_KEY="<vibecode-key>"
export OPENAI_API_BASE="https://api.vibecode-claude.online/v1"
```

---

## 4. Прогон спринта через nano-vm-dev-agent

### 4.1 Принцип

`SPRINTS_SIESHKA.md` написан в том же стиле, что и `SPRINTS.md` в основной экосистеме —
каждый sprint-блок самодостаточен: deliverables, constraints, gate. Это формат, который
ты уже передаёшь в `PROGRAM_SPRINT` DSL вручную при работе с dev-agent.

### 4.2 Шаги (ручной запуск, opencode как обёртка над CLI)

```bash
cd ~/projects/nano-vm-dev-agent
source .venv/bin/activate

# Контекст: положи ARCHITECTURE.md, SPRINTS_SIESHKA.md, AGENTS_SIESHKA.json,
# CONSTRAINTS.md (основной, из nano-vm экосистемы) в директорию, которую runner.py читает
# как context pack. Если у тебя уже есть convention (~/projects/nano-vm-dev-agent/context/),
# скопируй туда:

cp ~/projects/sieshka/docs/ARCHITECTURE.md      context/
cp ~/projects/sieshka/docs/SPRINTS_SIESHKA.md   context/
cp ~/projects/sieshka/docs/AGENTS_SIESHKA.json  context/
cp /path/to/nano-vm-ecosystem/CONSTRAINTS.md     context/CONSTRAINTS_CORE.md

# Запуск runner.py с целевым репозиторием = sieshka
python runner.py \
  --target-repo ~/projects/sieshka \
  --sprint sprint_m1_inventory_promotions \
  --context-dir context/
```

Если твой `runner.py` принимает sprint-блок как inline-текст (а не файл), вытащи нужный
блок из `SPRINTS_SIESHKA.md` между ` ``` ` маркерами и передай как `--sprint-spec`.

### 4.3 Что должно произойти (повторяет DA-4 транзакционный паттерн)

```
1. read_repo_files()           — агент читает текущие app/domains/orders/fsm.py как образец паттерна
2. apply_search_replace_patch  — генерирует новые app/domains/inventory/fsm.py, promotions/fsm.py, etc.
3. stage_patch()                — буферизация in-memory, диск не трогается
4. validate_staged_mypy()       — tmpdir overlay, mypy --strict проверяет staged-контент
5. [mypy FAIL] → rollback_patches()   — откат, диск нетронут, отчёт об ошибке
   [mypy PASS] → commit_patches()      — атомарная запись на диск
6. run_pytest()                  — прогон tests/unit/fsm/test_inventory_fsm.py и т.д.
7. [pytest FAIL] → git_checkout_files()  — safety net, откат HEAD
   [pytest PASS] → sprint DONE
```

### 4.4 Gate — без исключений

Каждый sprint считается завершённым только если:

```bash
pytest tests/unit/ -v        # GREEN, без новых failures
mypy app/ --ignore-missing-imports   # 0 errors
ruff check .                  # clean
```

Если dev-agent зафиксировал `FailureFingerprint` дважды на одном баге — он должен
эскалировать, а не повторять попытку (правило из основной экосистемы, применимо
без изменений).

### 4.5 После каждого спринта — обновление состояния

```bash
# Вручную или через memory_user_edits-аналог в твоём workflow:
# 1. Открой docs/AGENTS_SIESHKA.json
# 2. Перенеси завершённый sprint из "sprints_next" в "sprints_done"
# 3. Обновись domain_fsms_status / key_files_done
# 4. Если в ходе спринта возникло новое архитектурное решение — добавь в
#    sieshka_specific_decisions с classification: DECISION/CONSTRAINT/LEARNING
```

Это тот же knowledge lifecycle automaton (`PENDING → VALIDATED → {CONSTRAINT|DECISION|LEARNING|EXPIRED}`),
что уже используется в основной экосистеме — просто применённый к отдельному репозиторию.

---

## 5. Порядок спринтов (краткая шпаргалка)

```
M1 (текущий):
  [DONE] sprint_m1_scaffold
  → sprint_m1_inventory_promotions     ← следующий
  → sprint_m1_postgres_wiring
  → sprint_m1_api_routes

M2:
  → sprint_m2_kitchen_delivery_wiring
  → sprint_m2_yookassa_integration
  → sprint_m2_notifications
  → sprint_m2_idempotency_layer

M3 (требует M1+M2 DONE):
  → sprint_m3_program_validator_gate
  → sprint_m3_tools_implementation
  → sprint_m3_execution_vm_swap        ← migration gate: grep CI на прямые мутации
  → sprint_m3_governance_envelope
  → sprint_m3_execution_receipt_wiring

M4 (требует M3 DONE):
  → sprint_m4_yandexgpt_gigachat_adapters
  → sprint_m4_provider_fallback_fsm
  → sprint_m4_order_agent
  → sprint_m4_menu_promotion_support_agents
  → sprint_m4_narrative_receipt        ← gate: ExecutionReceipt стабилен в M3

M5 (параллельно M4, требует M3 DONE):
  → sprint_m5_otel_wiring               ← блокируется на upstream nano_vm OTel hooks
  → sprint_m5_program_validator_startup_gate
  → sprint_m5_transition_stats_dashboard
```

Полные deliverables/constraints/gate для каждого — в `docs/SPRINTS_SIESHKA.md`.

---

## 6. Контрольные точки перед M3 (важно)

Перед тем как запускать `sprint_m3_execution_vm_swap`, обязательно прогони:

```bash
grep -r 'status = \|state = ' --include='*.py' app/ | grep -v '# terminal-tool'
```

Если результат непустой — где-то осталась прямая мутация состояния вне terminal tool.
Это твердый гейт миграции из ARCHITECTURE.md §6, и dev-agent должен использовать
именно эту команду как automated check, а не доверять самоотчёту.

---

## 7. Что НЕ делать (anti-patterns, см. ARCHITECTURE.md §6)

- Не стабить `GovernanceEnvelope` до M3 — это cargo cult, явно запрещено
- Не создавать `fsm_instances` таблицу — current_state живёт в primary table сущности
- Не использовать polling (`while not paid: sleep()`) — только suspend/resume
- Не смешивать поля `NarrativeReceipt` и `ExecutionReceipt`
- Не давать агентам прямой доступ к ORM/SQL — только через Governed Tool (M3+)
- Не использовать строковые литералы как RHS в ASTEngine CONDITION — числовой sentinel (0/1)
- Не использовать `contains` в ASTEngine condition (не поддерживается в 0.8.6) — `==`

---

## 8. Быстрый health-check всего стека

```bash
# Приложение
curl http://localhost:8000/health

# PostgreSQL
docker compose exec postgres psql -U sieshka -d sieshka -c '\dt'

# Тесты
pytest tests/ -v --tb=short

# Полный gate
pytest tests/unit/ -v && mypy app/ --ignore-missing-imports && ruff check .
```

Если все три команды в последней строке отработали без ошибок — текущий milestone
гейт пройден, можно запускать следующий sprint через dev-agent.
