#!/usr/bin/env python3
"""
diag_astengine_string_literal.py
=================================
Диагностика sprint_astengine_string_literal_regression.

Вопрос: на РЕАЛЬНО запиненной в этом venv версии llm-nano-vm —
работает ли `$var == "строковый литерал"` в condition корректно,
или (как задокументировано в CONSTRAINTS.md / DECISIONS.md 2026-06-17,
подтверждено боевым прогоном provider-fallback-demo) парсится, но
всегда возвращает False?

Проверяет ИМЕННО тот исторический кейс из DECISIONS.md:
    ctx = {"check_provider": {"output": "PROVIDER_FAILED"}}
    condition = '$check_provider.output == "PROVIDER_FAILED"'

Запуск на ВПС (внутри venv sieshka, где реально стоит llm-nano-vm):
    source .venv/bin/activate   # или откуда там активируется venv
    python3 diag_astengine_string_literal.py

Ничего не пишет на диск, ничего не мутирует, безопасно гонять в проде.
Exit code: 0 если поведение соответствует README nano_vm (буква == работает),
           1 если воспроизводится старый баг (silent False),
           2 если что-то упало неожиданно (импорт, парсинг и т.п. — само по
             себе диагностическая информация).
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from typing import Any


@dataclass
class CaseResult:
    name: str
    condition: str
    ctx: dict[str, Any]
    expected: str          # человекочитаемое описание ожидания
    outcome: str            # "PASS" | "FAIL" | "ERROR"
    detail: str


def run_case(name: str, condition: str, ctx: dict[str, Any],
             expected_value: bool | None, expected_note: str,
             eval_condition, ASTEvalError) -> CaseResult:
    try:
        result = eval_condition(condition, ctx)
        if expected_value is None:
            # Кейс, где мы просто хотим увидеть факт (без жёсткого assert)
            return CaseResult(name, condition, ctx, expected_note, "PASS",
                               f"eval_condition() -> {result!r}")
        if result == expected_value:
            return CaseResult(name, condition, ctx, expected_note, "PASS",
                               f"eval_condition() -> {result!r} (совпало с ожиданием)")
        return CaseResult(name, condition, ctx, expected_note, "FAIL",
                           f"eval_condition() -> {result!r}, ожидалось {expected_value!r}")
    except ASTEvalError as exc:
        return CaseResult(name, condition, ctx, expected_note, "ERROR",
                           f"ASTEvalError: {exc}")
    except Exception as exc:  # noqa: BLE001 — диагностика, ловим всё
        return CaseResult(name, condition, ctx, expected_note, "ERROR",
                           f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")


def main() -> int:
    print("=" * 78)
    print("sprint_astengine_string_literal_regression — диагностика на живом venv")
    print("=" * 78)

    # --- версия пакета -----------------------------------------------------
    try:
        import importlib.metadata as importlib_metadata
        version = importlib_metadata.version("llm-nano-vm")
    except Exception as exc:  # noqa: BLE001
        version = f"<не удалось определить: {exc}>"
    print(f"llm-nano-vm version (installed): {version}")

    try:
        from nano_vm.ast_engine import ASTEvalError, eval_condition
    except Exception:
        print("\nКРИТИЧНО: не смог импортировать nano_vm.ast_engine.")
        print("Убедись, что скрипт запущен внутри венва, где реально стоит llm-nano-vm.")
        traceback.print_exc()
        return 2

    print(f"import OK: nano_vm.ast_engine.eval_condition = {eval_condition}")
    print()

    cases: list[CaseResult] = []

    # 1. Исторический кейс из DECISIONS.md (provider-fallback-demo, 2026-06-17):
    #    dotted-path $step_id.output == "СТРОКОВЫЙ_ЛИТЕРАЛ"
    cases.append(run_case(
        "historical_provider_failed_match",
        '$check_provider.output == "PROVIDER_FAILED"',
        {"check_provider": {"output": "PROVIDER_FAILED"}},
        expected_value=True,
        expected_note="должно быть True — значение реально совпадает",
        eval_condition=eval_condition, ASTEvalError=ASTEvalError,
    ))

    # 2. Тот же паттерн, но значение НЕ совпадает — должно быть False
    #    (в этом случае False корректен и информативен только в паре с #1)
    cases.append(run_case(
        "historical_provider_failed_mismatch",
        '$check_provider.output == "PROVIDER_FAILED"',
        {"check_provider": {"output": "PROVIDER_SUCCESS"}},
        expected_value=False,
        expected_note="должно быть False — значения разные",
        eval_condition=eval_condition, ASTEvalError=ASTEvalError,
    ))

    # 3. Простой (без dotted-path) вариант — README nano_vm ✅ CORRECT пример
    cases.append(run_case(
        "readme_correct_example_match",
        '$decision == "yes"',
        {"decision": "yes"},
        expected_value=True,
        expected_note='README nano_vm показывает это как ✅ CORRECT',
        eval_condition=eval_condition, ASTEvalError=ASTEvalError,
    ))
    cases.append(run_case(
        "readme_correct_example_mismatch",
        '$decision == "yes"',
        {"decision": "no"},
        expected_value=False,
        expected_note="должно быть False — значения разные",
        eval_condition=eval_condition, ASTEvalError=ASTEvalError,
    ))

    # 4. Контроль: quoted $var — задокументированный footgun, ДОЛЖЕН быть
    #    всегда False независимо от ctx (это НЕ баг, это документированное
    #    поведение — если он вдруг перестал быть always-False, это отдельная
    #    новость похуже).
    cases.append(run_case(
        "quoted_var_footgun_control",
        "'$decision' == \"yes\"",
        {"decision": "yes"},
        expected_value=False,
        expected_note="задокументированный footgun: должно ВСЕГДА быть False",
        eval_condition=eval_condition, ASTEvalError=ASTEvalError,
    ))

    # 5. Контроль: числовой sentinel — заведомо рабочий паттерн, baseline
    cases.append(run_case(
        "numeric_sentinel_control",
        "$stock > 0",
        {"stock": 5},
        expected_value=True,
        expected_note="baseline — числовые сравнения всегда работали",
        eval_condition=eval_condition, ASTEvalError=ASTEvalError,
    ))

    # 6. Контроль: contains — README nano_vm заявляет Supported
    cases.append(run_case(
        "contains_control",
        '$tag contains "urgent"',
        {"tag": "urgent"},
        expected_value=True,
        expected_note="README nano_vm заявляет contains как Supported",
        eval_condition=eval_condition, ASTEvalError=ASTEvalError,
    ))

    # 7. Контроль: method call всё ещё должен падать с ASTEvalError, не тихим False
    result_mc = run_case(
        "method_call_guard_control",
        '$decision.lower() == "yes"',
        {"decision": "Yes"},
        expected_value=None,
        expected_note="ожидаем ASTEvalError (см. случай ниже отдельно)",
        eval_condition=eval_condition, ASTEvalError=ASTEvalError,
    )
    # method_call должен именно упасть с ASTEvalError — это единственный кейс,
    # где "ERROR" означает PASS, а не FAIL. Перекодируем вручную.
    if result_mc.outcome == "ERROR" and "ASTEvalError" in result_mc.detail:
        result_mc = CaseResult(result_mc.name, result_mc.condition, result_mc.ctx,
                                'ожидаем ASTEvalError — это правильное поведение',
                                "PASS", result_mc.detail)
    else:
        result_mc = CaseResult(result_mc.name, result_mc.condition, result_mc.ctx,
                                'ожидаем ASTEvalError, а не тихий результат',
                                "FAIL", result_mc.detail)
    cases.append(result_mc)

    # --- вывод ---------------------------------------------------------
    print(f"{'CASE':38} {'RESULT':7} DETAIL")
    print("-" * 78)
    for c in cases:
        print(f"{c.name:38} {c.outcome:7} {c.detail.splitlines()[0]}")
    print()

    failed = [c for c in cases if c.outcome != "PASS"]

    print("=" * 78)
    if not failed:
        print("ВЕРДИКТ: строковые литералы как RHS в comparison РАБОТАЮТ КОРРЕКТНО")
        print("на этой версии llm-nano-vm. Старая заметка в CONSTRAINTS.md")
        print("('NO string literals as RHS — PROVIDER_FAILED парсится, но False')")
        print("устарела и должна быть снята / заменена ссылкой на эту проверку.")
        return 0
    else:
        print("ВЕРДИКТ: воспроизведено расхождение с README nano_vm. Провалились:")
        for c in failed:
            print(f"  - {c.name}: {c.detail}")
        print()
        print("Если провалился именно historical_provider_failed_match —")
        print("старый баг из DECISIONS.md (2026-06-17) ещё жив на этой версии,")
        print("несмотря на то что README nano_vm показывает этот паттерн как")
        print("✅ CORRECT. Числовой sentinel остаётся единственным безопасным")
        print("паттерном для этой версии — README нужно либо поправить, либо")
        print("зафиксировать версию, с которой пример реально верен.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
