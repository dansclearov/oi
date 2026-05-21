from oi.core.message_utils import (
    build_prompt,
    flatten_history,
    latest_system_prompt,
)


def test_build_prompt_without_system_includes_user_message():
    messages = build_prompt(None, "Hello from user")

    assert flatten_history(messages) == [("user", "Hello from user")]
    assert latest_system_prompt(messages) is None


def test_build_prompt_with_system_includes_both_parts():
    messages = build_prompt("You are helpful", "Hello from user")

    assert flatten_history(messages) == [("user", "Hello from user")]
    assert latest_system_prompt(messages) == "You are helpful"
