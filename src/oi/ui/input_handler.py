from prompt_toolkit.cursor_shapes import ModalCursorShapeConfig
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.shortcuts import CompleteStyle, PromptSession
from pydantic_ai.messages import UserContent

from oi.core.message_utils import render_user_prompt_content
from oi.llm_types import ModelCapabilities
from oi.local_commands import SlashCommandCompleter
from oi.ui.image_paste import (
    PasteStore,
    PillProcessor,
    read_clipboard_image,
)
from oi.ui.labels import USER_LABEL, ansi_message, ansi_pill, prompt_html_label

PASTE_LINE_THRESHOLD = 6
PASTE_CHAR_THRESHOLD = 400


class InputHandler:
    def __init__(self, config=None):
        self.config = config
        self.command_completer = SlashCommandCompleter()
        self.paste_store = PasteStore()
        self.pill_processor = PillProcessor(self.paste_store)

    def get_user_input(
        self, capabilities: ModelCapabilities | None = None
    ) -> str | list[UserContent]:
        """Get user input. Returns a plain str, or a list mixing text and images."""
        supports_vision = bool(capabilities and capabilities.supports_vision)
        bindings = KeyBindings()
        # Captured at Ctrl+C so we can echo the canceled buffer to scrollback
        # (erase_when_done wipes the prompt area on exit, including on cancel).
        canceled_text: dict[str, str] = {"value": ""}

        @bindings.add("c-m")  # Enter key
        def _(event):
            event.current_buffer.validate_and_handle()

        @bindings.add("c-j")  # Ctrl+J acts as Shift+Enter for newline
        def _(event):
            # Ctrl+J sends ASCII 0x0A (LF), same as Shift+Enter in most terminals
            # This provides a portable way to insert newlines across Unix/Linux/macOS
            event.app.current_buffer.insert_text("\n")

        @bindings.add("c-c")  # Ctrl+C
        def _(event):
            canceled_text["value"] = event.app.current_buffer.text
            event.app.exit(exception=KeyboardInterrupt)

        @bindings.add(Keys.BracketedPaste)
        def _(event):
            # Large pastes become pills so they don't get clobbered in scrollback
            # when total content exceeds terminal height (prompt_toolkit's
            # diff-based renderer can't progressively commit rows to scrollback).
            # Char threshold catches long single-line paragraphs that wrap.
            # Normalize to \n first: some terminals (iTerm2) send \r or \r\n
            # line endings in bracketed pastes, which breaks both the line
            # count below and the buffer rendering (\r rewinds the cursor).
            pasted = event.data.replace("\r\n", "\n").replace("\r", "\n")
            is_large = (
                pasted.count("\n") + 1 >= PASTE_LINE_THRESHOLD
                or len(pasted) >= PASTE_CHAR_THRESHOLD
            )
            if is_large:
                sentinel = self.paste_store.add_text(pasted)
                event.app.current_buffer.insert_text(sentinel)
            else:
                event.app.current_buffer.insert_text(pasted)

        if supports_vision:

            @bindings.add("escape", "v")
            def _(event):
                image = read_clipboard_image()
                if image is None:
                    return
                data, media_type = image
                sentinel = self.paste_store.add_image(data, media_type)
                event.app.current_buffer.insert_text(sentinel)

        try:
            vim_mode = self.config.vim_mode if self.config else False
            cursor_config = ModalCursorShapeConfig() if vim_mode else None
            session = PromptSession(
                prompt_html_label(USER_LABEL),
                multiline=True,
                key_bindings=bindings,
                prompt_continuation=lambda width, line_number, is_soft_wrap: "",
                vi_mode=vim_mode,
                cursor=cursor_config,
                completer=self.command_completer,
                complete_style=CompleteStyle.READLINE_LIKE,
                input_processors=[self.pill_processor],
                erase_when_done=True,
            )
            user_input = session.prompt()
        except KeyboardInterrupt:
            canceled = canceled_text["value"]
            pills = (
                self.paste_store.pill_text(canceled)
                if self.paste_store.has_entries
                else {}
            )
            displayed = "".join(
                ansi_pill(pills[ch]) if ch in pills else ch for ch in canceled
            )
            print(ansi_message(USER_LABEL, displayed))
            self.paste_store.reset()
            raise
        except EOFError:
            self.paste_store.reset()
            raise KeyboardInterrupt()

        has_pills = self.paste_store.has_entries and any(
            self.paste_store.is_sentinel(ch) for ch in user_input
        )
        parts: list[str | UserContent] | None = None
        if has_pills:
            parts = self.paste_store.split(user_input)
            display_text = render_user_prompt_content(parts, image_wrap=ansi_pill)
        else:
            display_text = user_input

        self.paste_store.reset()

        # Reprint the prompt line ourselves so the scrollback shows the fully
        # expanded message instead of the [Paste #N] / [Image #N] pills that
        # prompt_toolkit was rendering. erase_when_done=True wiped its draw,
        # and this print uses a normal terminal write — sidestepping
        # prompt_toolkit's diff renderer and its scrollback-clobbering issue.
        if display_text.strip():
            print(ansi_message(USER_LABEL, display_text))

        if parts is None:
            return user_input
        if len(parts) == 1 and isinstance(parts[0], str):
            return parts[0]
        return parts
