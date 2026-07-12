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

    parser = argparse.ArgumentParser(
        description="Run an interactive LLM chat session.",
        add_help=False,
        usage=(
            "%(prog)s [-h] [-m MODEL] [-c] [-r [CHAT_ID]] [options]\n"
            "       %(prog)s {stats,auth,docs} ..."
        ),
        epilog=(
            "configuration:\n"
            "  Models and aliases live in a user models.yaml (--user-paths prints\n"
            "  its location). To add or override models — config shape, pydantic-ai\n"
            "  model names, API keys — run: %(prog)s docs models\n"
            "  (LLM-agent-oriented; point your coding agent at it)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    selection = parser.add_argument_group("chat selection")
    selection.add_argument(
        "-r",
        "--resume",
        nargs="?",
        const="",
        metavar="CHAT_ID",
        help="Resume a chat (no ID shows the picker)",
    )
    selection.add_argument(
        "-c",
        "--continue",
        dest="continue_chat",
        action="store_true",
        help="Continue the most recent chat",
    )

    config = parser.add_argument_group("model & prompt")
    config.add_argument(
        "-m",
        "--model",
        choices=available_models,
        default=None,
        metavar="MODEL",
        help=f"Model to use: {', '.join(available_models)} "
        "(default: configured default; ignored when resuming)",
    )
    config.add_argument(
        "-P",
        "--system-prompt",
        choices=available_prompts,
        default=_get_default_prompt_name(available_prompts),
        metavar="NAME",
        help=f"Named system prompt: {', '.join(available_prompts)}",
    )

    headless = parser.add_argument_group("headless / scripting")
    headless.add_argument(
        "-p",
        "--prompt",
        metavar="MESSAGE",
        help="Send a single message and exit",
    )
    headless.add_argument(
        "--ephemeral",
        action="store_true",
        help="Do not persist the session "
        "(with -c/-r, runs against the chat without modifying it)",
    )

    behavior = parser.add_argument_group("behavior")
    behavior.add_argument(
        "--search",
        action="store_true",
        help="Enable web search (if supported by the model)",
    )
    behavior.add_argument(
        "--no-thinking",
        action="store_true",
        help="Disable thinking entirely",
    )
    behavior.add_argument(
        "--hide-thinking",
        action="store_true",
        help="Keep thinking on but don't display the traces",
    )

    other = parser.add_argument_group("other")
    other.add_argument(
        "-h",
        "--help",
        action="help",
        help="Show this help message and exit",
    )
    other.add_argument(
        "--user-paths",
        action="store_true",
        help="Print all user path locations and exit",
    )

    # Optional verb subcommands. With no verb, `command` is None and the args
    # above drive the normal chat path; chat flags must precede the verb token.
    # `prog` is pinned so subparser usage lines don't inherit the custom usage.
    subparsers = parser.add_subparsers(
        title="commands", dest="command", prog=parser.prog
    )
    stats_parser = subparsers.add_parser(
        "stats", help="Show usage statistics across all chats"
    )
    stats_parser.add_argument(
        "--deep",
        action="store_true",
        help="Scan full transcripts for token, word, and image stats (slower)",
    )

    docs_parser = subparsers.add_parser(
        "docs",
        help="Print detailed docs for a topic, written for LLM agents",
    )
    docs_parser.add_argument(
        "topic",
        nargs="?",
        choices=["models"],
        default="models",
        help="Doc topic (defaults to models)",
    )

    auth_parser = subparsers.add_parser(
        "auth",
        help="Manage provider subscription logins",
        usage="%(prog)s [-h] {openai} [{login,logout,status}]",
        description="Log in to a provider subscription. Each provider takes an "
        "action: login (default), logout, or status.",
    )
    auth_providers = auth_parser.add_subparsers(
        title="providers", dest="auth_provider", prog=auth_parser.prog
    )
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
