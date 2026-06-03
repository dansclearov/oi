import asyncio
import os
import signal
import time
from dataclasses import replace
from typing import Optional, Sequence

from pydantic_ai.direct import model_request_stream
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings

from oi.core import codex_auth
from oi.llm_types import ChatOptions, ModelCapabilities
from oi.registry import ModelRegistry
from oi.response_handler import ResponseHandler
from oi.ui.labels import INFO_LABEL, ansi_message

MAX_CHAT_ATTEMPTS = 3
RETRY_WAIT_MIN_SECONDS = 4
RETRY_WAIT_MAX_SECONDS = 10


def _subscription_disabled() -> bool:
    """True when the user has opted out of subscription billing via env."""
    return os.environ.get("OI_NO_SUBSCRIPTION", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _supports_subscription(provider_name: str, capabilities: ModelCapabilities) -> bool:
    return provider_name == "openai-responses" and capabilities.supports_subscription


def _use_subscription(provider_name: str, capabilities: ModelCapabilities) -> bool:
    return (
        _supports_subscription(provider_name, capabilities)
        and codex_auth.is_logged_in()
        and not _subscription_disabled()
    )


def subscription_billing_active(
    registry: ModelRegistry, model_name_or_alias: str
) -> Optional[bool]:
    """Whether a model bills to the subscription.

    None for models with no subscription option (callers omit any indicator),
    True when subscription billing is active, False when a subscription-capable
    model falls back to the API key.
    """
    provider_name, _ = registry.get_provider_for_model(model_name_or_alias)
    capabilities = registry.get_model_capabilities(model_name_or_alias)
    if not _supports_subscription(provider_name, capabilities):
        return None
    return (
        _use_subscription(provider_name, capabilities) and not codex_auth.is_exhausted()
    )


class LLMClient:
    def __init__(self, registry: ModelRegistry):
        self.registry = registry
        self.interrupt_handler = None

    def chat(
        self,
        messages: Sequence[ModelMessage],
        model_name_or_alias: str,
        options: Optional[ChatOptions] = None,
        *,
        capabilities_override: Optional[ModelCapabilities] = None,
    ) -> ModelResponse:
        """Get response from the specified model."""
        if options is None:
            options = ChatOptions()

        provider_name, provider_model_id = self.registry.get_provider_for_model(
            model_name_or_alias
        )

        capabilities = self.resolve_capabilities(
            model_name_or_alias,
            capabilities_override,
        )
        effective_options = self._normalize_options(options, capabilities)

        resolved_model_id = provider_model_id
        if effective_options.enable_search and provider_name == "openrouter":
            if not resolved_model_id.endswith(":online"):
                resolved_model_id = f"{resolved_model_id}:online"

        model_name = f"{provider_name}:{resolved_model_id}"

        use_subscription = (
            _use_subscription(provider_name, capabilities)
            and not codex_auth.is_exhausted()
        )

        if (
            use_subscription
            and codex_auth.consume_recovery()
            and not effective_options.silent
        ):
            print(ansi_message(INFO_LABEL, "Back on the ChatGPT subscription."))

        # Start with extra_params from model config, then override with request-specific settings
        model_settings = dict(capabilities.extra_params)
        if capabilities.max_tokens is not None:
            model_settings.setdefault("max_tokens", capabilities.max_tokens)
        model_settings.update(effective_options.extra_settings)

        if use_subscription:
            # The Codex backend rejects stored responses.
            model_settings["openai_store"] = False

        if effective_options.enable_thinking:
            if provider_name in {"openai", "openai-responses"}:
                model_settings.setdefault("openai_reasoning_summary", "detailed")
                model_settings.setdefault("openai_reasoning_effort", "medium")
            elif provider_name == "anthropic":
                model_settings.setdefault(
                    "anthropic_thinking",
                    {"type": "adaptive", "display": "summarized"},
                )
            elif provider_name in {"google", "google-cloud"}:
                model_settings.setdefault(
                    "google_thinking_config",
                    {"include_thoughts": True},
                )
        elif provider_name == "anthropic":
            # A model can pin a thinking budget in its extra_params (e.g. Haiku,
            # which has no adaptive mode), and extra_params are merged
            # unconditionally — so disable explicitly to honor enable_thinking.
            model_settings["anthropic_thinking"] = {"type": "disabled"}

        if effective_options.enable_search:
            self._apply_search_settings(
                provider_name, provider_model_id, model_settings
            )

        model_settings_param = ModelSettings(model_settings) if model_settings else None
        request_parameters = self._build_request_parameters(
            provider_name,
            capabilities,
            effective_options,
        )

        model_target: Model | str = (
            self._build_subscription_model(resolved_model_id)
            if use_subscription
            else model_name
        )

        # When billing to the subscription, prepare an API-key fallback for the
        # current turn in case the subscription is found exhausted mid-request.
        api_fallback: Optional[tuple[str, Optional[ModelSettings]]] = None
        if use_subscription:
            api_settings = {
                k: v for k, v in model_settings.items() if k != "openai_store"
            }
            api_fallback = (
                model_name,
                ModelSettings(api_settings) if api_settings else None,
            )

        handler = ResponseHandler(capabilities, effective_options)
        self.interrupt_handler = handler

        # Always operate on ModelMessage history.
        model_messages = list(messages)

        def handle_interrupt(signum, frame):
            if self.interrupt_handler:
                self.interrupt_handler.mark_interrupted()
            raise KeyboardInterrupt()

        old_handler = signal.signal(signal.SIGINT, handle_interrupt)

        try:
            handler.start_response()
            try:
                response: Optional[ModelResponse] = self._stream_with_fallback(
                    model_target,
                    model_settings_param,
                    api_fallback,
                    model_messages,
                    request_parameters,
                    handler,
                    effective_options,
                )
            except KeyboardInterrupt:
                handler.finish_response()
                raise
            except Exception:
                handler.finish_response()
                raise
            handler.finish_response(response)
            return response
        finally:
            signal.signal(signal.SIGINT, old_handler)
            self.interrupt_handler = None

    def resolve_capabilities(
        self,
        model_name_or_alias: str,
        capabilities_override: Optional[ModelCapabilities] = None,
    ) -> ModelCapabilities:
        """Use chat snapshot only when the model no longer has config."""
        if capabilities_override is not None and not self.registry.has_model_config(
            model_name_or_alias
        ):
            return capabilities_override

        return self.registry.get_model_capabilities(model_name_or_alias)

    def _normalize_options(
        self, options: ChatOptions, capabilities: ModelCapabilities
    ) -> ChatOptions:
        """Return a request-local options object constrained by model capabilities."""
        effective_options = replace(
            options, extra_settings=dict(options.extra_settings)
        )

        if effective_options.enable_search and not capabilities.supports_search:
            effective_options.enable_search = False

        if effective_options.enable_thinking and not capabilities.supports_thinking:
            effective_options.enable_thinking = False

        return effective_options

    def _build_subscription_model(self, provider_model_id: str) -> Model:
        """Build a Responses model that bills to the ChatGPT subscription."""
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        access_token, account_id = codex_auth.get_access_token()
        http_client = codex_auth.build_async_client(access_token, account_id)
        provider = OpenAIProvider(
            base_url=codex_auth.CODEX_BASE_URL,
            api_key="unused",
            http_client=http_client,
        )
        return OpenAIResponsesModel(provider_model_id, provider=provider)

    def _stream_with_fallback(
        self,
        primary_model: Model | str,
        primary_settings: Optional[ModelSettings],
        api_fallback: Optional[tuple[str, Optional[ModelSettings]]],
        model_messages: list[ModelMessage],
        request_parameters: ModelRequestParameters,
        handler: ResponseHandler,
        options: ChatOptions,
    ) -> ModelResponse:
        """Stream on the subscription, falling back to the API key on exhaustion."""
        try:
            return self._stream_model_response_with_retry(
                primary_model,
                model_messages,
                primary_settings,
                request_parameters,
                handler,
            )
        except ModelHTTPError:
            if (
                api_fallback is None
                or handler.has_visible_output()
                or not codex_auth.is_exhausted()
            ):
                raise
            if not options.silent:
                until = time.strftime(
                    "%H:%M", time.localtime(codex_auth.exhausted_until())
                )
                print(
                    ansi_message(
                        INFO_LABEL,
                        "ChatGPT subscription limit reached — "
                        f"using your API key until {until}.",
                    )
                )
            api_model, api_settings = api_fallback
            return self._stream_model_response_with_retry(
                api_model,
                model_messages,
                api_settings,
                request_parameters,
                handler,
            )

    def _stream_model_response_with_retry(
        self,
        model: Model | str,
        model_messages: list[ModelMessage],
        model_settings: Optional[ModelSettings],
        request_parameters: ModelRequestParameters,
        handler: ResponseHandler,
    ) -> ModelResponse:
        """Retry transient failures only before any streamed output is shown."""
        for attempt in range(1, MAX_CHAT_ATTEMPTS + 1):
            try:
                return asyncio.run(
                    self._stream_model_response(
                        model,
                        model_messages,
                        model_settings,
                        request_parameters,
                        handler,
                    )
                )
            except Exception:
                # A subscription model (passed as an instance, not a string) that
                # just hit its limit shouldn't burn the retry budget — let the
                # caller fall back to the API key instead.
                if codex_auth.is_exhausted() and not isinstance(model, str):
                    raise
                if attempt >= MAX_CHAT_ATTEMPTS or handler.has_visible_output():
                    raise
                time.sleep(self._retry_wait_seconds(attempt))

        raise RuntimeError("unreachable")

    def _retry_wait_seconds(self, attempt: int) -> int:
        """Exponential backoff for retries after an attempt number (1-indexed)."""
        delay = RETRY_WAIT_MIN_SECONDS * (2 ** (attempt - 1))
        return min(delay, RETRY_WAIT_MAX_SECONDS)

    async def _stream_model_response(
        self,
        model: Model | str,
        model_messages: list[ModelMessage],
        model_settings: Optional[ModelSettings],
        request_parameters: ModelRequestParameters,
        handler: ResponseHandler,
    ) -> ModelResponse:
        """Stream model events via the async API and return the final response."""
        async with model_request_stream(
            model=model,
            messages=model_messages,
            model_settings=model_settings,
            model_request_parameters=request_parameters,
        ) as stream:
            async for event in stream:
                handler.handle_event(event)
            return stream.get()

    def _apply_search_settings(
        self,
        provider_name: str,
        provider_model_id: str,
        model_settings: dict,
    ) -> None:
        """Apply provider-specific settings that enable search features."""
        if provider_name == "openrouter":
            self._enable_openrouter_web_plugin(model_settings)

    def _enable_openrouter_web_plugin(self, model_settings: dict) -> None:
        """Attach OpenRouter's `web` plugin to the request extra body."""
        extra_body = model_settings.setdefault("extra_body", {})
        plugins = extra_body.setdefault("plugins", [])
        if not any(
            isinstance(plugin, dict) and plugin.get("id") == "web" for plugin in plugins
        ):
            plugins.append({"id": "web"})

    _BUILTIN_SEARCH_PROVIDERS = {
        "anthropic",
        "openai-responses",
        "google",
        "google-cloud",
        "xai",
    }

    def _build_request_parameters(
        self,
        provider_name: str,
        capabilities: ModelCapabilities,
        options: ChatOptions,
    ) -> ModelRequestParameters:
        """Create provider-specific request parameters (native tools, etc.)."""
        native_tools = []

        if (
            options.enable_search
            and capabilities.supports_search
            and provider_name in self._BUILTIN_SEARCH_PROVIDERS
        ):
            native_tools.append(WebSearchTool())

        return ModelRequestParameters(native_tools=native_tools)
