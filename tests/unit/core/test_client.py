import pytest
from unittest.mock import Mock
from pydantic_ai.messages import ModelResponse, TextPart

from llm_cli.core.client import LLMClient
from llm_cli.llm_types import ChatOptions, ModelCapabilities


class TestLLMClient:
    def test_init(self):
        mock_registry = Mock()
        client = LLMClient(mock_registry)

        assert client.registry == mock_registry
        assert client.interrupt_handler is None

    def test_normalize_options_returns_copy_without_mutating_input(self):
        client = LLMClient(Mock())
        options = ChatOptions(
            enable_search=True,
            enable_thinking=True,
            extra_settings={"temperature": 0.2},
        )
        capabilities = ModelCapabilities(
            supports_search=False,
            supports_thinking=False,
        )

        effective = client._normalize_options(options, capabilities)

        assert effective is not options
        assert effective.enable_search is False
        assert effective.enable_thinking is False
        assert options.enable_search is True
        assert options.enable_thinking is True
        assert effective.extra_settings == {"temperature": 0.2}
        assert effective.extra_settings is not options.extra_settings

    def test_stream_model_response_with_retry_retries_before_output(self, monkeypatch):
        client = LLMClient(Mock())
        handler = Mock()
        handler.has_visible_output.return_value = False
        sleep_calls = []
        monkeypatch.setattr("llm_cli.core.client.time.sleep", sleep_calls.append)

        attempts = {"count": 0}
        result = object()

        async def fake_stream(*args, **kwargs):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("transient")
            return result

        client._stream_model_response = fake_stream  # type: ignore[method-assign]

        response = client._stream_model_response_with_retry(
            "provider:model",
            [],
            None,
            Mock(),
            handler,
        )

        assert response is result
        assert attempts["count"] == 3
        assert sleep_calls == [4, 4]

    def test_stream_model_response_with_retry_does_not_retry_after_output(
        self, monkeypatch
    ):
        client = LLMClient(Mock())
        handler = Mock()
        handler.has_visible_output.return_value = True
        monkeypatch.setattr("llm_cli.core.client.time.sleep", Mock())

        attempts = {"count": 0}

        async def fake_stream(*args, **kwargs):
            attempts["count"] += 1
            raise RuntimeError("mid-stream failure")

        client._stream_model_response = fake_stream  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="mid-stream failure"):
            client._stream_model_response_with_retry(
                "provider:model",
                [],
                None,
                Mock(),
                handler,
            )

        assert attempts["count"] == 1

    def test_resolve_capabilities_uses_config_when_available(self):
        registry = Mock()
        configured = ModelCapabilities(supports_search=True)
        override = ModelCapabilities(supports_thinking=True)
        registry.has_model_config.return_value = True
        registry.get_model_capabilities.return_value = configured
        client = LLMClient(registry)

        resolved = client._resolve_capabilities("provider:model", override)

        assert resolved is configured
        registry.get_model_capabilities.assert_called_once_with("provider:model")

    def test_resolve_capabilities_uses_snapshot_when_model_config_missing(self):
        registry = Mock()
        override = ModelCapabilities(
            supports_search=False,
            supports_thinking=True,
            extra_params={"foo": "bar"},
        )
        registry.has_model_config.return_value = False
        client = LLMClient(registry)

        resolved = client._resolve_capabilities("provider:model", override)

        assert resolved is override
        registry.get_model_capabilities.assert_not_called()

    def test_chat_applies_configured_max_tokens_to_model_settings(self, monkeypatch):
        registry = Mock()
        registry.get_provider_for_model.return_value = ("anthropic", "claude-sonnet")
        registry.get_model_capabilities.return_value = ModelCapabilities(
            max_tokens=8192
        )
        client = LLMClient(registry)
        captured = {}
        response = ModelResponse(parts=[TextPart(content="ok")])

        def fake_stream(
            model_name, model_messages, model_settings, request_parameters, handler
        ):
            captured["model_name"] = model_name
            captured["model_settings"] = model_settings
            return response

        monkeypatch.setattr(client, "_stream_model_response_with_retry", fake_stream)

        result = client.chat([], "sonnet", ChatOptions(silent=True))

        assert result is response
        assert captured["model_name"] == "anthropic:claude-sonnet"
        assert captured["model_settings"]["max_tokens"] == 8192

    def test_chat_request_overrides_configured_max_tokens(self, monkeypatch):
        registry = Mock()
        registry.get_provider_for_model.return_value = ("anthropic", "claude-sonnet")
        registry.get_model_capabilities.return_value = ModelCapabilities(
            max_tokens=8192
        )
        client = LLMClient(registry)
        captured = {}
        response = ModelResponse(parts=[TextPart(content="ok")])

        def fake_stream(
            model_name, model_messages, model_settings, request_parameters, handler
        ):
            captured["model_settings"] = model_settings
            return response

        monkeypatch.setattr(client, "_stream_model_response_with_retry", fake_stream)

        client.chat(
            [],
            "sonnet",
            ChatOptions(silent=True, extra_settings={"max_tokens": 12000}),
        )

        assert captured["model_settings"]["max_tokens"] == 12000
