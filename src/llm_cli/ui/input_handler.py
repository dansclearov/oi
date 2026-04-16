from prompt_toolkit import prompt
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.cursor_shapes import ModalCursorShapeConfig
from prompt_toolkit.shortcuts import CompleteStyle
from pydantic_ai.messages import UserContent

from llm_cli.llm_types import ModelCapabilities
from llm_cli.local_commands import SlashCommandCompleter
from llm_cli.ui.image_paste import (
    ImagePasteStore,
    ImagePillProcessor,
    read_clipboard_image,
)
from llm_cli.ui.labels import USER_LABEL, prompt_html_label


class InputHandler:
    def __init__(self, config=None):
        self.config = config
        self.command_completer = SlashCommandCompleter()
        self.image_store = ImagePasteStore()
        self.pill_processor = ImagePillProcessor(self.image_store)

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

        if supports_vision:

            @bindings.add("escape", "v")
            def _(event):
                image = read_clipboard_image()
                if image is None:
                    return
                data, media_type = image
                sentinel = self.image_store.add(data, media_type)
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
            self.image_store.reset()
            raise
        except EOFError:
            self.image_store.reset()
            raise KeyboardInterrupt()

        if not self.image_store.has_images or not any(
            self.image_store.is_sentinel(ch) for ch in user_input
        ):
            self.image_store.reset()
            return user_input

        parts = self.image_store.split(user_input)
        self.image_store.reset()
        if len(parts) == 1 and isinstance(parts[0], str):
            return parts[0]
        return parts
