"""Tests for the TUI vim layer: pure motions/objects + widget integration."""

import asyncio

from oi.tui.vim import (
    Mode,
    find_char,
    next_word_start,
    object_range,
    prev_word_start,
    word_end,
)

TEXT = "foo bar_baz, qux"


class TestMotions:
    def test_next_word_start(self):
        assert next_word_start(TEXT, 0) == 4  # foo -> bar_baz
        assert next_word_start(TEXT, 4) == 11  # bar_baz -> comma
        assert next_word_start(TEXT, 11) == 13  # comma -> qux
        assert next_word_start(TEXT, 13) == len(TEXT)

    def test_prev_word_start(self):
        assert prev_word_start(TEXT, 13) == 11
        assert prev_word_start(TEXT, 11) == 4
        assert prev_word_start(TEXT, 4) == 0
        assert prev_word_start(TEXT, 0) == 0

    def test_word_end(self):
        assert word_end(TEXT, 0) == 2  # end of foo
        assert word_end(TEXT, 2) == 10  # end of bar_baz
        assert word_end(TEXT, 13) == 15  # end of qux (clamped)

    def test_find_char(self):
        assert find_char(TEXT, 0, "b", "f") == 4
        assert find_char(TEXT, 0, "b", "t") == 3
        assert find_char(TEXT, 10, "b", "F") == 8  # nearest previous
        assert find_char(TEXT, 0, "z", "F") is None
        # line-bounded
        assert find_char("ab\ncd", 0, "c", "f") is None


class TestObjects:
    def test_inner_word(self):
        assert object_range(TEXT, 5, "w", True) == (4, 11)
        assert object_range(TEXT, 11, "w", True) == (11, 12)  # punct run

    def test_a_word_takes_trailing_space(self):
        assert object_range("foo bar baz", 4, "w", False) == (4, 8)

    def test_quotes(self):
        text = 'say "hello there" ok'
        assert object_range(text, 8, '"', True) == (5, 16)
        assert object_range(text, 8, '"', False) == (4, 17)
        # cursor before the opening quote still finds the pair
        assert object_range(text, 0, '"', True) == (5, 16)

    def test_pairs(self):
        text = "f(a, (b), c) end"
        assert object_range(text, 6, "(", True) == (6, 7)  # inner nested
        assert object_range(text, 3, "(", True) == (2, 11)
        assert object_range(text, 3, "b", True) == (2, 11)  # alias
        assert object_range(text, 13, "(", True) is None
        # cursor on the closing bracket selects that pair
        assert object_range(text, 11, "(", True) == (2, 11)


def _make_input():
    from textual import on
    from textual.app import App

    from oi.tui.app import ChatInput

    class Harness(App):
        def compose(self):
            yield ChatInput()

        # Mirror OiApp's escape routing (menu closed -> vim layer).
        @on(ChatInput.MenuKey)
        def _on_menu_key(self, message: ChatInput.MenuKey) -> None:
            if message.action == "dismiss":
                self.query_one(ChatInput).vim_escape()

    return Harness()


def _drive(text: str, keys: list[str], expect_text: str, expect_mode: Mode):
    async def scenario():
        app = _make_input()
        async with app.run_test() as pilot:
            from oi.tui.app import ChatInput

            chat_input = app.query_one(ChatInput)
            chat_input.set_vim_enabled(True)
            chat_input.insert(text)
            chat_input.focus()
            await pilot.pause()
            for key in keys:
                await pilot.press(key)
            assert chat_input.text == expect_text
            assert chat_input.vim is not None
            assert chat_input.vim.mode is expect_mode

    asyncio.run(scenario())


def test_escape_enters_normal_and_i_returns():
    _drive("hello", ["escape", "i", "x"], "hellxo", Mode.INSERT)


def test_dd_deletes_line():
    _drive("one\ntwo", ["escape", "g", "g", "d", "d"], "two", Mode.NORMAL)


def test_dw_and_undo():
    async def scenario():
        app = _make_input()
        async with app.run_test() as pilot:
            from oi.tui.app import ChatInput

            chat_input = app.query_one(ChatInput)
            chat_input.set_vim_enabled(True)
            chat_input.insert("foo bar")
            chat_input.focus()
            await pilot.pause()
            for key in ["escape", "g", "g", "d", "w"]:
                await pilot.press(key)
            assert chat_input.text == "bar"
            await pilot.press("u")
            assert chat_input.text == "foo bar"

    asyncio.run(scenario())


def test_ciw_changes_inner_word():
    _drive(
        "foo bar baz",
        ["escape", "0", "w", "c", "i", "w", "X"],
        "foo X baz",
        Mode.INSERT,
    )


def test_visual_yank_and_paste():
    async def scenario():
        app = _make_input()
        async with app.run_test() as pilot:
            from oi.tui.app import ChatInput

            chat_input = app.query_one(ChatInput)
            chat_input.set_vim_enabled(True)
            chat_input.insert("abc")
            chat_input.focus()
            await pilot.pause()
            # yank "ab" visually, paste after cursor (at "a" after yank)
            for key in ["escape", "0", "v", "l", "y"]:
                await pilot.press(key)
            assert chat_input.vim.mode is Mode.NORMAL
            await pilot.press("p")
            assert chat_input.text == "aabbc"

    asyncio.run(scenario())


def test_visual_line_delete():
    _drive("one\ntwo\nthree", ["escape", "g", "g", "V", "j", "d"], "three", Mode.NORMAL)


def test_counts_and_dollar():
    _drive("abcdef", ["escape", "0", "2", "l", "D"], "ab", Mode.NORMAL)


def test_normal_mode_blocks_typing():
    _drive("abc", ["escape", "0", "z", "q"], "abc", Mode.NORMAL)


class TestImageMarkerAtoms:
    """Image markers behave as one character under vim motions/edits."""

    def _setup(self, pilot, app, text_before="hello ", text_after=" world"):
        from pydantic_ai.messages import BinaryContent

        from oi.tui.app import ChatInput

        chat_input = app.query_one(ChatInput)
        chat_input.set_vim_enabled(True)
        chat_input.focus()
        chat_input.insert(text_before)
        chat_input.attach_image(BinaryContent(data=b"x", media_type="image/png"))
        # attach_image adds a trailing space; drop it for exact assertions.
        chat_input.delete((0, len(chat_input.text) - 1), (0, len(chat_input.text)))
        chat_input.insert(text_after)
        return chat_input

    def test_l_steps_over_the_whole_marker(self):
        async def scenario():
            app = _make_input()
            async with app.run_test() as pilot:
                chat_input = self._setup(pilot, app)
                await pilot.pause()
                for key in ["escape", "0"] + ["l"] * 6:
                    await pilot.press(key)
                # Cursor sits at the marker start after 6 rights.
                assert chat_input.cursor_location == (0, 6)
                await pilot.press("l")
                # One more right clears the entire marker.
                assert chat_input.cursor_location == (0, 16)

        asyncio.run(scenario())

    def test_h_steps_back_over_the_whole_marker(self):
        async def scenario():
            app = _make_input()
            async with app.run_test() as pilot:
                chat_input = self._setup(pilot, app)
                await pilot.pause()
                for key in ["escape", "0"] + ["l"] * 7:
                    await pilot.press(key)
                assert chat_input.cursor_location == (0, 16)
                await pilot.press("h")
                assert chat_input.cursor_location == (0, 6)

        asyncio.run(scenario())

    def test_x_deletes_the_marker_whole(self):
        async def scenario():
            app = _make_input()
            async with app.run_test() as pilot:
                chat_input = self._setup(pilot, app)
                await pilot.pause()
                for key in ["escape", "0"] + ["l"] * 6 + ["x"]:
                    await pilot.press(key)
                assert chat_input.text == "hello  world"
                assert chat_input._images == {}

        asyncio.run(scenario())

    def test_operator_clipping_a_marker_takes_it_whole(self):
        async def scenario():
            app = _make_input()
            async with app.run_test() as pilot:
                # "hi [Image #1]x" — dw from inside "hi" would clip the marker.
                chat_input = self._setup(pilot, app, "hi ", "x")
                await pilot.pause()
                for key in ["escape", "0", "d", "w"]:
                    await pilot.press(key)
                # The marker is never left half-deleted.
                assert "[Image" not in chat_input.text or chat_input.text.startswith(
                    "[Image #1]"
                )

        asyncio.run(scenario())

    def test_visual_selection_covers_the_marker(self):
        async def scenario():
            app = _make_input()
            async with app.run_test() as pilot:
                chat_input = self._setup(pilot, app)
                await pilot.pause()
                for key in ["escape", "0", "v", "l", "l", "l", "l", "l", "l", "l"]:
                    await pilot.press(key)
                # Selection ends past the marker, never inside it.
                assert chat_input.selection.end == (0, 16)
                await pilot.press("d")
                # Visual is inclusive of the cursor char (the space at 16).
                assert chat_input.text == "world"
                assert chat_input._images == {}

        asyncio.run(scenario())


def test_mode_changes_are_reported():
    async def scenario():
        from textual import on
        from textual.app import App

        from oi.tui.app import ChatInput

        seen: list[Mode] = []

        class Harness(App):
            def compose(self):
                yield ChatInput()

            @on(ChatInput.MenuKey)
            def _on_menu_key(self, message: ChatInput.MenuKey) -> None:
                if message.action == "dismiss":
                    self.query_one(ChatInput).vim_escape()

            @on(ChatInput.VimModeChanged)
            def _on_mode(self, message: ChatInput.VimModeChanged) -> None:
                seen.append(message.mode)

        app = Harness()
        async with app.run_test() as pilot:
            chat_input = app.query_one(ChatInput)
            chat_input.set_vim_enabled(True)
            chat_input.insert("abc")
            chat_input.focus()
            await pilot.pause()
            for key in ["escape", "v", "escape", "i"]:
                await pilot.press(key)
            await pilot.pause()

        assert seen == [Mode.NORMAL, Mode.VISUAL, Mode.NORMAL, Mode.INSERT]

    asyncio.run(scenario())
