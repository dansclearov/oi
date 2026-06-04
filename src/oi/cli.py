import argparse

from oi.config.settings import load_user_config
from oi.prompts import get_prompts
from oi.registry import ModelRegistry


DEFAULT_PROMPT_NAME = "empty"


def _get_default_prompt_name(available_prompts: list[str]) -> str:
    """Return the configured default prompt when it exists, else fall back."""
    configured_prompt = load_user_config().get("default_prompt")
    if configured_prompt in available_prompts:
        return configured_prompt

    return DEFAULT_PROMPT_NAME


def parse_arguments(registry: ModelRegistry) -> argparse.Namespace:
    """Parse command line arguments."""
    available_models = registry.get_display_models()
    available_prompts = sorted(set(get_prompts() + [DEFAULT_PROMPT_NAME]))

    parser = argparse.ArgumentParser(description="Run an interactive LLM chat session.")
    parser.add_argument(
        "-P",
        "--system-prompt",
        choices=available_prompts,
        default=_get_default_prompt_name(available_prompts),
        help="Named system prompt to load from the prompts directory",
    )
    parser.add_argument(
        "-m",
        "--model",
        choices=available_models,
        default=None,
        help="Specify which model to use (defaults to the configured default for "
        "new chats; ignored when resuming a chat)",
    )
    parser.add_argument(
        "-r",
        "--resume",
        nargs="?",
        const="",
        metavar="CHAT_ID",
        help="Resume a chat: no ID shows selector, with ID loads specific chat",
    )
    parser.add_argument(
        "-c",
        "--continue",
        dest="continue_chat",
        action="store_true",
        help="Continue the most recent chat",
    )
    parser.add_argument(
        "-p",
        "--prompt",
        metavar="MESSAGE",
        help="Send a single message and exit (headless mode)",
    )
    parser.add_argument(
        "--ephemeral",
        action="store_true",
        help=(
            "Do not persist this session. With -c/-r, runs the turn against "
            "the existing chat without modifying it."
        ),
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="Enable search (if supported by model)",
    )
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        help="Disable thinking mode completely",
    )
    parser.add_argument(
        "--hide-thinking",
        action="store_true",
        help="Hide thinking trace display",
    )
    parser.add_argument(
        "--user-paths",
        action="store_true",
        help="Print all user path locations and exit",
    )

    # Optional verb subcommands. With no verb, `command` is None and the args
    # above drive the normal chat path; chat flags must precede the verb token.
    subparsers = parser.add_subparsers(dest="command")
    stats_parser = subparsers.add_parser(
        "stats", help="Show usage statistics across all chats"
    )
    stats_parser.add_argument(
        "--deep",
        action="store_true",
        help="Scan full transcripts for token, word, and image stats (slower)",
    )

    auth_parser = subparsers.add_parser(
        "auth", help="Manage provider subscription logins"
    )
    auth_providers = auth_parser.add_subparsers(dest="auth_provider")
    openai_auth = auth_providers.add_parser(
        "openai", help="Use your ChatGPT subscription for OpenAI models"
    )
    openai_auth.add_argument(
        "action",
        nargs="?",
        choices=["login", "logout", "status"],
        default="login",
        help="Auth action (defaults to login)",
    )

    return parser.parse_args()
