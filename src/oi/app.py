"""Main application orchestration."""

import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Sequence

from dotenv import load_dotenv
from platformdirs import user_config_dir, user_data_dir

from oi.cli import parse_arguments
from oi.config.settings import Config, load_env_file, update_user_config
from oi.constants import (
    MAX_TITLE_LENGTH,
    MIN_MESSAGES_FOR_SMART_TITLE,
    SMART_TITLE_API_KEY_ENV,
    SMART_TITLE_MODEL,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    UserPromptPart,
)

from oi.core.chat_manager import ChatManager
from oi.core.client import LLMClient, subscription_billing_active
from oi.core.message_utils import (
    count_non_system_messages,
    flatten_history,
    latest_system_prompt,
)
from oi.core.session import Chat
from oi.exceptions import (
    ChatNotFoundError,
    ModelNotFoundError,
    PromptNotFoundError,
)
from oi.local_commands import (
    LOCAL_COMMANDS,
    build_argument_error_message,
    build_unknown_command_message,
    parse_local_command,
)
from oi.prompts import read_system_message_from_file
from oi.llm_types import ChatOptions
from oi.registry import ModelRegistry
from oi.ui.input_handler import InputHandler
from oi.ui.labels import (
    AI_LABEL,
    BTW_AI_LABEL_TEXT,
    ERROR_LABEL,
    INFO_LABEL,
    SYSTEM_LABEL,
    USER_LABEL,
    WARNING_LABEL,
    ansi_message,
    ansi_pill,
)

load_dotenv()


def print_user_paths() -> None:
    """Print all user path locations used by the application."""
    config_dir = Path(user_config_dir("oi", ensure_exists=True))
    data_dir = Path(user_data_dir("oi", ensure_exists=True))

    # Configuration directory
    print(f"Configuration directory: {config_dir}")
    print(f"  - User config file: {config_dir / 'config.json'}")
    print(f"  - User prompts: {config_dir / 'prompts'}/ (*.txt files)")
    print(f"  - User model overrides: {config_dir / 'models.yaml'}")
    print(f"  - API keys: {config_dir / 'env'} (overrides global environment)")

    # Data directory
    chat_dir = os.getenv("OI_CHAT_DIR", str(data_dir / "chats"))
    print(f"Data directory: {data_dir}")
    print(f"  - Chat storage: {chat_dir}")

    # Environment variable overrides
    print("\nEnvironment variable overrides:")
    print(f"  - OI_CHAT_DIR: {os.getenv('OI_CHAT_DIR', 'not set')}")

    # Show which paths currently exist
    print("\nCurrent status:")
    paths_to_check = [
        config_dir,
        config_dir / "config.json",
        config_dir / "prompts",
        config_dir / "models.yaml",
        config_dir / "env",
        Path(chat_dir),
    ]

    for path in paths_to_check:
        exists = "✓" if path.exists() else "✗"
        print(f"  {exists} {path}")


def print_all_messages(messages: Sequence[ModelMessage]) -> None:
    """Print all messages in the conversation history."""
    for role, content in flatten_history(messages, image_wrap=ansi_pill):
        label = USER_LABEL if role == "user" else AI_LABEL
        print(ansi_message(label, content))


@dataclass
class ChatLoopContext:
    """Groups the state needed by the chat loop."""

    config: Config
    chat_manager: ChatManager
    llm_client: LLMClient
    input_handler: InputHandler
    chat_options: ChatOptions
    prompt_str: str
    ephemeral: bool = False
    active_model: str = ""


def setup_configuration(args, registry) -> ChatLoopContext:
    """Set up configuration and components."""
    config = Config()

    chat_options = ChatOptions(
        enable_search=args.search,
        enable_thinking=not args.no_thinking,
        show_thinking=not args.no_thinking and not args.hide_thinking,
        show_assistant_label=args.prompt is None,
    )

    prompt_str = read_system_message_from_file("prompt_" + args.system_prompt + ".txt")

    return ChatLoopContext(
        config=config,
        chat_manager=ChatManager(config),
        llm_client=LLMClient(registry),
        input_handler=InputHandler(config),
        chat_options=chat_options,
        prompt_str=prompt_str,
        ephemeral=args.ephemeral,
    )


def handle_chat_selection(
    args, chat_manager: ChatManager, *, quiet: bool = False
) -> Optional[Chat]:
    """Handle chat selection/loading based on arguments."""
    current_chat: Optional[Chat] = None

    if args.resume is not None:
        if args.resume:  # Specific chat ID provided
            try:
                current_chat = chat_manager.load_chat(args.resume)
            except (ChatNotFoundError, FileNotFoundError):
                print(ansi_message(ERROR_LABEL, f"Chat not found: {args.resume}"))
                sys.exit(1)
        else:  # No ID provided, show selector
            current_chat = chat_manager.interactive_chat_selection()
            if current_chat is None:
                # User cancelled, exit
                sys.exit(0)
    elif args.continue_chat:
        # Continue most recent chat
        current_chat = chat_manager.get_last_chat()
        if not current_chat and not quiet:
            print(
                ansi_message(
                    INFO_LABEL, "No previous chats found. Starting new chat..."
                )
            )

    return current_chat


def _billing_tag(registry: ModelRegistry, model_name: str) -> str:
    """A minimal ` (sub)`/` (api)` billing suffix shown on every chat.

    ` (sub)` only when a model bills to the subscription; ` (api)` otherwise,
    including models with no subscription option.
    """
    return " (sub)" if subscription_billing_active(registry, model_name) else " (api)"


def _print_chat_session_context(
    current_chat: Chat, prompt_str: str, source_tag: str = ""
) -> None:
    """Print startup context for new or resumed chats."""
    history = flatten_history(current_chat.messages)
    has_user_messages = any(role == "user" for role, _ in history)

    if not has_user_messages:
        print(
            ansi_message(
                INFO_LABEL,
                f"Starting new {current_chat.metadata.model}{source_tag} chat session. "
                "Press Ctrl+C to exit. Use Shift+Enter for new lines.",
            )
        )
        print(ansi_message(SYSTEM_LABEL, prompt_str))
        return

    system_message = latest_system_prompt(current_chat.messages) or ""
    if system_message != prompt_str:
        print(
            ansi_message(
                SYSTEM_LABEL,
                system_message,
                label_text="System (from chat): ",
            )
        )
    else:
        print(ansi_message(SYSTEM_LABEL, prompt_str))

    print_all_messages(current_chat.messages)


def _handle_local_command(
    normalized_input: str,
    ctx: "ChatLoopContext",
    current_chat: Chat,
) -> bool:
    """Handle local slash commands. Returns True when handled."""
    parsed_command = parse_local_command(normalized_input)
    if parsed_command is None:
        return False

    command_name, command_args = parsed_command
    if command_name not in LOCAL_COMMANDS:
        print(ansi_message(WARNING_LABEL, build_unknown_command_message(command_name)))
        return True

    if command_name == "/btw":
        question = command_args.strip()
        if not question:
            print(
                ansi_message(
                    INFO_LABEL,
                    "Usage: /btw <question> — ask a one-off question with the full "
                    "conversation as context. Nothing is saved to the chat.",
                )
            )
            return True
        _run_side_question(question, current_chat, ctx)
        return True

    if command_args:
        print(ansi_message(WARNING_LABEL, build_argument_error_message(command_name)))
        return True

    if command_name == "/vim":
        ctx.config.vim_mode = not ctx.config.vim_mode
        update_user_config("vim_mode", ctx.config.vim_mode)
        status = "enabled" if ctx.config.vim_mode else "disabled"
        print(ansi_message(INFO_LABEL, f"Vim mode {status}."))
        return True

    if command_name == "/bookmark":
        if not current_chat.should_be_saved():
            print(
                ansi_message(
                    WARNING_LABEL,
                    "Bookmarking is available after the first saved exchange.",
                )
            )
            return True

        bookmarked = ctx.chat_manager.toggle_bookmark(current_chat)
        if bookmarked is not None:
            action = "Bookmarked" if bookmarked else "Removed bookmark from"
            print(
                ansi_message(
                    INFO_LABEL, f"{action} chat: {current_chat.metadata.title}"
                )
            )
    return True


def _run_side_question(
    question: str, current_chat: Chat, ctx: "ChatLoopContext"
) -> None:
    """Answer a one-off `/btw` question with full context, persisting nothing.

    Runs a normal streamed turn against a throwaway copy of the history so the
    model sees everything so far, but neither the question nor the answer is
    appended to the chat or saved. The answer renders under an `AI (btw): `
    label. Search/thinking follow the session's options.
    """
    side_messages = list(current_chat.messages)
    parts: list = []
    # On a brand-new chat the system prompt is still pending (not yet in
    # history); include it transiently without consuming it.
    if current_chat.pending_system_prompt:
        parts.append(SystemPromptPart(current_chat.pending_system_prompt))
    parts.append(UserPromptPart(question))
    side_messages.append(ModelRequest(parts=parts))

    options = replace(ctx.chat_options, assistant_label_text=BTW_AI_LABEL_TEXT)
    capabilities_override = current_chat.metadata.get_model_capabilities_snapshot()

    try:
        ctx.llm_client.chat(
            side_messages,
            ctx.active_model,
            options,
            capabilities_override=capabilities_override,
        )
    except KeyboardInterrupt:
        # Ctrl+C cancels just the side question; the main chat is untouched.
        # Catch it here so it doesn't reach the loop's idle-exit handler.
        print("", flush=True)
    except Exception as exc:
        print(
            ansi_message(
                ERROR_LABEL,
                f"Request failed: {type(exc).__name__}: {exc}",
            )
        )


def _update_title_from_first_user_message(current_chat: Chat) -> None:
    """Replace placeholder title with the first user message."""
    if not current_chat.metadata.title.startswith("Chat "):
        return

    user_messages = [
        content
        for role, content in flatten_history(current_chat.messages)
        if role == "user"
    ]
    if len(user_messages) != 1:
        return

    first_msg = user_messages[0]
    current_chat.metadata.title = first_msg.replace("\n", " ").strip()[
        :MAX_TITLE_LENGTH
    ]


def _maybe_generate_smart_title(
    current_chat: Chat,
    chat_manager: ChatManager,
    llm_client: LLMClient,
) -> None:
    """Generate a smart title once enough conversation exists.

    Titles use a fixed cheap model (`SMART_TITLE_MODEL`) instead of the chat's
    active model. When that model's API key isn't configured we skip generation
    and keep the first-message title rather than billing the active model.
    """
    non_system_count = count_non_system_messages(current_chat.messages)
    should_generate_title = (
        non_system_count >= MIN_MESSAGES_FOR_SMART_TITLE
        and not current_chat.metadata.smart_title_generated
    )
    if not should_generate_title:
        return
    if not os.environ.get(SMART_TITLE_API_KEY_ENV):
        return
    chat_manager.generate_smart_title(current_chat, llm_client, SMART_TITLE_MODEL)


def _warn_if_response_hit_output_limit(model_response: ModelResponse) -> None:
    """Surface provider-side output truncation so it isn't mistaken for UI cutoff."""
    if model_response.finish_reason != "length":
        return

    print(
        ansi_message(
            WARNING_LABEL,
            "Response hit the model output limit. Set `max_tokens` for this model "
            "in models.yaml if you want longer replies.",
        )
    )


def run_chat_loop(current_chat: Chat, ctx: ChatLoopContext) -> None:
    """Run the main chat interaction loop."""
    source_tag = _billing_tag(ctx.llm_client.registry, ctx.active_model)
    _print_chat_session_context(current_chat, ctx.prompt_str, source_tag)
    capabilities_override = current_chat.metadata.get_model_capabilities_snapshot()
    active_capabilities = ctx.llm_client.resolve_capabilities(
        ctx.active_model, capabilities_override
    )

    # Main interaction loop
    is_idle = True
    while True:
        pending_user_message = False
        try:
            user_input = ctx.input_handler.get_user_input(active_capabilities)

            if isinstance(user_input, str):
                normalized_input = user_input.strip()
                if not normalized_input:
                    continue

                if _handle_local_command(normalized_input, ctx, current_chat):
                    continue

            # Process normal input
            current_chat.append_user_message(user_input)
            pending_user_message = True

            is_idle = False
            try:
                model_response = ctx.llm_client.chat(
                    current_chat.messages,
                    ctx.active_model,
                    ctx.chat_options,
                    capabilities_override=capabilities_override,
                )
            except KeyboardInterrupt:
                _discard_pending_user_message(current_chat)
                pending_user_message = False
                raise
            except Exception as exc:
                _discard_pending_user_message(current_chat)
                pending_user_message = False
                is_idle = True
                print(
                    ansi_message(
                        ERROR_LABEL,
                        f"Request failed: {type(exc).__name__}: {exc}",
                    )
                )
                continue

            current_chat.append_assistant_response(model_response)
            _warn_if_response_hit_output_limit(model_response)
            pending_user_message = False

            if not ctx.ephemeral:
                _update_title_from_first_user_message(current_chat)
                ctx.chat_manager.save_chat(current_chat)
                _maybe_generate_smart_title(
                    current_chat, ctx.chat_manager, ctx.llm_client
                )

            is_idle = True

        except KeyboardInterrupt:
            if not is_idle:
                if pending_user_message:
                    _discard_pending_user_message(current_chat)
                is_idle = True
                print("", flush=True)
            else:
                # Touch the chat on exit so `oi -c` reopens the one you just
                # closed, even if you only re-read it without sending anything.
                # save_chat() bumps updated_at (which drives -c ordering) and
                # skips empty chats via should_be_saved().
                if not ctx.ephemeral:
                    ctx.chat_manager.save_chat(current_chat)
                break


def run_headless_turn(
    args,
    ctx: ChatLoopContext,
    registry: ModelRegistry,
) -> None:
    """Send a single message, print the response, and exit."""
    if args.resume == "":
        print(
            ansi_message(
                ERROR_LABEL,
                "Headless mode (-p) requires a chat ID with -r; the interactive selector is unavailable.",
            )
        )
        sys.exit(2)

    current_chat = handle_chat_selection(args, ctx.chat_manager, quiet=True)
    requested_model = registry.resolve_model_name(
        args.model or registry.get_default_model()
    )

    if current_chat is None:
        current_chat = ctx.chat_manager.create_new_chat(requested_model, ctx.prompt_str)
        current_chat.metadata.set_model_capabilities_snapshot(
            registry.get_model_capabilities(requested_model)
        )

    ctx.active_model = current_chat.metadata.model
    capabilities_override = current_chat.metadata.get_model_capabilities_snapshot()

    current_chat.append_user_message(args.prompt)
    try:
        model_response = ctx.llm_client.chat(
            current_chat.messages,
            ctx.active_model,
            ctx.chat_options,
            capabilities_override=capabilities_override,
        )
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(
            ansi_message(
                ERROR_LABEL,
                f"Request failed: {type(exc).__name__}: {exc}",
            )
        )
        sys.exit(1)

    current_chat.append_assistant_response(model_response)
    _warn_if_response_hit_output_limit(model_response)

    if ctx.ephemeral:
        return

    _update_title_from_first_user_message(current_chat)
    ctx.chat_manager.save_chat(current_chat)
    _maybe_generate_smart_title(current_chat, ctx.chat_manager, ctx.llm_client)


def _discard_pending_user_message(current_chat: Chat) -> None:
    """Drop a trailing user request when generation fails or is interrupted."""
    if not current_chat.messages:
        return

    if isinstance(current_chat.messages[-1], ModelRequest):
        current_chat.messages.pop()


def run_stats(args) -> None:
    """Collect and render usage statistics, then exit."""
    # Heavy imports are local so the chat path doesn't pay for them.
    from oi.core.stats import StatsCollector
    from oi.ui.stats_view import render_stats

    manager = ChatManager(Config())
    if args.deep:
        print(
            ansi_message(
                INFO_LABEL, "Scanning transcripts (this may take a moment)..."
            ),
            flush=True,
        )
    render_stats(StatsCollector(manager).collect(deep=args.deep))


def _print_openai_auth_status() -> None:
    """Print whether an OpenAI subscription login is active."""
    from oi.core import codex_auth

    creds = codex_auth.load_credentials()
    if creds is None:
        print(ansi_message(INFO_LABEL, "OpenAI: not logged in (using API key)."))
        return
    who = creds.email or creds.account_id or "unknown account"
    print(ansi_message(INFO_LABEL, f"OpenAI: logged in as {who}."))


def run_auth(args) -> None:
    """Handle `oi auth ...` provider login/logout/status, then exit."""
    from oi.core import codex_auth
    from oi.exceptions import CodexAuthError

    provider = getattr(args, "auth_provider", None)
    if provider is None:
        _print_openai_auth_status()
        return

    if provider == "openai":
        action = getattr(args, "action", "login")
        if action == "status":
            _print_openai_auth_status()
            return
        if action == "logout":
            removed = codex_auth.logout()
            msg = "Logged out of OpenAI subscription." if removed else "Not logged in."
            print(ansi_message(INFO_LABEL, msg))
            return
        try:
            creds = codex_auth.login()
        except CodexAuthError as e:
            print(ansi_message(ERROR_LABEL, str(e)))
            sys.exit(1)
        who = creds.email or creds.account_id or "your subscription"
        print(ansi_message(INFO_LABEL, f"Logged in as {who}."))


def main():
    """Main entry point for the LLM CLI application."""
    load_env_file()
    registry = ModelRegistry()
    args = parse_arguments(registry)

    if getattr(args, "command", None) == "stats":
        run_stats(args)
        return

    if getattr(args, "command", None) == "auth":
        run_auth(args)
        return

    # Handle --user-paths command
    if args.user_paths:
        print_user_paths()
        return

    try:
        ctx = setup_configuration(args, registry)
    except PromptNotFoundError as e:
        print(ansi_message(ERROR_LABEL, str(e)))
        sys.exit(2)

    if args.prompt is not None:
        run_headless_turn(args, ctx, registry)
        return

    # Handle chat selection/loading
    current_chat = handle_chat_selection(args, ctx.chat_manager)
    is_new_chat = False
    requested_model = registry.resolve_model_name(
        args.model or registry.get_default_model()
    )

    if current_chat is None:
        # Create new chat
        current_chat = ctx.chat_manager.create_new_chat(requested_model, ctx.prompt_str)
        current_chat.metadata.set_model_capabilities_snapshot(
            registry.get_model_capabilities(requested_model)
        )
        is_new_chat = True
    ctx.active_model = current_chat.metadata.model
    if not is_new_chat:
        try:
            resolved_active_model = registry.resolve_model_name(ctx.active_model)
        except ModelNotFoundError:
            resolved_active_model = ctx.active_model
        if args.model is not None and requested_model != resolved_active_model:
            print(
                ansi_message(
                    INFO_LABEL,
                    f"Resumed chat locked to its original model: {ctx.active_model} "
                    f"(ignoring --model {args.model})",
                )
            )

    # Show continuation message for existing chats
    if not is_new_chat:
        print(
            ansi_message(
                INFO_LABEL,
                f"Continuing chat: {current_chat.metadata.title} "
                f"({current_chat.metadata.model}{_billing_tag(registry, ctx.active_model)}"
                f", {current_chat.metadata.message_count} messages)",
            )
        )
        print(
            ansi_message(
                INFO_LABEL,
                "Press Ctrl+C to exit. Use Shift+Enter for new lines.",
            )
        )

    run_chat_loop(current_chat, ctx)


if __name__ == "__main__":
    main()
