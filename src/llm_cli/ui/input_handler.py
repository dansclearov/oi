from prompt_toolkit import prompt
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.cursor_shapes import ModalCursorShapeConfig
from prompt_toolkit.shortcuts import CompleteStyle
from pydantic_ai.messages import UserContent

from llm_cli.llm_types import ModelCapabilities
from llm_cli.local_commands import SlashCommandCompleter
from llm_cli.ui.image_paste import (
    PasteStore,
    PillProcessor,
    read_clipboard_image,
)
from llm_cli.ui.labels import USER_LABEL, prompt_html_label

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
            event.app.exit(exception=KeyboardInterrupt)

        @bindings.add(Keys.BracketedPaste)
        def _(event):
            # Large pastes become pills so they don't get clobbered in scrollback
            # when total content exceeds terminal height (prompt_toolkit's
            # diff-based renderer can't progressively commit rows to scrollback).
            # Char threshold catches long single-line paragraphs that wrap.
            pasted = event.data
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
            user_input = prompt(
                prompt_html_label(USER_LABEL),
                multiline=True,
                key_bindings=bindings,
                prompt_continuation=lambda width, line_number, is_soft_wrap: "",
                vi_mode=vim_mode,
                cursor=cursor_config,
                completer=self.command_completer,
                complete_style=CompleteStyle.READLINE_LIKE,
                input_processors=[self.pill_processor],
            )
        except KeyboardInterrupt:
            self.paste_store.reset()
            raise
        except EOFError:
            self.paste_store.reset()
            raise KeyboardInterrupt()

        if not self.paste_store.has_entries or not any(
            self.paste_store.is_sentinel(ch) for ch in user_input
        ):
            self.paste_store.reset()
            return user_input

        parts = self.paste_store.split(user_input)
        self.paste_store.reset()
        if len(parts) == 1 and isinstance(parts[0], str):
            return parts[0]
        return parts
