import pytest
from pydantic_ai.messages import ModelResponse, TextPart

from oi.core.message_utils import (
    build_prompt,
    flatten_history,
    join_text_parts,
    latest_system_prompt,
    prune_interrupted_response,
    text_part_separator,
)


def test_build_prompt_without_system_includes_user_message():
    messages = build_prompt(None, "Hello from user")

    assert flatten_history(messages) == [("user", "Hello from user")]
    assert latest_system_prompt(messages) is None


def test_build_prompt_with_system_includes_both_parts():
    messages = build_prompt("You are helpful", "Hello from user")

    assert flatten_history(messages) == [("user", "Hello from user")]
    assert latest_system_prompt(messages) == "You are helpful"


CITED_PARTS = [
    "Here's the rundown:\n\n## Specs",
    "Both models have a 1M token context window",
    " — ",
    "less than half the price.",
    "\n\n## Access",
    "Included in Max.",
]


def test_join_text_parts_restores_break_dropped_after_a_heading():
    assert join_text_parts(CITED_PARTS) == (
        "Here's the rundown:\n\n## Specs\n\n"
        "Both models have a 1M token context window — less than half the price."
        "\n\n## Access\n\nIncluded in Max."
    )


@pytest.mark.parametrize(
    "accumulated, following",
    [
        ("## Specs\n", "Both models"),  # nothing was dropped
        ("## Specs", "\n\nBoth models"),  # the break is on the other side
        ("a paragraph.", "Another sentence."),  # not a heading: unknowable
        ("#hashtag", "no space, no heading"),
        ("", "Opening block"),
    ],
)
def test_text_part_separator_stays_out_of_the_way(accumulated, following):
    assert text_part_separator(accumulated, following) == ""


def test_flatten_history_restores_the_break_on_replay():
    response = ModelResponse(parts=[TextPart(content=c) for c in CITED_PARTS])

    ((role, text),) = flatten_history([response])
    assert role == "assistant"
    assert "## Specs\n\nBoth models" in text


def test_prune_interrupted_response_drops_dangling_native_call():
    from pydantic_ai.messages import NativeToolCallPart, ThinkingPart

    response = ModelResponse(
        parts=[
            ThinkingPart(content="let me search"),
            TextPart(content="Checking that now."),
            NativeToolCallPart(
                tool_name="web_search", args={"query": "q"}, tool_call_id="c1"
            ),
        ]
    )

    pruned = prune_interrupted_response(response)

    assert pruned is not None
    assert [type(part).__name__ for part in pruned.parts] == [
        "ThinkingPart",
        "TextPart",
    ]
    assert pruned.state == "interrupted"


def test_prune_interrupted_response_keeps_completed_native_call_pair():
    from pydantic_ai.messages import NativeToolCallPart, NativeToolReturnPart

    response = ModelResponse(
        parts=[
            NativeToolCallPart(
                tool_name="web_search", args={"query": "q"}, tool_call_id="c1"
            ),
            NativeToolReturnPart(tool_name="web_search", content=[], tool_call_id="c1"),
        ]
    )

    pruned = prune_interrupted_response(response)

    assert pruned is not None
    assert len(pruned.parts) == 2


def test_prune_interrupted_response_returns_none_without_substance():
    assert prune_interrupted_response(ModelResponse(parts=[])) is None
    assert (
        prune_interrupted_response(ModelResponse(parts=[TextPart(content="  \n")]))
        is None
    )
