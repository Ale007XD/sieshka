from __future__ import annotations

import os
import re

# Must set env vars BEFORE importing app.llm.providers (litellm reads them at
# module-load time, and pydantic-settings reads them at Settings() creation).
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("OPENAI_API_BASE", "https://openrouter.ai/api/v1")
os.environ.setdefault("YANDEX_API_KEY", "test-yandex-key")
os.environ.setdefault("YANDEX_API_BASE", "https://llm.api.cloud.yandex.net/foundationModels/v1/completion")
os.environ.setdefault("YANDEX_MODEL", "openai/yandexgpt-pro")
os.environ.setdefault("GIGACHAT_API_KEY", "test-gigachat-key")
os.environ.setdefault("GIGACHAT_API_BASE", "https://gigachat.devices.sberbank.ru/api/v1")
os.environ.setdefault("GIGACHAT_MODEL", "openai/GigaChat-Pro")

from nano_vm.adapters.litellm_adapter import LiteLLMAdapter

from app.llm.providers import (
    gigachat_adapter,
    openrouter_adapter,
    yandexgpt_adapter,
)


class TestOpenRouterConfig:
    def test_model_format(self) -> None:
        assert re.match(
            r"^openrouter/[^/]+/[^:]+:free$",
            openrouter_adapter.model,
        ), f"Model {openrouter_adapter.model!r} does not match 'openrouter/vendor/model:free'"

    def test_stream_and_max_tokens(self) -> None:
        assert openrouter_adapter._extra.get("stream") is True
        assert openrouter_adapter._extra.get("max_tokens") == 8192

    def test_is_litellm_adapter(self) -> None:
        assert isinstance(openrouter_adapter, LiteLLMAdapter)

    def test_no_api_key_in_kwargs(self) -> None:
        assert "api_key" not in openrouter_adapter._extra

    def test_no_api_base_in_kwargs(self) -> None:
        assert "api_base" not in openrouter_adapter._extra


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
