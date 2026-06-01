import pytest
from unittest.mock import Mock
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart

from oi.core.client import LLMClient, subscription_billing_active
from oi.llm_types import ChatOptions, ModelCapabilities
from oi.registry import ModelRegistry


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
        monkeypatch.setattr("oi.core.client.time.sleep", sleep_calls.append)

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
        assert sleep_calls == [4, 8]

    def test_stream_model_response_with_retry_does_not_retry_after_output(
        self, monkeypatch
    ):
        client = LLMClient(Mock())
        handler = Mock()
        handler.has_visible_output.return_value = True
        monkeypatch.setattr("oi.core.client.time.sleep", Mock())

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

        resolved = client.resolve_capabilities("provider:model", override)

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

        resolved = client.resolve_capabilities("provider:model", override)

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

    def test_chat_defaults_anthropic_thinking_to_adaptive(self, monkeypatch):
        registry = Mock()
        registry.get_provider_for_model.return_value = ("anthropic", "claude-sonnet")
        registry.get_model_capabilities.return_value = ModelCapabilities(
            supports_thinking=True
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

        client.chat([], "sonnet", ChatOptions(silent=True))

        assert captured["model_settings"]["anthropic_thinking"] == {
            "type": "adaptive",
            "display": "summarized",
        }

    def test_chat_keeps_configured_anthropic_thinking_override(self, monkeypatch):
        registry = Mock()
        registry.get_provider_for_model.return_value = ("anthropic", "claude-haiku")
        registry.get_model_capabilities.return_value = ModelCapabilities(
            supports_thinking=True,
            extra_params={
                "anthropic_thinking": {"type": "enabled", "budget_tokens": 2048}
            },
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

        client.chat([], "haiku", ChatOptions(silent=True))

        assert captured["model_settings"]["anthropic_thinking"] == {
            "type": "enabled",
            "budget_tokens": 2048,
        }


class TestSubscriptionBillingActive:
    @pytest.fixture
    def registry(self):
        return ModelRegistry()

    def test_none_for_non_subscription_model(self, registry, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_logged_in", lambda: True)
        assert (
            subscription_billing_active(registry, "anthropic:claude-sonnet-4-6") is None
        )

    def test_true_when_logged_in(self, registry, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_logged_in", lambda: True)
        monkeypatch.delenv("OI_NO_SUBSCRIPTION", raising=False)
        assert subscription_billing_active(registry, "openai-responses:gpt-5.5") is True

    def test_false_when_not_logged_in(self, registry, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_logged_in", lambda: False)
        monkeypatch.delenv("OI_NO_SUBSCRIPTION", raising=False)
        assert (
            subscription_billing_active(registry, "openai-responses:gpt-5.5") is False
        )

    def test_false_when_disabled_via_env(self, registry, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_logged_in", lambda: True)
        monkeypatch.setenv("OI_NO_SUBSCRIPTION", "1")
        assert (
            subscription_billing_active(registry, "openai-responses:gpt-5.5") is False
        )


class TestSubscriptionFallback:
    def _client_with_calls(self, monkeypatch, first_raises):
        client = LLMClient(Mock())
        calls = []
        api_response = ModelResponse(parts=[TextPart(content="api")])

        def fake_retry(model, messages, settings, params, handler):
            calls.append(model)
            if len(calls) == 1 and first_raises is not None:
                raise first_raises
            return api_response

        monkeypatch.setattr(client, "_stream_model_response_with_retry", fake_retry)
        return client, calls, api_response

    def test_falls_back_to_api_on_exhaustion(self, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_exhausted", lambda: True)
        monkeypatch.setattr("oi.core.client.codex_auth.exhausted_until", lambda: 0.0)
        err = ModelHTTPError(
            status_code=429, model_name="gpt-5.5", body={"detail": "x"}
        )
        client, calls, api_response = self._client_with_calls(monkeypatch, err)
        handler = Mock()
        handler.has_visible_output.return_value = False

        result = client._stream_with_fallback(
            Mock(),  # subscription model instance
            None,
            ("openai-responses:gpt-5.5", None),
            [],
            Mock(),
            handler,
            ChatOptions(silent=True),
        )

        assert result is api_response
        assert calls[1] == "openai-responses:gpt-5.5"

    def test_reraises_when_not_exhausted(self, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_exhausted", lambda: False)
        err = ModelHTTPError(status_code=500, model_name="m", body=None)
        client, calls, _ = self._client_with_calls(monkeypatch, err)
        handler = Mock()
        handler.has_visible_output.return_value = False

        with pytest.raises(ModelHTTPError):
            client._stream_with_fallback(
                Mock(),
                None,
                ("api", None),
                [],
                Mock(),
                handler,
                ChatOptions(silent=True),
            )
        assert len(calls) == 1

    def test_no_fallback_after_visible_output(self, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_exhausted", lambda: True)
        err = ModelHTTPError(status_code=429, model_name="m", body={"quota": 1})
        client, calls, _ = self._client_with_calls(monkeypatch, err)
        handler = Mock()
        handler.has_visible_output.return_value = True

        with pytest.raises(ModelHTTPError):
            client._stream_with_fallback(
                Mock(),
                None,
                ("api", None),
                [],
                Mock(),
                handler,
                ChatOptions(silent=True),
            )
        assert len(calls) == 1

    def test_exhausted_disables_billing_indicator(self, monkeypatch):
        registry = ModelRegistry()
        monkeypatch.setattr("oi.core.client.codex_auth.is_logged_in", lambda: True)
        monkeypatch.delenv("OI_NO_SUBSCRIPTION", raising=False)
        monkeypatch.setattr("oi.core.client.codex_auth.is_exhausted", lambda: True)
        assert (
            subscription_billing_active(registry, "openai-responses:gpt-5.5") is False
        )
