"""Pilot-driven tests for the TUI frontend."""

import asyncio
from unittest.mock import Mock

from pydantic_ai.messages import ModelResponse, TextPart
from textual.widgets import Markdown

from oi.app import ChatLoopContext
from oi.config.settings import Config
from oi.core.chat_manager import ChatManager
from oi.llm_types import ChatOptions, ModelCapabilities
from oi.tui.app import ChatInput, OiApp, ResponseView
from oi.tui.renderer import ResponseStarted, TextDelta, ThinkingDelta, TuiRenderer

RESPONSE_MD = "# Title\n\nHello **world**\n\n- one\n- two"


class FakeLLMClient:
    """Streams a canned markdown response through the injected renderer."""

    def __init__(self, capabilities: ModelCapabilities):
        self.capabilities = capabilities
        self.registry = Mock()
        self.registry.get_provider_for_model.return_value = ("anthropic", "claude-x")
        self.registry.get_model_capabilities.return_value = capabilities
        self.calls = 0

    def resolve_capabilities(self, model_name, capabilities_override=None):
        return self.capabilities

    async def chat_async(
        self,
        messages,
        model_name_or_alias,
        options=None,
        *,
        capabilities_override=None,
        renderer_factory=None,
    ):
        self.calls += 1
        assert renderer_factory is not None
        renderer = renderer_factory(self.capabilities, options)
        renderer.start_response()
        renderer.render_thinking("pondering...")
        for chunk in (RESPONSE_MD[:10], RESPONSE_MD[10:]):
            renderer.render_text(chunk)
        renderer.finish_response()
        return ModelResponse(parts=[TextPart(content=RESPONSE_MD)])


def _make_app(tmp_path, capabilities=None):
    capabilities = capabilities or ModelCapabilities(supports_thinking=True)
    config = Config(chat_dir=str(tmp_path / "chats"))
    chat_manager = ChatManager(config)
    llm_client = FakeLLMClient(capabilities)
    chat = chat_manager.create_new_chat("test-model", "test prompt")
    chat.metadata.set_model_capabilities_snapshot(capabilities)
    ctx = ChatLoopContext(
        config=config,
        chat_manager=chat_manager,
        llm_client=llm_client,  # type: ignore[arg-type]
        input_handler=Mock(),
        chat_options=ChatOptions(),
        prompt_str="test prompt",
        active_model="test-model",
    )
    return OiApp(chat, ctx, llm_client.registry, is_new_chat=True), chat, ctx


def test_submitted_turn_streams_markdown_and_saves(tmp_path):
    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        async with app.run_test() as pilot:
            chat_input = app.query_one(ChatInput)
            chat_input.insert("hello there")
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            # Let the worker's queued renderer/finish messages be processed.
            await pilot.pause()
            await pilot.pause()

            markdown = app.query_one(ResponseView).query_one(Markdown)
            assert markdown.source == RESPONSE_MD
            thinking = app.query_one(".thinking")
            assert "pondering" in str(thinking.render())
            assert chat_input.text == ""

        assert ctx.llm_client.calls == 1
        assert len(chat.messages) == 2  # request (system+user) + response
        saved = ctx.chat_manager.get_last_chat()
        assert saved is not None
        assert saved.metadata.title == "hello there"

    asyncio.run(scenario())


def test_assistant_marker_waits_for_the_first_output(tmp_path):
    """The ● row appears with the response, not at submit (CC behavior)."""

    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_chat_async(
            messages,
            model_name_or_alias,
            options=None,
            *,
            capabilities_override=None,
            renderer_factory=None,
        ):
            assert renderer_factory is not None
            renderer = renderer_factory(ctx.llm_client.capabilities, options)
            renderer.start_response()  # request in flight, no output yet
            started.set()
            await release.wait()
            renderer.render_text("here it is")
            renderer.finish_response()
            return ModelResponse(parts=[TextPart(content="here it is")])

        ctx.llm_client.chat_async = slow_chat_async

        async with app.run_test() as pilot:
            app.query_one(ChatInput).insert("hello")
            await pilot.press("enter")
            await asyncio.wait_for(started.wait(), timeout=5)
            await pilot.pause()
            await pilot.pause()

            assert not list(app.query(ResponseView)), "marker shown before output"

            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.pause()

            assert list(app.query(ResponseView)), "marker missing after output"

    asyncio.run(scenario())


def test_slash_command_is_not_sent_to_model(tmp_path):
    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        async with app.run_test() as pilot:
            app.query_one(ChatInput).insert("/vim")
            await pilot.press("enter")
            await pilot.pause()
            assert list(app.query(".info-label")), "expected a /vim notice"

            app.query_one(ChatInput).insert("/nonsense")
            await pilot.press("enter")
            await pilot.pause()
            warnings = app.query(".warning-label")
            assert list(warnings), "expected a warning for the unknown command"

        assert ctx.llm_client.calls == 0
        assert chat.messages == []

    asyncio.run(scenario())


def test_slash_menu_completes_on_tab(tmp_path):
    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        async with app.run_test() as pilot:
            from oi.tui.slash_menu import SlashMenu

            chat_input = app.query_one(ChatInput)
            menu = app.query_one(SlashMenu)
            await pilot.press("/")
            assert menu.is_open
            assert menu.selected_name == "/btw"

            await pilot.press("ctrl+n")
            assert menu.selected_name == "/bookmark"

            await pilot.press("tab")
            assert chat_input.text == "/bookmark "
            await pilot.pause()
            assert not menu.is_open

            await pilot.press("escape")  # menu closed: must not crash/quit
            chat_input.clear()
            await pilot.press("b")  # not a slash prefix
            assert not menu.is_open

    asyncio.run(scenario())


def test_btw_turn_streams_but_saves_nothing(tmp_path):
    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        async with app.run_test() as pilot:
            app.query_one(ChatInput).insert("/btw what about this?")
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.pause()

            markdown = app.query_one(ResponseView).query_one(Markdown)
            assert markdown.source == RESPONSE_MD

        assert ctx.llm_client.calls == 1
        assert chat.messages == []  # side question leaves no trace
        assert chat.pending_system_prompt == "test prompt"  # not consumed
        assert ctx.chat_manager.get_last_chat() is None

    asyncio.run(scenario())


def test_consume_content_splices_images():
    from pydantic_ai.messages import BinaryContent

    chat_input = ChatInput()
    image = BinaryContent(data=b"png", media_type="image/png")
    chat_input._images[1] = image

    content = chat_input.consume_content("look at [Image #1] please")

    assert content == ["look at ", image, " please"]
    assert chat_input._images == {}
    # Markers with no pending image stay literal text.
    assert chat_input.consume_content("[Image #9]") == "[Image #9]"


def test_cursor_cannot_rest_inside_a_marker(tmp_path):
    from pydantic_ai.messages import BinaryContent

    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        async with app.run_test() as pilot:
            chat_input = app.query_one(ChatInput)
            chat_input.focus()
            await pilot.pause()
            chat_input.insert("hi ")
            chat_input.attach_image(BinaryContent(data=b"x", media_type="image/png"))
            await pilot.pause()
            assert chat_input.text == "hi [Image #1] "

            # Arrowing left from the end steps over the marker in one go.
            chat_input.move_cursor((0, 13))
            await pilot.press("left")
            assert chat_input.cursor_location == (0, 3)

            # Arrowing right from before it clears it entirely.
            await pilot.press("right")
            assert chat_input.cursor_location == (0, 13)

            # Typing can never land inside the marker, so it stays intact.
            chat_input.move_cursor((0, 7))  # dropped in the middle
            await pilot.pause()
            assert chat_input.cursor_location in ((0, 3), (0, 13))
            await pilot.press("Z")
            await pilot.pause()
            assert "[Image #1]" in chat_input.text
            assert chat_input._images != {}

    asyncio.run(scenario())


def test_image_markers_are_atomic_and_renumbered(tmp_path):
    from pydantic_ai.messages import BinaryContent

    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        async with app.run_test() as pilot:
            chat_input = app.query_one(ChatInput)
            chat_input.focus()
            await pilot.pause()

            first = BinaryContent(data=b"a", media_type="image/png")
            second = BinaryContent(data=b"b", media_type="image/png")
            chat_input.attach_image(first)
            chat_input.attach_image(second)
            assert chat_input.text == "[Image #1] [Image #2] "

            # Backspace at a marker's trailing edge deletes it atomically.
            chat_input.move_cursor((0, 21))  # just after "[Image #2]"
            await pilot.press("backspace")
            await pilot.pause()
            # ...and the survivor keeps its number.
            assert chat_input.text == "[Image #1]  "
            assert chat_input._images == {1: first}

            # Next paste reuses the freed number.
            chat_input.attach_image(BinaryContent(data=b"c", media_type="image/png"))
            assert "[Image #2]" in chat_input.text
            assert sorted(chat_input._images) == [1, 2]
            # Drain pending Changed messages while the widgets are mounted.
            await pilot.pause()

    asyncio.run(scenario())


def test_interrupt_discards_pending_message(tmp_path):
    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        started = asyncio.Event()

        async def hanging_chat_async(
            messages,
            model_name_or_alias,
            options=None,
            *,
            capabilities_override=None,
            renderer_factory=None,
        ):
            assert renderer_factory is not None
            renderer = renderer_factory(ctx.llm_client.capabilities, options)
            renderer.start_response()
            renderer.render_text("partial answer")
            started.set()
            await asyncio.Event().wait()  # streams forever until cancelled

        ctx.llm_client.chat_async = hanging_chat_async

        async with app.run_test() as pilot:
            app.query_one(ChatInput).insert("hello")
            await pilot.press("enter")
            await asyncio.wait_for(started.wait(), timeout=5)
            await pilot.pause()

            await pilot.press("ctrl+c")
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.pause()

            assert list(app.query(".interrupted")), "expected [interrupted] marker"

        assert chat.messages == []  # pending user message discarded
        # The unsent system prompt is restored for the next attempt.
        assert chat.pending_system_prompt == "test prompt"

    asyncio.run(scenario())


def test_renderer_posts_deltas_in_order():
    posted = []
    renderer = TuiRenderer(
        ModelCapabilities(supports_thinking=True),
        ChatOptions(),
        lambda message: posted.append(message) or True,
    )
    renderer.start_response()
    renderer.render_thinking("hmm")
    renderer.render_text("part one, ")
    renderer.render_text("part two")
    renderer.finish_response()

    assert isinstance(posted[0], ResponseStarted)
    assert isinstance(posted[1], ThinkingDelta)
    assert [m.text for m in posted if isinstance(m, TextDelta)] == [
        "part one, ",
        "part two",
    ]
    assert renderer.get_full_response() == "part one, part two"
