import asyncio
import signal
import time
from dataclasses import replace
from typing import Optional, Sequence

from pydantic_ai.builtin_tools import WebSearchTool
from pydantic_ai.direct import model_request_stream
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings

from llm_cli.llm_types import ChatOptions, ModelCapabilities
from llm_cli.registry import ModelRegistry
from llm_cli.response_handler import ResponseHandler

MAX_CHAT_ATTEMPTS = 3
RETRY_WAIT_MIN_SECONDS = 4
RETRY_WAIT_MAX_SECONDS = 10


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

        capabilities = self._resolve_capabilities(
            model_name_or_alias,
            capabilities_override,
        )
        effective_options = self._normalize_options(options, capabilities)

        resolved_model_id = provider_model_id
        if effective_options.enable_search and provider_name == "openrouter":
            if not resolved_model_id.endswith(":online"):
                resolved_model_id = f"{resolved_model_id}:online"

        model_name = f"{provider_name}:{resolved_model_id}"

        # Start with extra_params from model config, then override with request-specific settings
        model_settings = dict(capabilities.extra_params)
        if capabilities.max_tokens is not None:
            model_settings.setdefault("max_tokens", capabilities.max_tokens)
        model_settings.update(effective_options.extra_settings)

        if effective_options.enable_thinking:
            if provider_name in {"openai", "openai-responses"}:
                model_settings.setdefault("openai_reasoning_summary", "detailed")
                model_settings.setdefault("openai_reasoning_effort", "medium")
            elif provider_name == "anthropic":
                model_settings.setdefault(
                    "anthropic_thinking",
                    {"type": "adaptive"},
                )
            elif provider_name in {"google-gla", "google-vertex"}:
                model_settings.setdefault(
                    "google_thinking_config",
                    {"include_thoughts": True},
                )

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
                response: Optional[ModelResponse] = (
                    self._stream_model_response_with_retry(
                        model_name,
                        model_messages,
                        model_settings_param,
                        request_parameters,
                        handler,
                    )
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

    def _resolve_capabilities(
        self,
        model_name_or_alias: str,
        capabilities_override: Optional[ModelCapabilities],
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

    def _stream_model_response_with_retry(
        self,
        model_name: str,
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
                        model_name,
                        model_messages,
                        model_settings,
                        request_parameters,
                        handler,
                    )
                )
            except Exception:
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
        model_name: str,
        model_messages: list[ModelMessage],
        model_settings: Optional[ModelSettings],
        request_parameters: ModelRequestParameters,
        handler: ResponseHandler,
    ) -> ModelResponse:
        """Stream model events via the async API and return the final response."""
        async with model_request_stream(
            model=model_name,
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
        "google-gla",
        "google-vertex",
        "xai",
    }

    def _build_request_parameters(
        self,
        provider_name: str,
        capabilities: ModelCapabilities,
        options: ChatOptions,
    ) -> ModelRequestParameters:
        """Create provider-specific request parameters (built-in tools, etc.)."""
        builtin_tools = []

        if (
            options.enable_search
            and capabilities.supports_search
            and provider_name in self._BUILTIN_SEARCH_PROVIDERS
        ):
            builtin_tools.append(WebSearchTool())

        return ModelRequestParameters(builtin_tools=builtin_tools)
