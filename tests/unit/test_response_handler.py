"""Streaming-event handling, focused on server-side (native) tool calls."""

from pydantic_ai.messages import (
    NativeToolCallPart,
    NativeToolReturnPart,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    ToolCallPartDelta,
)

from oi.llm_types import ChatOptions, ModelCapabilities
from oi.renderers import ResponseRenderer
from oi.response_handler import ResponseHandler


class RecordingRenderer(ResponseRenderer):
    def __init__(self):
        super().__init__(ModelCapabilities(), ChatOptions())
        self.events = []

    def start_response(self):
        pass

    def _render_text(self, text):
        pass

    def _render_thinking(self, text):
        pass

    def _render_tool(self, text):
        self.events.append(("tool", text))

    def _begin_thinking_section(self):
        pass

    def _end_thinking_section(self, *, final):
        pass

    def _finish(self):
        pass

    def _render_native_tool_call(self, call_id, tool_name, args):
        self.events.append(("call", call_id, tool_name, args))

    def _render_native_tool_return(self, call_id, tool_name, content):
        self.events.append(("return", call_id, tool_name, content))


def make_handler():
    renderer = RecordingRenderer()
    handler = ResponseHandler(ModelCapabilities(), ChatOptions(), renderer=renderer)
    return handler, renderer.events


def test_anthropic_style_args_stream_as_json_fragments():
    """Call part starts empty, the query arrives via string deltas, and is
    announced exactly once — before the return part (i.e. during the wait)."""
    handler, events = make_handler()

    call = NativeToolCallPart(tool_name="web_search", args=None, tool_call_id="c1")
    handler.handle_event(PartStartEvent(index=0, part=call))
    assert events == [("call", "c1", "web_search", None)]

    handler.handle_event(
        PartDeltaEvent(index=0, delta=ToolCallPartDelta(args_delta='{"query": '))
    )
    assert len(events) == 1  # fragment doesn't parse yet

    handler.handle_event(
        PartDeltaEvent(index=0, delta=ToolCallPartDelta(args_delta='"cats"}'))
    )
    assert events[-1] == ("call", "c1", "web_search", {"query": "cats"})

    results = [{"title": "a", "url": "u"}, {"title": "b", "url": "v"}]
    handler.handle_event(
        PartEndEvent(
            index=0,
            part=NativeToolCallPart(
                tool_name="web_search", args='{"query": "cats"}', tool_call_id="c1"
            ),
        )
    )
    handler.handle_event(
        PartStartEvent(
            index=1,
            part=NativeToolReturnPart(
                tool_name="web_search", content=results, tool_call_id="c1"
            ),
        )
    )
    assert events == [
        ("call", "c1", "web_search", None),
        ("call", "c1", "web_search", {"query": "cats"}),
        ("return", "c1", "web_search", results),
    ]


def test_openai_style_args_arrive_as_one_dict_delta():
    handler, events = make_handler()
    args = {"type": "search", "query": "textual release"}

    handler.handle_event(
        PartStartEvent(
            index=0,
            part=NativeToolCallPart(
                tool_name="web_search", args=None, tool_call_id="ws_1"
            ),
        )
    )
    handler.handle_event(
        PartDeltaEvent(index=0, delta=ToolCallPartDelta(args_delta=args))
    )
    handler.handle_event(
        PartEndEvent(
            index=0,
            part=NativeToolCallPart(
                tool_name="web_search", args=args, tool_call_id="ws_1"
            ),
        )
    )
    assert events == [
        ("call", "ws_1", "web_search", None),
        ("call", "ws_1", "web_search", args),
    ]


def test_full_args_at_start_announce_once():
    """xAI/Gemini-3 style: the call part arrives with complete args."""
    handler, events = make_handler()
    args = {"query": "cats", "num_results": "10"}

    part = NativeToolCallPart(tool_name="web_search", args=args, tool_call_id="c1")
    handler.handle_event(PartStartEvent(index=0, part=part))
    handler.handle_event(PartEndEvent(index=0, part=part))
    assert events == [("call", "c1", "web_search", args)]


def test_client_tool_deltas_keep_old_rendering():
    handler, events = make_handler()
    handler.handle_event(
        PartDeltaEvent(
            index=0,
            delta=ToolCallPartDelta(tool_name_delta="mytool", args_delta='{"x": 1}'),
        )
    )
    assert events == [("tool", 'mytool {"x": 1}')]
