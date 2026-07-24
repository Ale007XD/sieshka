from __future__ import annotations

import os
import re


def _ensure(var: str, val: str) -> None:
    os.environ[var] = os.environ.get(var) or val


# Must set env vars BEFORE importing app.llm.providers (litellm reads them at
# module-load time, and pydantic-settings reads them at Settings() creation).
_ensure("OPENAI_API_KEY", "test-openai-key")
_ensure("OPENAI_API_BASE", "https://openrouter.ai/api/v1")
_ensure("NVIDIA_NIM_API_KEY", "test-nvidia-key")
_ensure("YANDEX_API_KEY", "test-yandex-key")
_ensure("YANDEX_API_BASE", "https://llm.api.cloud.yandex.net/foundationModels/v1/completion")
_ensure("YANDEX_MODEL", "openai/yandexgpt-pro")
_ensure("GIGACHAT_API_KEY", "test-gigachat-key")
_ensure("GIGACHAT_API_BASE", "https://gigachat.devices.sberbank.ru/api/v1")
_ensure("GIGACHAT_MODEL", "openai/GigaChat-Pro")

from nano_vm.adapters.litellm_adapter import LiteLLMAdapter  # noqa: E402

from app.llm.providers import (  # noqa: E402
    gigachat_adapter,
    openrouter_adapter,
    yandexgpt_adapter,
)


class TestNvidiaNimConfig:
    def test_model_format(self) -> None:
        assert re.match(
            r"^nvidia_nim/[^/]+/[^/]+$",
            openrouter_adapter.model,
        ), f"Model {openrouter_adapter.model!r} does not match 'nvidia_nim/vendor/model'"

    def test_stream_and_max_tokens(self) -> None:
        assert openrouter_adapter._extra.get("stream") is True
        assert openrouter_adapter._extra.get("max_tokens") == 8192

    def test_is_litellm_adapter(self) -> None:
        assert isinstance(openrouter_adapter, LiteLLMAdapter)

    def test_api_key_in_kwargs(self) -> None:
        assert openrouter_adapter._extra.get("api_key") == "test-nvidia-key"

    def test_api_base_in_kwargs(self) -> None:
        assert openrouter_adapter._extra.get("api_base") == "https://integrate.api.nvidia.com/v1"


class TestYandexGPTConfig:
    def test_model_from_settings(self) -> None:
        assert yandexgpt_adapter.model == "openai/yandexgpt-pro"

    def test_api_key_and_base_in_kwargs(self) -> None:
        assert yandexgpt_adapter._extra.get("api_key") == "test-yandex-key"
        assert yandexgpt_adapter._extra.get("api_base") == "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def test_stream_and_max_tokens(self) -> None:
        assert yandexgpt_adapter._extra.get("stream") is True
        assert yandexgpt_adapter._extra.get("max_tokens") == 8192

    def test_is_litellm_adapter(self) -> None:
        assert isinstance(yandexgpt_adapter, LiteLLMAdapter)


class TestGigaChatConfig:
    def test_model_from_settings(self) -> None:
        assert gigachat_adapter.model == "openai/GigaChat-Pro"

    def test_api_key_and_base_in_kwargs(self) -> None:
        assert gigachat_adapter._extra.get("api_key") == "test-gigachat-key"
        assert gigachat_adapter._extra.get("api_base") == "https://gigachat.devices.sberbank.ru/api/v1"

    def test_stream_and_max_tokens(self) -> None:
        assert gigachat_adapter._extra.get("stream") is True
        assert gigachat_adapter._extra.get("max_tokens") == 8192

    def test_is_litellm_adapter(self) -> None:
        assert isinstance(gigachat_adapter, LiteLLMAdapter)
