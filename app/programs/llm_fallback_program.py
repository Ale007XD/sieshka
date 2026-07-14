"""Provider fallback program — two-hop hot-switch FSM.

Primary = OpenRouter free tier; on TIMEOUT → YandexGPT Pro;
on TIMEOUT → GigaChat (last resort).

Multi-hop next_step chain. Requires llm-nano-vm>=0.8.7 (BUG-NEXTSTEP-01/02 fix).
"""

from __future__ import annotations

from nano_vm.models import Program, Step, StepType

PROVIDER_FALLBACK = Program(
    name="provider_fallback",
    steps=[
        Step(
            id="attempt_openrouter",
            type=StepType.TOOL,
            tool="attempt_openrouter",
            args={"prompt": "$prompt", "timeout_seconds": 15},
            output_key="openrouter_result",
            next_step="check_openrouter",
        ),
        Step(
            id="check_openrouter",
            type=StepType.CONDITION,
            condition="$openrouter_result.output < 1",
            then="attempt_yandexgpt",
            otherwise="success_openrouter",
        ),
        Step(
            id="attempt_yandexgpt",
            type=StepType.TOOL,
            tool="attempt_yandexgpt",
            args={"prompt": "$prompt", "timeout_seconds": 15},
            output_key="yandexgpt_result",
            next_step="check_yandexgpt",
        ),
        Step(
            id="check_yandexgpt",
            type=StepType.CONDITION,
            condition="$yandexgpt_result.output < 1",
            then="attempt_gigachat",
            otherwise="success_yandexgpt",
        ),
        Step(
            id="attempt_gigachat",
            type=StepType.TOOL,
            tool="attempt_gigachat",
            args={"prompt": "$prompt", "timeout_seconds": 15},
            output_key="gigachat_result",
            next_step="success_gigachat",
        ),
        Step(
            id="success_openrouter",
            type=StepType.TOOL,
            tool="finalize_success",
            args={"result": "$openrouter_result.output"},
            output_key="final_result",
            is_terminal=True,
        ),
        Step(
            id="success_yandexgpt",
            type=StepType.TOOL,
            tool="finalize_success",
            args={"result": "$yandexgpt_result.output"},
            output_key="final_result",
            is_terminal=True,
        ),
        Step(
            id="success_gigachat",
            type=StepType.TOOL,
            tool="finalize_success",
            args={"result": "$gigachat_result.output"},
            output_key="final_result",
            is_terminal=True,
        ),
    ],
)
