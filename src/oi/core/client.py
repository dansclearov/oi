from __future__ import annotations

import asyncio
import os
import signal
import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Callable, Optional, Sequence

# pydantic_ai imports are function-local: its package __init__ costs ~600ms,
# which would otherwise land on every startup before the first prompt paints.
if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage, ModelResponse
    from pydantic_ai.models import Model, ModelRequestParameters
    from pydantic_ai.settings import ModelSettings

from oi.core import codex_auth
from oi.core.message_utils import prune_interrupted_response
from oi.llm_types import ChatOptions, ModelCapabilities
from oi.registry import ModelRegistry
from oi.renderers import ResponseRenderer
from oi.response_handler import ResponseHandler
from oi.ui.labels import INFO_LABEL, ansi_message

RendererFactory = Callable[[ModelCapabilities, ChatOptions], ResponseRenderer]

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


def _notify(options: ChatOptions, message: str) -> None:
    """Route an informational notice to the configured sink (stdout by default)."""
    if options.notify is not None:
        options.notify(message)
    else:
        print(ansi_message(INFO_LABEL, message))


@dataclass
class InterruptedTurn:
    """What an interrupt left behind, for the frontend to act on.

    `saw_output` is whether any output had reached the user when they pressed
    Ctrl+C: if so, the turn is kept in history (with `partial_response` when
    something storable arrived); if not, the message is unsent — dropped from
    history and returned to the input for review.
    """

    partial_response: Optional[ModelResponse]
    saw_output: bool


@dataclass
class _PreparedRequest:
    """Everything a single streaming turn needs, computed before it starts."""

    handler: ResponseHandler
    options: ChatOptions
    model_target: Model | str
    model_settings: Optional[ModelSettings]
    api_fallback: Optional[tuple[str, Optional[ModelSettings]]]
    request_parameters: ModelRequestParameters
    messages: list[ModelMessage]


class LLMClient:
    def __init__(self, registry: ModelRegistry):
        self.registry = registry
        self.interrupt_handler = None
        self.last_interrupt: Optional[InterruptedTurn] = None
        self._model_cache: dict[str, tuple[asyncio.AbstractEventLoop, Model]] = {}

    def take_interrupt(self) -> Optional[InterruptedTurn]:
        """Pop what the last interrupted turn left behind (None if not interrupted)."""
        interrupt, self.last_interrupt = self.last_interrupt, None
        return interrupt

    def chat(
        self,
        messages: Sequence[ModelMessage],
        model_name_or_alias: str,
        options: Optional[ChatOptions] = None,
        *,
        capabilities_override: Optional[ModelCapabilities] = None,
    ) -> ModelResponse:
        """Get response from the specified model (synchronous entry point).

        Runs the turn on its own event loop and maps Ctrl+C to an interrupt.
        The SIGINT handler is only installed on the main thread; background
        callers (e.g. smart titling from the TUI) just run without it.
        """
        prepared = self._prepare_request(
            messages, model_name_or_alias, options, capabilities_override
        )
        self.interrupt_handler = prepared.handler

        def handle_interrupt(signum, frame):
            if self.interrupt_handler:
                self.interrupt_handler.mark_interrupted()
            raise KeyboardInterrupt()

        on_main_thread = threading.current_thread() is threading.main_thread()
        old_handler = (
            signal.signal(signal.SIGINT, handle_interrupt) if on_main_thread else None
        )

        try:
            return asyncio.run(self._run_prepared(prepared))
        finally:
            if on_main_thread:
                signal.signal(signal.SIGINT, old_handler)
            self.interrupt_handler = None

    async def chat_async(
        self,
        messages: Sequence[ModelMessage],
        model_name_or_alias: str,
        options: Optional[ChatOptions] = None,
        *,
        capabilities_override: Optional[ModelCapabilities] = None,
        renderer_factory: Optional[RendererFactory] = None,
    ) -> ModelResponse:
        """Async entry point for callers that own an event loop (the TUI).

        No signal handling: interruption is task cancellation, which marks the
        response interrupted and finalizes rendering before propagating.
        """
        prepared = self._prepare_request(
            messages,
            model_name_or_alias,
            options,
            capabilities_override,
            renderer_factory=renderer_factory,
        )
        self.interrupt_handler = prepared.handler
        try:
            return await self._run_prepared(prepared)
        finally:
            self.interrupt_handler = None

    async def _run_prepared(self, prepared: _PreparedRequest) -> ModelResponse:
        """Stream a prepared turn, finalizing the renderer on every outcome."""
        handler = prepared.handler
        self.last_interrupt = None
        handler.start_response()
        try:
            response = await self._stream_with_fallback(prepared)
        except (asyncio.CancelledError, KeyboardInterrupt):
            # Sync path: Ctrl+C either cancels the task or (when the signal
            # lands while coroutine bytecode is running) raises straight through
            # it. Async path: the caller cancelled the turn.
            handler.mark_interrupted()
            partial = handler.partial_response
            self.last_interrupt = InterruptedTurn(
                partial_response=(
                    prune_interrupted_response(partial) if partial is not None else None
                ),
                saw_output=handler.has_visible_output(),
            )
            handler.finish_response()
            raise
        except Exception:
            handler.finish_response()
            raise
        handler.finish_response(response)
        return response

    def _prepare_request(
        self,
        messages: Sequence[ModelMessage],
        model_name_or_alias: str,
        options: Optional[ChatOptions],
        capabilities_override: Optional[ModelCapabilities],
        renderer_factory: Optional[RendererFactory] = None,
    ) -> _PreparedRequest:
        """Resolve model, settings, and rendering for a single turn."""
        from pydantic_ai.settings import ModelSettings

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
            _notify(effective_options, "Back on the ChatGPT subscription.")

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

        renderer = (
            renderer_factory(capabilities, effective_options)
            if renderer_factory is not None
            else None
        )
        handler = ResponseHandler(capabilities, effective_options, renderer=renderer)

        return _PreparedRequest(
            handler=handler,
            options=effective_options,
            model_target=model_target,
            model_settings=model_settings_param,
            api_fallback=api_fallback,
            request_parameters=request_parameters,
            # Always operate on ModelMessage history.
            messages=list(messages),
        )

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

    async def _resolve_model(self, target: Model | str) -> Model:
        """Build the pydantic-ai model for a turn, off the event loop.

        `infer_model` imports the provider SDK and constructs an HTTP client:
        a few hundred ms on the first turn of a run, tens of ms after. On the
        async path that runs on the frontend's loop, freezing the UI, so it
        goes to a thread. The cache is keyed by the loop that built the model
        because the HTTP client binds to it — the sync entry point runs each
        turn on a fresh loop, so it simply rebuilds. A long-lived loop (the
        TUI) reuses the model, which keeps its connection warm between turns.
        """
        from pydantic_ai.models import Model, infer_model

        if isinstance(target, Model):
            return target
        loop = asyncio.get_running_loop()
        cached = self._model_cache.get(target)
        if cached is not None and cached[0] is loop:
            return cached[1]
        model = await asyncio.to_thread(infer_model, target)
        self._model_cache[target] = (loop, model)
        return model

    async def _stream_with_fallback(self, prepared: _PreparedRequest) -> ModelResponse:
        """Stream on the subscription, falling back to the API key on exhaustion."""
        from pydantic_ai.exceptions import ModelHTTPError

        handler = prepared.handler
        try:
            return await self._stream_model_response_with_retry(
                prepared.model_target,
                prepared.messages,
                prepared.model_settings,
                prepared.request_parameters,
                handler,
            )
        except ModelHTTPError:
            if (
                prepared.api_fallback is None
                or handler.has_visible_output()
                or not codex_auth.is_exhausted()
            ):
                raise
            if not prepared.options.silent:
                until = time.strftime(
                    "%H:%M", time.localtime(codex_auth.exhausted_until())
                )
                _notify(
                    prepared.options,
                    "ChatGPT subscription limit reached — "
                    f"using your API key until {until}.",
                )
            api_model, api_settings = prepared.api_fallback
            return await self._stream_model_response_with_retry(
                api_model,
                prepared.messages,
                api_settings,
                prepared.request_parameters,
                handler,
            )

    async def _stream_model_response_with_retry(
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
                return await self._stream_model_response(
                    model,
                    model_messages,
                    model_settings,
                    request_parameters,
                    handler,
                )
            except Exception:
                # A subscription model (passed as an instance, not a string) that
                # just hit its limit shouldn't burn the retry budget — let the
                # caller fall back to the API key instead.
                if codex_auth.is_exhausted() and not isinstance(model, str):
                    raise
                if attempt >= MAX_CHAT_ATTEMPTS or handler.has_visible_output():
                    raise
                await asyncio.sleep(self._retry_wait_seconds(attempt))

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
        from pydantic_ai.direct import model_request_stream

        async with model_request_stream(
            model=await self._resolve_model(model),
            messages=model_messages,
            model_settings=model_settings,
            model_request_parameters=request_parameters,
        ) as stream:
            try:
                async for event in stream:
                    handler.handle_event(event)
            except (asyncio.CancelledError, KeyboardInterrupt):
                # Snapshot what arrived so an interrupted turn can keep its
                # partial response in history (CC-style).
                handler.partial_response = stream.get()
                raise
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
        from pydantic_ai.models import ModelRequestParameters
        from pydantic_ai.native_tools import WebFetchTool, WebSearchTool

        native_tools = []

        if (
            options.enable_search
            and capabilities.supports_search
            and provider_name in self._BUILTIN_SEARCH_PROVIDERS
        ):
            native_tools.append(WebSearchTool())
            # Anthropic and Google put reading a specific URL in a separate
            # tool; OpenAI's and xAI's web search opens pages itself. `optional`
            # lets pydantic-ai drop this on the models that don't take one.
            native_tools.append(WebFetchTool(optional=True))

        return ModelRequestParameters(native_tools=native_tools)
