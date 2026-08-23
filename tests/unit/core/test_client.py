import asyncio
import time

import pytest
from unittest.mock import Mock
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import Model

from oi.core.client import LLMClient, _PreparedRequest, subscription_billing_active
from oi.llm_types import ChatOptions, ModelCapabilities
from oi.registry import ModelRegistry


def _model_supporting_subscription(registry: ModelRegistry, supported: bool) -> str:
    """A configured model whose supports_subscription matches, so subscription
    tests follow models.yaml instead of pinning a model that later gets replaced."""
    for name in registry.get_available_models():
        if registry.get_model_capabilities(name).supports_subscription is supported:
            return name
    pytest.fail(f"no model with supports_subscription={supported} in models.yaml")


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

    def test_search_sends_both_web_tools(self):
        """Fetching a URL rides along with --search; pydantic-ai drops the fetch
        tool on models that fold page-opening into their search tool."""
        client = LLMClient(Mock())
        options = ChatOptions(enable_search=True)
        capabilities = ModelCapabilities(supports_search=True)

        params = client._build_request_parameters("anthropic", capabilities, options)

        assert [type(tool).__name__ for tool in params.native_tools] == [
            "WebSearchTool",
            "WebFetchTool",
        ]
        assert params.native_tools[1].optional is True

    @pytest.mark.parametrize(
        "provider,supports_search,enable_search",
        [
            ("deepseek", True, True),  # provider has no builtin search
            ("anthropic", False, True),  # model can't search
            ("anthropic", True, False),  # --search not passed
        ],
    )
    def test_no_web_tools_without_search(
        self, provider, supports_search, enable_search
    ):
        client = LLMClient(Mock())

        params = client._build_request_parameters(
            provider,
            ModelCapabilities(supports_search=supports_search),
            ChatOptions(enable_search=enable_search),
        )

        assert params.native_tools == []

    def test_stream_model_response_with_retry_retries_before_output(self, monkeypatch):
        client = LLMClient(Mock())
        handler = Mock()
        handler.has_visible_output.return_value = False
        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr("oi.core.client.asyncio.sleep", fake_sleep)

        attempts = {"count": 0}
        result = object()

        async def fake_stream(*args, **kwargs):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("transient")
            return result

        client._stream_model_response = fake_stream  # type: ignore[method-assign]

        response = asyncio.run(
            client._stream_model_response_with_retry(
                "provider:model",
                [],
                None,
                Mock(),
                handler,
            )
        )

        assert response is result
        assert attempts["count"] == 3
        assert sleep_calls == [4, 8]

    def test_stream_model_response_with_retry_does_not_retry_after_output(self):
        client = LLMClient(Mock())
        handler = Mock()
        handler.has_visible_output.return_value = True

        attempts = {"count": 0}

        async def fake_stream(*args, **kwargs):
            attempts["count"] += 1
            raise RuntimeError("mid-stream failure")

        client._stream_model_response = fake_stream  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="mid-stream failure"):
            asyncio.run(
                client._stream_model_response_with_retry(
                    "provider:model",
                    [],
                    None,
                    Mock(),
                    handler,
                )
            )

        assert attempts["count"] == 1

    def test_run_prepared_captures_interrupted_turn_on_cancellation(self):
        from pydantic_ai.messages import NativeToolCallPart

        client = LLMClient(Mock())
        handler = Mock()
        handler.has_visible_output.return_value = True
        handler.partial_response = ModelResponse(
            parts=[
                TextPart(content="partial answer"),
                NativeToolCallPart(
                    tool_name="web_search", args={"query": "q"}, tool_call_id="c1"
                ),
            ]
        )
        prepared = _PreparedRequest(
            handler=handler,
            options=ChatOptions(),
            model_target="provider:model",
            model_settings=None,
            api_fallback=None,
            request_parameters=Mock(),
            messages=[],
        )

        async def cancelled_stream(prepared):
            raise asyncio.CancelledError()

        client._stream_with_fallback = cancelled_stream  # type: ignore[method-assign]

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(client._run_prepared(prepared))

        interrupt = client.take_interrupt()
        assert interrupt is not None
        assert interrupt.saw_output is True
        # The dangling native call is pruned; the text survives.
        partial = interrupt.partial_response
        assert partial is not None
        assert [type(part).__name__ for part in partial.parts] == ["TextPart"]
        assert partial.state == "interrupted"
        handler.mark_interrupted.assert_called_once()
        handler.finish_response.assert_called_once()
        # take_interrupt pops: a second read reports no interrupt.
        assert client.take_interrupt() is None

    def test_stream_model_response_snapshots_partial_on_cancellation(self, monkeypatch):
        import contextlib

        import pydantic_ai.direct

        client = LLMClient(Mock())
        handler = Mock()
        partial = ModelResponse(parts=[TextPart(content="so far")])

        class FakeStream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise asyncio.CancelledError()

            def get(self):
                return partial

        @contextlib.asynccontextmanager
        async def fake_request_stream(**kwargs):
            yield FakeStream()

        monkeypatch.setattr(
            pydantic_ai.direct, "model_request_stream", fake_request_stream
        )

        async def fake_resolve(target):
            return Mock(spec=Model)

        client._resolve_model = fake_resolve  # type: ignore[method-assign]

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                client._stream_model_response(
                    "provider:model", [], None, Mock(), handler
                )
            )

        assert handler.partial_response is partial

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

        async def fake_stream(
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

        async def fake_stream(
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

        async def fake_stream(
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

        async def fake_stream(
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

    @pytest.fixture
    def sub_model(self, registry):
        return _model_supporting_subscription(registry, True)

    @pytest.fixture
    def api_model(self, registry):
        return _model_supporting_subscription(registry, False)

    def test_none_for_non_subscription_model(self, registry, api_model, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_logged_in", lambda: True)
        assert subscription_billing_active(registry, api_model) is None

    def test_true_when_logged_in(self, registry, sub_model, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_logged_in", lambda: True)
        monkeypatch.delenv("OI_NO_SUBSCRIPTION", raising=False)
        assert subscription_billing_active(registry, sub_model) is True

    def test_false_when_not_logged_in(self, registry, sub_model, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_logged_in", lambda: False)
        monkeypatch.delenv("OI_NO_SUBSCRIPTION", raising=False)
        assert subscription_billing_active(registry, sub_model) is False

    def test_false_when_disabled_via_env(self, registry, sub_model, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_logged_in", lambda: True)
        monkeypatch.setenv("OI_NO_SUBSCRIPTION", "1")
        assert subscription_billing_active(registry, sub_model) is False


class TestResolveModel:
    """Building a model imports the provider SDK — it must not run on the loop."""

    def test_builds_off_the_loop_and_caches_per_loop(self, monkeypatch):
        client = LLMClient(Mock())
        built = []

        def slow_infer_model(target):
            time.sleep(0.05)
            built.append(target)
            return Mock(spec=Model)

        monkeypatch.setattr("pydantic_ai.models.infer_model", slow_infer_model)

        async def resolve_while_ticking():
            ticks = 0

            async def tick():
                nonlocal ticks
                while True:
                    ticks += 1
                    await asyncio.sleep(0.001)

            ticker = asyncio.create_task(tick())
            first = await client._resolve_model("anthropic:claude-x")
            second = await client._resolve_model("anthropic:claude-x")
            ticker.cancel()
            return first, second, ticks

        first, second, ticks = asyncio.run(resolve_while_ticking())

        assert built == ["anthropic:claude-x"], "model rebuilt on the second turn"
        assert first is second
        assert ticks > 1, "the event loop was blocked while the model was built"

        # A second loop can't reuse a client bound to the first one.
        asyncio.run(client._resolve_model("anthropic:claude-x"))
        assert built == ["anthropic:claude-x"] * 2

    def test_passes_model_instances_through(self):
        client = LLMClient(Mock())
        model = Mock(spec=Model)

        assert asyncio.run(client._resolve_model(model)) is model


class TestSubscriptionFallback:
    def _client_with_calls(self, monkeypatch, first_raises):
        client = LLMClient(Mock())
        calls = []
        api_response = ModelResponse(parts=[TextPart(content="api")])

        async def fake_retry(model, messages, settings, params, handler):
            calls.append(model)
            if len(calls) == 1 and first_raises is not None:
                raise first_raises
            return api_response

        monkeypatch.setattr(client, "_stream_model_response_with_retry", fake_retry)
        return client, calls, api_response

    def _prepared(self, handler) -> _PreparedRequest:
        return _PreparedRequest(
            handler=handler,
            options=ChatOptions(silent=True),
            model_target=Mock(),  # subscription model instance
            model_settings=None,
            api_fallback=("api", None),
            request_parameters=Mock(),
            messages=[],
        )

    def test_falls_back_to_api_on_exhaustion(self, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_exhausted", lambda: True)
        monkeypatch.setattr("oi.core.client.codex_auth.exhausted_until", lambda: 0.0)
        err = ModelHTTPError(status_code=429, model_name="m", body={"detail": "x"})
        client, calls, api_response = self._client_with_calls(monkeypatch, err)
        handler = Mock()
        handler.has_visible_output.return_value = False

        result = asyncio.run(client._stream_with_fallback(self._prepared(handler)))

        assert result is api_response
        assert calls[1] == "api"

    def test_reraises_when_not_exhausted(self, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_exhausted", lambda: False)
        err = ModelHTTPError(status_code=500, model_name="m", body=None)
        client, calls, _ = self._client_with_calls(monkeypatch, err)
        handler = Mock()
        handler.has_visible_output.return_value = False

        with pytest.raises(ModelHTTPError):
            asyncio.run(client._stream_with_fallback(self._prepared(handler)))
        assert len(calls) == 1

    def test_no_fallback_after_visible_output(self, monkeypatch):
        monkeypatch.setattr("oi.core.client.codex_auth.is_exhausted", lambda: True)
        err = ModelHTTPError(status_code=429, model_name="m", body={"quota": 1})
        client, calls, _ = self._client_with_calls(monkeypatch, err)
        handler = Mock()
        handler.has_visible_output.return_value = True

        with pytest.raises(ModelHTTPError):
            asyncio.run(client._stream_with_fallback(self._prepared(handler)))
        assert len(calls) == 1

    def test_exhausted_disables_billing_indicator(self, monkeypatch):
        registry = ModelRegistry()
        sub_model = _model_supporting_subscription(registry, True)
        monkeypatch.setattr("oi.core.client.codex_auth.is_logged_in", lambda: True)
        monkeypatch.delenv("OI_NO_SUBSCRIPTION", raising=False)
        monkeypatch.setattr("oi.core.client.codex_auth.is_exhausted", lambda: True)
        assert subscription_billing_active(registry, sub_model) is False
