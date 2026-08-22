"""Pilot-driven tests for the TUI frontend."""

import asyncio
from unittest.mock import Mock

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from textual.events import Key
from textual.widgets import Markdown
from textual.widgets._markdown import MarkdownTable, MarkdownTableContent

from oi.app import ChatLoopContext
from oi.config.settings import Config, load_user_config
from oi.core.chat_manager import ChatManager
from oi.llm_types import ChatOptions, ModelCapabilities
from oi.tui.app import MAX_INPUT_HEIGHT, ChatInput, ChatLog, OiApp, ResponseView
from oi.tui.slash_menu import SlashMenu
from oi.tui.vim import Mode as VimMode
from oi.tui.renderer import ResponseStarted, TextDelta, ThinkingDelta, TuiRenderer

RESPONSE_MD = "# Title\n\nHello **world**\n\n- one\n- two"
TABLE_MD = "| Model | Provider |\n| --- | --- |\n| opus | anthropic |\n"


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


def _make_app(tmp_path, capabilities=None, chat_options=None):
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
        chat_options=chat_options or ChatOptions(),
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


def test_header_shows_search_only_when_it_is_in_play(tmp_path):
    async def header_for(capabilities, options):
        app, _, _ = _make_app(tmp_path, capabilities, options)
        async with app.run_test():
            return str(app.query_one(".header").render())

    async def scenario():
        searching = ModelCapabilities(supports_search=True)
        assert "· search" in await header_for(
            searching, ChatOptions(enable_search=True)
        )
        assert "· search" not in await header_for(searching, ChatOptions())
        # Enabled but unsupported: the client drops the tool, so does the header.
        assert "· search" not in await header_for(
            ModelCapabilities(), ChatOptions(enable_search=True)
        )

    asyncio.run(scenario())


def test_search_command_turns_search_on_and_updates_the_header(tmp_path):
    async def scenario():
        app, _, ctx = _make_app(
            tmp_path, ModelCapabilities(supports_search=True), ChatOptions()
        )
        async with app.run_test() as pilot:
            assert "· search" not in str(app.query_one(".header").render())

            app.query_one(ChatInput).insert("/search")
            await pilot.press("enter")
            await pilot.pause()

            assert ctx.chat_options.enable_search is True
            # The header is mounted once at startup, so it has to be rewritten
            # or it keeps advertising the state the session opened in.
            assert "· search" in str(app.query_one(".header").render())

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


class TestMacChords:
    """Cmd+C copy and Ctrl+V image paste, for terminals that forward them."""

    def test_super_c_copies_and_never_arms_the_exit(self, tmp_path):
        async def scenario():
            app, chat, ctx = _make_app(tmp_path)
            copied: list[str] = []

            async with app.run_test() as pilot:
                app.copy_to_clipboard = copied.append
                app.screen.get_selected_text = lambda: "selected text"

                await pilot.press("super+c")
                await pilot.pause()
                assert copied == ["selected text"]

                # With nothing selected it must do nothing — not arm the exit.
                app.screen.get_selected_text = lambda: None
                await pilot.press("super+c")
                await pilot.press("super+c")
                await pilot.pause()
                assert app.is_running
                assert copied == ["selected text"]

        asyncio.run(scenario())

    def test_ctrl_v_pastes_an_image(self, tmp_path, monkeypatch):
        async def scenario():
            from oi.llm_types import ModelCapabilities

            monkeypatch.setattr(
                "oi.tui.app.read_clipboard_image", lambda: (b"png", "image/png")
            )
            app, chat, ctx = _make_app(
                tmp_path, capabilities=ModelCapabilities(supports_vision=True)
            )
            async with app.run_test() as pilot:
                chat_input = app.query_one(ChatInput)
                await pilot.press("ctrl+v")
                await pilot.pause()
                assert chat_input.text == "[Image #1] "

        asyncio.run(scenario())

    def test_ctrl_v_is_a_no_op_without_vision(self, tmp_path, monkeypatch):
        async def scenario():
            monkeypatch.setattr(
                "oi.tui.app.read_clipboard_image", lambda: (b"png", "image/png")
            )
            app, chat, ctx = _make_app(tmp_path)  # default caps: no vision
            async with app.run_test() as pilot:
                chat_input = app.query_one(ChatInput)
                await pilot.press("ctrl+v")
                await pilot.pause()
                assert chat_input.text == ""

        asyncio.run(scenario())


def test_tables_are_sized_to_their_content(tmp_path):
    """A narrow table stays narrow instead of stretching across the pane."""

    async def scenario():
        app, _, _ = _make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            log = app.query_one(ChatLog)
            await log.mount(Markdown(TABLE_MD))
            await pilot.pause()

            content = app.query_one(MarkdownTableContent)
            columns = [cell.region.width for cell in content.children][:3]
            assert sum(columns) < log.size.width // 2
            assert app.query_one(MarkdownTable).styles.is_auto_width

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


def _fill_history(chat, turns=20):
    """Enough conversation that the log is scrolled to the bottom."""
    for i in range(turns):
        chat.messages.append(
            ModelRequest(parts=[UserPromptPart(content=f"question {i}")])
        )
        chat.messages.append(
            ModelResponse(parts=[TextPart(content="An answer.\n\n- one\n- two\n")])
        )


def test_a_newline_moves_the_conversation_in_one_step(tmp_path):
    """Growing the input shifts the log once, not once per layout pass.

    Anchored content is re-scrolled to the bottom from the container height
    recorded on the *previous* layout pass, so an input that just grew leaves
    the conversation a row short until the follow-up pass — visible as a jump
    a frame after every newline.
    """

    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        _fill_history(chat)
        async with app.run_test() as pilot:
            chat_input = app.query_one(ChatInput)
            log = app.query_one(ChatLog)
            screen = app.screen
            scrolls: list[float] = []
            compositor_refresh = screen._compositor_refresh

            def record_frame() -> None:
                compositor_refresh()
                scrolls.append(log.scroll_y)

            screen._compositor_refresh = record_frame

            chat_input.insert("first line")
            await pilot.pause()

            for _ in range(3):
                scrolls.clear()
                await pilot.press("ctrl+j")
                await pilot.pause()
                await pilot.pause()

                assert scrolls, "no frame was painted for the newline"
                assert len(set(scrolls)) == 1, (
                    f"the conversation moved mid-newline: {scrolls}"
                )
                assert scrolls[-1] == log.max_scroll_y

    asyncio.run(scenario())


def test_growing_past_the_height_cap_keeps_the_text_where_it_is(tmp_path):
    """Beyond the cap the input scrolls instead of growing.

    A visible scrollbar would take two columns off the wrap width and re-wrap
    everything already typed, and the cursor must not lag a frame behind the
    line being typed.
    """

    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        async with app.run_test() as pilot:
            chat_input = app.query_one(ChatInput)
            screen = app.screen
            offsets: list[int] = []
            compositor_refresh = screen._compositor_refresh

            def record_frame() -> None:
                compositor_refresh()
                offsets.append(chat_input.scroll_offset.y)

            screen._compositor_refresh = record_frame

            chat_input.insert("line 1")
            await pilot.pause()
            wrap_width = chat_input.wrap_width

            for line in range(2, MAX_INPUT_HEIGHT + 4):
                offsets.clear()
                await pilot.press("ctrl+j")
                chat_input.insert(f"line {line}")
                await pilot.pause()
                await pilot.pause()

                assert chat_input.wrap_width == wrap_width, "the input re-wrapped"
                assert len(set(offsets)) == 1, f"the text moved mid-edit: {offsets}"
                assert offsets[-1] == chat_input.scroll_offset.y

    asyncio.run(scenario())


def test_deleting_a_newline_puts_the_caret_straight_where_it_lands(tmp_path):
    """The caret goes to the end of the line above, without a detour.

    The input is pinned to the bottom, so joining two lines moves its top edge
    down a row — but TextArea records the caret's screen offset mid-edit,
    against the geometry it is leaving, so the terminal cursor would be
    painted a row high and drop back on the next frame.
    """

    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        async with app.run_test() as pilot:
            chat_input = app.query_one(ChatInput)
            screen = app.screen
            positions: list[tuple[int, int]] = []
            compositor_refresh = screen._compositor_refresh

            def record_frame() -> None:
                compositor_refresh()
                positions.append(tuple(app.cursor_position))

            screen._compositor_refresh = record_frame

            chat_input.insert("abc")
            await pilot.press("ctrl+j")
            chat_input.insert("defg")
            await pilot.press("home")
            await pilot.pause()
            settled_before = positions[-1]
            positions.clear()

            await pilot.press("backspace")
            await pilot.pause()
            await pilot.pause()

            assert positions, "no frame was painted for the delete"
            assert len(set(positions)) == 1, f"the caret took a detour: {positions}"
            # End of "abc", on the row the second line used to occupy.
            assert positions[-1] == (5, settled_before[1])

    asyncio.run(scenario())


def test_vim_line_delete_resizes_the_input_in_the_painted_frame(tmp_path):
    """A vim `dd` that shrinks the input is one frame, caret included.

    Syncing the height off the `Changed` message the edit posts is a frame
    late: `dd` does enough work that the screen is painted before that message
    is handled, leaving the input a row too tall with the caret on the old row.
    """

    async def scenario():
        app, chat, ctx = _make_app(tmp_path)
        async with app.run_test() as pilot:
            chat_input = app.query_one(ChatInput)
            chat_input.set_vim_enabled(True)
            screen = app.screen
            frames: list[tuple[int, tuple[int, int]]] = []
            compositor_refresh = screen._compositor_refresh

            def record_frame() -> None:
                compositor_refresh()
                frames.append((chat_input.size.height, tuple(app.cursor_position)))

            screen._compositor_refresh = record_frame

            chat_input.insert("alpha\nbeta\ngamma")
            chat_input.move_cursor((1, 0))
            await pilot.press("escape")
            await pilot.pause()
            frames.clear()

            await pilot.press("d")
            await pilot.press("d")
            await pilot.pause()
            await pilot.pause()

            assert chat_input.text == "alpha\ngamma"
            assert frames, "no frame was painted for the delete"
            assert len(set(frames)) == 1, f"the input settled late: {frames}"
            assert frames[-1][0] == 2

    asyncio.run(scenario())


def _run_search_turn(tmp_path, stream):
    """Run one fake turn that drives `stream(renderer)` and return the app."""

    async def scenario(result):
        app, chat, ctx = _make_app(tmp_path)

        async def searching_chat_async(
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
            stream(renderer)
            renderer.finish_response()
            return ModelResponse(parts=[TextPart(content="x")])

        ctx.llm_client.chat_async = searching_chat_async

        async with app.run_test() as pilot:
            app.query_one(ChatInput).insert("what about cats")
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.pause()
            result(app)

    return scenario


def test_native_tool_lines_are_top_level_and_update_in_place(tmp_path):
    """A search call renders as one gutter-level line (a sibling of the turn
    rows, not inside the content column) updated through its lifecycle, and
    text after it mounts as a new segment row below, keeping order."""

    def stream(renderer):
        renderer.render_text("Let me check.")
        renderer.render_native_tool_call("c1", "web_search", None)
        renderer.render_native_tool_call("c1", "web_search", {"query": "cats"})
        renderer.render_native_tool_return("c1", "web_search", [{}, {}])
        # An argless fetch (driven from a code-execution block): the URL is
        # recovered from the return payload.
        renderer.render_native_tool_call("c2", "web_fetch", None)
        renderer.render_native_tool_return("c2", "web_fetch", {"url": "https://x.io/a"})
        renderer.render_text("Answer.")

    def check(app):
        search_line, fetch_line = app.query(".tool-line").results()
        assert search_line.render().plain == '● Web Search("cats") · 2 results'
        assert fetch_line.render().plain == "● Fetch(https://x.io/a)"
        (block,) = app.query(".tool-block").results()
        assert search_line in block.children
        assert not app.query_one(ResponseView).query(".tool-line")

        views = list(app.query(ResponseView).results())
        assert len(views) == 2
        assert views[0].query_one(Markdown).source == "Let me check."
        assert views[1].query_one(Markdown).source == "Answer."

        log = app.query_one(ChatLog)
        rows = [w for w in log.children if w.has_class("row") or w is block]
        assert rows.index(block) == len(rows) - 2  # between the two turn rows

    asyncio.run(_run_search_turn(tmp_path, stream)(check))


def test_thinking_after_tool_calls_mounts_below_in_order(tmp_path):
    """Interleaved thinking reads top to bottom: a trace resuming after a
    search continues in a new block below it, not in the widget at the top."""

    def stream(renderer):
        renderer.render_thinking("first thought")
        renderer.render_native_tool_call("c1", "web_search", {"query": "cats"})
        renderer.render_thinking("second thought")
        renderer.render_text("Answer.")

    def check(app):
        views = list(app.query(ResponseView).results())
        assert len(views) == 2
        first = views[0].query_one(".thinking")
        second = views[1].query_one(".thinking")
        assert "first thought" in str(first.render())
        assert "second thought" in str(second.render())
        assert "first" not in str(second.render())

        (block,) = app.query(".tool-block").results()
        log = app.query_one(ChatLog)
        children = list(log.children)
        first_row = first.ancestors[1]  # content column -> row
        second_row = second.ancestors[1]
        assert children.index(first_row) < children.index(block)
        assert children.index(block) < children.index(second_row)

        # One marker per turn: the continuation row keeps the indent only.
        assert first_row.children[0].render().plain == "● "
        assert second_row.children[0].render().plain == "  "

    asyncio.run(_run_search_turn(tmp_path, stream)(check))


def test_wrapper_code_collapses_into_the_web_line(tmp_path):
    """Dynamic-filtering wrapper code (`await web_search({...})`) is not shown
    as a Code line; its args are donated to the paired argless search line.
    Code that actually processes data keeps its line."""

    def stream(renderer):
        renderer.render_native_tool_call("cx", "code_execution", None)
        renderer.render_native_tool_call(
            "cx",
            "code_execution",
            {
                "code": 'result = await web_search({"query": "Claude Fable"})\nprint(result)'
            },
        )
        renderer.render_native_tool_call("ws", "web_search", None, from_code=True)
        renderer.render_native_tool_return("ws", "web_search", [{}] * 8)
        renderer.render_native_tool_return("cx", "code_execution", {})
        renderer.render_native_tool_call("cy", "code_execution", None)
        renderer.render_native_tool_call(
            "cy", "code_execution", {"code": "import json\ndata = json.loads(r1)"}
        )
        renderer.render_native_tool_return("cy", "code_execution", {})
        renderer.render_text("Answer.")

    def check(app):
        lines = [w.render().plain for w in app.query(".tool-line").results()]
        assert lines == [
            '● Web Search("Claude Fable") · 8 results',
            "● Code(data = json.loads(r1))",
        ]

    asyncio.run(_run_search_turn(tmp_path, stream)(check))
