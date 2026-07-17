# CONSTRAINTS.md — статичные ограничения (меняются редко)

## Hard technical
- Python 3.10+: X|Y union syntax only
- Pydantic v2: frozen=True где нужно, model_rebuild() после forward refs
- NO eval()/exec() — ASTEngine only
- ruff line-length=100, select=E,F,I,UP; CI check only (не --fix — рекурсивный триггер)
- mypy --ignore-missing-imports, 0 errors обязательно
- ProgramStore sync (не async)
- hatchling+hatch-vcs: version из git tag
- CI matrix: Python 3.10 / 3.11 / 3.12

## DSL invariants
- condition ctx = {**{k:{'output':v} for k,v in step_outputs.items()}, **state.data}
- terminal шаги в КОНЦЕ steps[] — is_terminal=True
- next_step только на branch targets которые продолжают основной поток
- ASTEngine: NO method calls → ASTEvalError при парсинге (v0.7.5+)
- 'PENDING' зарезервирован FSM suspend-сентинел; использовать REQUIRES_ACTION, AWAITING_3DS
- allowed_outputs только для llm-шагов, не пустой список

## FSM execution semantics (v0.8.x, validated nano-vm-experiment + provider-fallback-demo)
- next_step (v0.8.7+, BUG-NEXTSTEP-01/02 fix): работает на ЛЮБОЙ глубине — multi-hop цепочка
  (step_a→step_b→step_c) и посадка после рекурсии CONDITION→CONDITION оба читают next_step.
  ДО 0.8.7 next_step читался ТОЛЬКО для непосредственного target_step одного CONDITION —
  следующий хоп или landing-точка рекурсии тихо игнорировались, маскируясь совпадением
  порядка steps[]. Sieshka пинует llm-nano-vm>=0.8.8 (см. pyproject.toml) — фикс уже внутри,
  но любой Program, писанный/скопированный с расчётом на старое поведение, надо пересверить
- BREAKING CHANGE (0.8.7): Program, где next_step стоял на шаге, недостижимом старым
  механизмом (и работавшим только за счёт совпадения array order), после апгрейда мог
  изменить порядок исполнения. Все Program в этом репо (order_programs.py,
  llm_fallback_program.py, agent-программы) должны быть перепроверены при апгрейде
  llm-nano-vm — не полагаться молча на то, что "раньше работало"
- Правильный паттерн для multi-gate pipeline: LLM-шаг с next_step="gate_id" → CONDITION с
  then/otherwise → terminal leaf
- TOOL-шаг нормализации вместо allowed_outputs для free-form LLM output: normalize_output(text, keywords) → первый совпавший keyword
- ASTEngine condition синтаксис: $var.field == "VALUE" — переменная с $ префиксом, правая часть в кавычках
- ASTEngine: contains не поддерживается в v0.8.6 (Unknown node type: str) — использовать ==
- OpenRouter/Groq через LiteLLMAdapter: model="groq/model-id" или model="openai/model-id" + явный api_base + api_key в kwargs
- OPENAI_API_KEY + OPENAI_API_BASE выставлять ДО импортов litellm — иначе litellm не подхватывает
- resume_with_program() всегда продвигает current_idx ЗА step, с которого резюмим (resume_after=True в _execute_loop) — повторный PENDING на ТОМ ЖЕ step на следующем resume НЕ пересуспендирует этот step; FSM молча проваливается к SUCCESS с final_output=None, если ни один последующий step не произвёл output. Suspend — one-shot per step occurrence: каждый step программы может suspend'иться ровно один раз за проход. Multi-turn паттерны (ask_name → ask_age → ...) обязаны использовать РАЗНЫЙ step на каждый turn, не повторный опрос одного step. Validated: sprint_5_mcp_vmstep VS-14.
 
## TraceAnalyzer
- Pure post-processing над Trace — NO changes to vm.py/models.py/kernel
- Thresholds: rollback_density>0.3, tool_churn_rate>0.4, path_variance>0.5, transition_entropy>2.5 → alert, НЕ прерывают FSM
- lazy projection — NOT in __init__; вычисление по требованию с internal cache

## ExecutionReceipt
- NO LLM generation, NO operator-specific fields, Receipt ⊆ Trace
- RejectedTransition.timestamp: finished_at ?? started_at ?? '' — triple-branch обязателен, никогда не падает на None
- RejectedTransition source = StepResult(status=FAILED) — НЕ 'rollback events' (терминология зафиксирована)

## ProgramValidator / IssueSeverity
- ValidationIssue.severity: IssueSeverity.ERROR блокирует is_valid(); IssueSeverity.WARNING — не блокирует
- is_valid() = all(severity != ERROR) — не len(issues) == 0
- NO_FAILURE_TERMINAL эмитируется только как WARNING

## ProgramValidator usage (hard constraint, 2026-06-28) — MISSING FROM PREVIOUS SNAPSHOT
- ExecutionVM.run()/resume_with_program() НЕ вызывают ProgramValidator сами — валидация
  opt-in, не гарантирована движком
- ОБЯЗАТЕЛЬНО: ProgramValidator(program).validate().is_valid() перед ЛЮБЫМ vm.run() в
  production-коде — иначе циклы/missing_targets/unreachable steps долетают до runtime
  недетектированными. Sieshka: уже реализовано как startup gate (validate_all_programs()
  вызывается из app/main.py) + per-call проверка внутри order_service.py::_build_vm — оба
  места должны сохраняться при любом рефакторинге Program-registry
- Защита от бесконечного next_step-цикла (включая self-loop) покрыта cycle_detection (DFS
  WHITE/GRAY/BLACK, validator.py) как ERROR на этапе валидации — НЕ дублировать guard внутри
  vm.py hot loop (constrain-and-prove на уровне графа, не detect-and-recover в рантайме)

## LLMAdapter Protocol
- complete() return type: str | tuple[str, dict[str, Any] | None]
- Legacy/custom адаптеры могут возвращать str; встроенные (LiteLLMAdapter) возвращают tuple
- ExecutionVM обрабатывает оба варианта через isinstance(result, tuple) — не менять

## CI hardening
- pyproject.toml: llm-nano-vm явно в [dev] extras (не только в dependencies)
- pyproject.toml: litellm в [dev] extras
- ci.yml: cache-dependency-path: pyproject.toml — обязательно для инвалидации кэша
- ci.yml: pip install --upgrade в install шаге
- ci.yml: smoke-import шаг в lint job — проверяет все публичные модули nano_vm после каждого релиза
- ci.yml: verify wheel шаг в test job — pip show -f llm-nano-vm | grep <новый модуль>
- При добавлении нового модуля в llm-nano-vm: обновить smoke-import список в ci.yml nano-vm-mcp

## dev-agent
- tool-fn: subprocess только, sync, **kwargs обязателен, timeout=120s
- S&R: SEARCH блок |matches|=1 — ValueError иначе → FSM retry
- патчинг: stage_patch() in-memory → validate_staged_mypy() в tmpdir (диск чист) → commit_patches() на диск атомарно → git_checkout_files() safety net если pytest упал после commit
- Transactionality > AST Addressing: repository corruption > address instability по классу риска — транзакционный патчинг обязателен до боевого прогона
- patch_outcome: trace.status=SUCCESS — execution outcome; patch_outcome=ACCEPTED|REJECTED из terminal step output
- тесты: mock subprocess unit, MockLLMAdapter integration, никакого реального API в CI
- Vibecode: OPENAI_API_KEY+OPENAI_API_BASE ДО импортов litellm; stream=True; модель openai/claude-sonnet-4.6

## Tool-authoring: side-effect session boundary (cross-repo, все каналы поверх nano-vm) — MISSING FROM PREVIOUS SNAPSHOT
- Non-goal ядра: nano-vm НЕ управляет транзакциями/сессиями произвольных внешних систем
  (PG/CRM/ticketing) — tool получает **kwargs и возвращает str|int sentinel, движок не видит
  и не контролирует, как tool пишет данные
- ПРАВИЛО: tool с DB/API side-effect НЕ открывает собственную сессию независимо
  (`async with own_session_factory() as session`) — session прокидывается извне через
  closure-injection на момент регистрации tool в VM (functools.partial при _build_vm(session)),
  НЕ через Step.args/context (не сериализуемо в Trace/projected_json)
- Commit — на границе caller'а (сервис/адаптер, владеющий transition), НЕ внутри tool. Tool,
  вызывающий session.commit() сам — нарушение
- Sieshka: все 9 tools в order_tools.py переведены на этот паттерн (sprint_m3_session_boundary_fix,
  DONE 2026-07-01) — референсная реализация для любого нового governed tool (menu/zone/
  schedule agents, M7)

## Terminal TOOL step failure propagation (2026-07-02) — MISSING FROM PREVIOUS SNAPSHOT
- TOOL-шаг с is_terminal:True и без downstream CONDITION, читающего его output — единственный
  способ дать Trace.status=FAILED — raise внутри tool-функции. return sentinel-строки/int без
  CONDITION-консьюмера НИКОГДА не помечает Trace как FAILED
- Sentinel-return (OK/ERROR, 0/1) валиден ТОЛЬКО когда следующий CONDITION-шаг явно читает
  $<step.id>.output и ветвится на failure-terminal
- Найдено и исправлено 3 раза в этом репо: order_tools.py (2026-07-02), kitchen_tools.py
  (2026-07-03), inventory_tools.py (2026-07-03) — все возвращали "ERROR" вместо raise на
  race-guard failure. Любой новый governed tool (M7 agent apply_command'ы) обязан следовать
  raise-паттерну с первого написания, не постфактум-рефактором

## DSL args resolution: $output_key.output vs $step_id.output — MISSING FROM PREVIOUS SNAPSHOT
- nano_vm vm.py::_resolve()._lookup() (TOOL step args) индексирует ТОЛЬКО по step.id
  (state.step_outputs) — без fallback на output_key-алиас для multi-part выражений ($x.y).
  CONDITION-контекст это прощает (transparent-skip на non-dict), TOOL args — нет
- ПРАВИЛО: в args TOOL-шага всегда "$<step.id>.output", никогда "$<output_key>.output".
  Найдено дважды в order_programs.py (2026-07-02) — один случай молчаливый (литерал строка
  ушёл в PG незамеченным), один громкий (ValueError на UUID(literal))

## Sieshka integration tests — Postgres fixture + isolation (added 2026-07-10) — MISSING FROM PREVIOUS SNAPSHOT
- postgres_dsn (tests/integration/conftest.py, session-scope): CREATE/DROP DATABASE против
  уже работающего sieshka-postgres-1 контейнера — НЕ testcontainers.PostgresContainer
  (детерминированно зависает на initdb под Docker Desktop/WSL2 на чистом томе)
- migrations/env.py::run_migrations_online() игнорирует alembic.ini/Config.set_main_option()
  целиком — подключается через create_async_engine(settings.DATABASE_URL) напрямую;
  единственный рабочий рычаг — временная подмена settings.DATABASE_URL перед
  command.upgrade(), restore в finally
- Любая session_factory-фикстура с assertion на пустоту всей таблицы/точное количество строк
  без фильтра по id (`== []`, `len(x) == N` на неотфильтрованном запросе) ОБЯЗАНА делать
  TRUNCATE перед yield — postgres_dsn session-scoped, данные из более ранних тестов в том же
  файле/сессии иначе никуда не деваются. Найдено и исправлено: test_menu_repo.py

## Workflow rules
- Читать исходник перед правкой; str_replace или полная перезапись
- Тесты обязательны для каждого deliverable
- ANTI_PROSE: коммуникация data-dense; без дублирования неизменённых кусков спринтов

## ASTEngine limits (documented, не баги)
- NO method calls: .lower/.strip/.upper/.split
- NO arithmetic operators
- NO parentheses grouping
- NO string interpolation внутри condition expression
- Supported: == != > < in not_in and or not contains $var.field
- NO string literals as RHS in comparisons ('PROVIDER_FAILED' парсится но возвращает False)
- Числовой sentinel паттерн: TOOL output_key → 0/1, CONDITION $var < 1 или $var > 0

## kyc-demo specific
- run_iter.py: stream=False обязателен (stream=True → CustomStreamWrapper has no attribute 'choices')
- engine/ store/ модули: NO import streamlit — тестируются без st.runtime
- mypy.ini обязателен в корне проекта: [mypy-streamlit.*] ignore_missing_imports = True
- session_state: assert isinstance(val, T) вместо type: ignore[return-value] — Python 3.14 strict отклоняет unused ignore
- Typed dataclasses (InjectorDef) вместо dict[str, object] для атрибутного доступа под mypy --strict
- cat >> в bash не гарантирует пустую строку перед def — pytest не собирает слитые тесты; использовать cat > (полная перезапись) для test файлов
- Vibecode multi-file JSON ненадёжен для scaffold; писать файлы руками через cat > heredoc

## nano-vm-mcp specific
- AGENT_DEBUGGER_TOKEN/URL читаются на уровне модуля — тесты через patch.object(tools, "AGENT_DEBUGGER_TOKEN", ...), не patch.dict(os.environ)
- _build_debugger_payload: trace_id передаётся внутри trace{} (не на верхнем уровне) — TraceRequest schema: {trace: {additionalProperties: true}}
- _build_debugger_payload: status нормализуется через .split(".")[-1] — "TraceStatus.FAILED" → "FAILED"; аналогично для StepStatus
- AGENT_DEBUGGER_TOKEN обязателен в .env для auto-diagnostic; без токена diagnostic тихо absent, execution не прерывается

## tarot-bot specific
- ProgramValidator BFS: TOOL шаг с condition/then/otherwise — edges не видны валидатору.
  Правильный паттерн: отдельный TOOL шаг (выполняет) + отдельный CONDITION шаг (ветвит).
- asyncio_mode = "auto" в pyproject.toml — обязателен для pytest в tarot-bot (aiogram async)
- give_spreads = admin_give — алиас для backward compat с тестами; не удалять
- TOOL шаги: args обязателен для передачи context vars (user_id, execution_date, salt и т.д.) — FSM не передаёт автоматически
- Stars payment: run_full_reading(free_spreads=1) после successful_payment — не resume_with_program (Stars flow synchronous, не suspend)
- i18n: bot/i18n.py dict + t(key, lang); LLM → "Respond in $language" в промпте; EN default
- allowed_outputs не использовать для free-text LLM шагов — _check_allowed_outputs требует точное совпадение stripped == allowed
- LLM provider: Groq через OPENAI_API_BASE + OPENAI_API_KEY (litellm openai-compatible); llm-nano-vm[litellm] в requirements.txt
- nano_vm.adapters.litellm_adapter.LiteLLMAdapter — прямой импорт (не через __init__)

## support-bot pilot specific
- Pilot — отдельный репо/папка, НЕ рефакторинг tarot-bot прод-кода; sprint_tarot_3 CI не трогать
- normalize_output(text, keywords) вместо allowed_outputs для classify_intent/policy_gate — паттерн из tarot-bot
- WhatsApp/Zalo — вне scope pilot (business verification deps); Telegram+Web достаточны как proof
- channel adapter = receive(payload)→vm.step()→send(output); адаптер не имеет доступа к ядру кроме vm.step()
