"""Pilot-driven tests for the TUI frontend."""

import asyncio
from unittest.mock import Mock

from pydantic_ai.messages import ModelResponse, TextPart
from textual.events import Key
from textual.widgets import Markdown

from oi.app import ChatLoopContext
from oi.config.settings import Config, load_user_config
from oi.core.chat_manager import ChatManager
from oi.llm_types import ChatOptions, ModelCapabilities
from oi.tui.app import ChatInput, OiApp, ResponseView
from oi.tui.slash_menu import SlashMenu
from oi.tui.vim import Mode as VimMode
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


def test_input_is_never_empty_before_the_echoed_row_exists(tmp_path):
    """Clearing the input and echoing the message are one frame.

    Mounting costs a refresh cycle of its own, so an unbatched submit paints
    the emptied input first and the message a relayout later — visible as a
    stutter between typing and the message landing in the conversation.
    """

    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        async with app.run_test() as pilot:
            chat_input = app.query_one(ChatInput)
            screen = app.screen
            painted: list[tuple[bool, bool]] = []
            compositor_refresh = screen._compositor_refresh

            def record_frame() -> None:
                compositor_refresh()
                # Laid out, not merely mounted: `mount()` registers the widget
                # straight away, so only the compositor knows what was shown.
                echoed = any(
                    "hello there" in str(widget.render())
                    for widget in screen._compositor.visible_widgets
                    if widget.has_class("content")
                )
                painted.append((chat_input.text == "", echoed))

            screen._compositor_refresh = record_frame

            chat_input.insert("hello there")
            await pilot.pause()
            painted.clear()

            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()

            assert painted, "no frame was painted for the submit"
            assert (True, False) not in painted, (
                "a frame showed an emptied input before the message was echoed"
            )
            assert (True, True) in painted

    asyncio.run(scenario())


def test_the_turn_starts_only_once_the_message_is_on_screen(tmp_path):
    """Starting a turn blocks the loop while pydantic-ai builds the model."""

    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        painted_when_called: list[bool] = []

        async def chat_async(messages, model_name_or_alias, options=None, **kwargs):
            painted_when_called.append(
                any(
                    "hello there" in str(widget.render())
                    for widget in app.screen._compositor.visible_widgets
                    if widget.has_class("content")
                )
            )
            return ModelResponse(parts=[TextPart(content="ok")])

        ctx.llm_client.chat_async = chat_async

        async with app.run_test() as pilot:
            app.query_one(ChatInput).insert("hello there")
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert painted_when_called == [True], (
                "the turn began before the message had been painted"
            )

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


class TestCtrlC:
    """Ctrl+C forks: interrupt a stream, else copy a selection, else exit."""

    def _hint(self, app) -> str:
        from textual.widgets import Static

        return str(app.query_one("#hint", Static).render())

    def test_copies_selection_and_never_exits(self, tmp_path):
        async def scenario():
            app, chat, ctx = _make_app(tmp_path)
            copied: list[str] = []

            async with app.run_test() as pilot:
                app.copy_to_clipboard = copied.append
                app.screen.get_selected_text = lambda: "selected text"

                await pilot.press("ctrl+c")
                await pilot.pause()
                assert copied == ["selected text"]
                assert app.is_running
                assert "copied" in self._hint(app)

                # A second copy in quick succession must not quit.
                await pilot.press("ctrl+c")
                await pilot.pause()
                assert copied == ["selected text", "selected text"]
                assert app.is_running

        asyncio.run(scenario())

    def test_double_press_exits_and_single_press_only_arms(self, tmp_path):
        async def scenario():
            app, chat, ctx = _make_app(tmp_path)
            async with app.run_test() as pilot:
                app.screen.get_selected_text = lambda: None

                await pilot.press("ctrl+c")
                await pilot.pause()
                assert app.is_running, "one press must not exit"
                assert "again to exit" in self._hint(app)

                await pilot.press("ctrl+c")
                await pilot.pause()
                assert not app.is_running

        asyncio.run(scenario())

    def test_arming_lapses_after_the_window(self, tmp_path):
        async def scenario():
            from oi.tui import app as tui_app

            app, chat, ctx = _make_app(tmp_path)
            async with app.run_test() as pilot:
                app.screen.get_selected_text = lambda: None

                await pilot.press("ctrl+c")
                await pilot.pause()
                # Simulate the window elapsing without waiting for it.
                app._exit_armed_at -= tui_app.CTRL_C_EXIT_WINDOW + 1

                await pilot.press("ctrl+c")
                await pilot.pause()
                assert app.is_running, "a lapsed arming must not exit"

        asyncio.run(scenario())

    def test_clicking_the_log_keeps_the_input_usable(self, tmp_path):
        """Selecting with the mouse must not steal focus from the input."""

        async def scenario():
            app, chat, ctx = _make_app(tmp_path)
            async with app.run_test() as pilot:
                chat_input = app.query_one(ChatInput)
                await pilot.click("#log", offset=(3, 1))
                await pilot.pause()
                assert app.focused is chat_input

                await pilot.press("a")
                await pilot.pause()
                assert chat_input.text == "a"

        asyncio.run(scenario())

    def test_focus_returns_to_the_input_after_copying(self, tmp_path):
        async def scenario():
            app, chat, ctx = _make_app(tmp_path)
            async with app.run_test() as pilot:
                chat_input = app.query_one(ChatInput)
                app.copy_to_clipboard = lambda text: None
                app.screen.get_selected_text = lambda: "selected text"
                app.set_focus(None)

                await pilot.press("ctrl+c")
                await pilot.pause()
                assert app.focused is chat_input

                await pilot.press("b")
                await pilot.pause()
                assert chat_input.text == "b"

        asyncio.run(scenario())

    def test_interrupt_takes_priority_over_copy(self, tmp_path):
        async def scenario():
            app, chat, ctx = _make_app(tmp_path)
            started = asyncio.Event()
            copied: list[str] = []

            async def hanging_chat_async(
                messages,
                model_name_or_alias,
                options=None,
                *,
                capabilities_override=None,
                renderer_factory=None,
            ):
                started.set()
                await asyncio.Event().wait()

            ctx.llm_client.chat_async = hanging_chat_async

            async with app.run_test() as pilot:
                app.copy_to_clipboard = copied.append
                app.screen.get_selected_text = lambda: "selected text"

                app.query_one(ChatInput).insert("hi")
                await pilot.press("enter")
                await asyncio.wait_for(started.wait(), timeout=5)

                await pilot.press("ctrl+c")
                await app.workers.wait_for_complete()
                await pilot.pause()

                assert copied == [], "streaming Ctrl+C must interrupt, not copy"
                assert app.is_running

        asyncio.run(scenario())


def test_markdown_table_cells_have_no_tooltips(tmp_path):
    """`run_test` forces tooltips off, so assert the app's own setting."""
    app, _, _ = _make_app(tmp_path)
    assert app._disable_tooltips


def test_mouse_wheel_scrolls_one_line_per_event(tmp_path):
    """Terminals send three wheel events per notch; one line each = 3 a notch."""
    app, _, _ = _make_app(tmp_path)
    assert app.scroll_sensitivity_y == 1.0


def test_vim_toggle_reports_a_config_that_could_not_be_saved(tmp_path, monkeypatch):
    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        monkeypatch.setattr(
            "oi.app.update_user_config",
            Mock(side_effect=OSError("Read-only file system")),
        )
        async with app.run_test() as pilot:
            chat_input = app.query_one(ChatInput)
            chat_input.insert("/vim")
            await pilot.press("enter")
            await pilot.pause()

            # Vim still turns on for the session; the notice says it is unsaved.
            assert chat_input.vim is not None
            warnings = app.query(".warning-label")
            assert list(warnings), "expected a warning about the unsaved config"

    asyncio.run(scenario())


def test_tui_toggle_persists_the_setting(tmp_path):
    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        ctx.config.tui = True
        async with app.run_test() as pilot:
            app.query_one(ChatInput).insert("/tui")
            await pilot.press("enter")
            await pilot.pause()

            assert ctx.config.tui is False
            assert load_user_config()["tui"] is False
            assert list(app.query(".info-label")), "expected a /tui notice"

        assert ctx.llm_client.calls == 0
        assert chat.messages == []

    asyncio.run(scenario())


def test_escape_leaves_insert_before_the_keys_typed_behind_it(tmp_path):
    """Esc + command in one burst runs the command, not inserts it."""

    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        ctx.config.vim_mode = True
        async with app.run_test() as pilot:
            chat_input = app.query_one(ChatInput)
            chat_input.insert("hello world")
            await pilot.pause()
            assert chat_input.vim is not None

            # Queued together, as a fast typist's keystrokes arrive: the mode
            # change must be visible to the keys behind Esc.
            chat_input.post_message(Key("escape", None))
            chat_input.post_message(Key("d", "d"))
            chat_input.post_message(Key("d", "d"))
            await pilot.pause()
            await pilot.pause()

            assert chat_input.vim.mode is VimMode.NORMAL
            assert chat_input.text == ""

    asyncio.run(scenario())


def test_escape_prefers_the_slash_menu_then_vim(tmp_path):
    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        ctx.config.vim_mode = True
        async with app.run_test() as pilot:
            chat_input = app.query_one(ChatInput)
            chat_input.insert("/vi")
            await pilot.pause()
            menu = app.query_one(SlashMenu)
            assert menu.is_open

            await pilot.press("escape")
            await pilot.pause()
            assert not menu.is_open
            # The menu ate this Esc; vim stays in insert.
            assert chat_input.vim is not None
            assert chat_input.vim.mode is VimMode.INSERT

            await pilot.press("escape")
            await pilot.pause()
            assert chat_input.vim.mode is VimMode.NORMAL

    asyncio.run(scenario())


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


def test_image_markers_render_as_colored_pills(tmp_path):
    """Pills are cyan bold in the input and in the echoed message (CLI parity)."""
    from pydantic_ai.messages import BinaryContent
    from textual.widgets import Static

    def pill_style(strip):
        for segment in strip:
            if segment.text == "[Image #1]":
                return segment.style
        raise AssertionError(f"no marker segment in {[s.text for s in strip]!r}")

    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        async with app.run_test() as pilot:
            chat_input = app.query_one(ChatInput)
            chat_input.focus()
            await pilot.pause()
            chat_input.insert("look at ")
            chat_input.attach_image(BinaryContent(data=b"x", media_type="image/png"))
            # The cursor rests on the marker's opening bracket; neither it nor
            # the cursor line may repaint those cells.
            chat_input.move_cursor((0, 8))
            await pilot.pause()

            style = pill_style(chat_input.render_line(0))
            assert style.bold and style.color.number == 6

            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()

            echoed = app.query("Static.content").last(Static)
            assert pill_style(echoed.render_line(0)).color.number == 6

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
