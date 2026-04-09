"""Chat management with auto-save and smart title generation."""

from functools import partial
from typing import Optional

from rich.console import Console

from llm_cli.config.settings import Config
from llm_cli.core.chat_repository import ChatRepository
from llm_cli.core.smart_title import SmartTitleGenerator
from llm_cli.core.session import Chat, ChatMetadata
from llm_cli.exceptions import ChatNotFoundError
from llm_cli.ui.chat_selector import ChatSelector
from llm_cli.ui.labels import ERROR_LABEL, WARNING_LABEL, rich_message


class ChatManager:
    """Manages chat sessions with auto-save and interactive selection."""

    def __init__(self, config: Config):
        self.config = config
        self.repository = ChatRepository(config)
        self.smart_title_generator = SmartTitleGenerator()
        # Disable Rich's automatic syntax highlighting - it makes timestamps look like code
        self.console = Console(highlight=False)
        self.chat_selector = ChatSelector(self.console)

    def create_new_chat(self, model: str, system_message: str) -> Chat:
        """Create a new empty chat session."""
        return Chat.create_new(model, system_message)

    def list_chats(self) -> list[ChatMetadata]:
        """List all available chats, sorted by updated_at desc."""
        return self.repository.list_chat_metadata(
            on_root_read_error=self._on_chat_dir_read_error,
            on_unreadable_metadata=self._on_unreadable_chat_metadata,
        )

    def load_chat(self, chat_id: str) -> Chat:
        """Load a chat by ID."""
        return self.repository.load_chat(chat_id)

    def save_chat(self, chat: Chat) -> None:
        """Persist a chat to disk."""
        self.repository.save_chat(chat)

    def save_metadata(self, metadata: ChatMetadata) -> None:
        """Persist metadata without rewriting message history."""
        self.repository.save_metadata(metadata)

    def delete_chat(self, chat_id: str) -> None:
        """Delete a chat by moving it to trash when possible."""
        try:
            self.repository.delete_chat(chat_id)
        except OSError as exc:
            self.console.print(
                rich_message(
                    ERROR_LABEL,
                    f"Could not delete chat {chat_id}: {type(exc).__name__}",
                    dim=True,
                )
            )

    def get_last_chat(self) -> Optional[Chat]:
        """Get the most recently updated chat."""
        chats = self.list_chats()
        for chat_metadata in chats:
            loaded_chat = self.repository.try_load_chat(
                chat_metadata.id,
                on_error=partial(
                    self._on_chat_load_error,
                    "Skipping unreadable chat during --continue",
                ),
            )
            if loaded_chat is not None:
                return loaded_chat
        return None

    def interactive_chat_selection(self) -> Optional[Chat]:
        """Interactive chat selection with keyboard navigation."""
        chats = self.list_chats()
        return self.chat_selector.select_chat(
            chats,
            load_chat=self._load_chat_for_selector,
            delete_chat=self.delete_chat,
            toggle_bookmark=self.toggle_bookmark,
        )

    def toggle_bookmark(self, target: Chat | ChatMetadata) -> bool | None:
        """Toggle bookmark state and persist it without changing sort order."""
        metadata = target.metadata if isinstance(target, Chat) else target
        previous_value = metadata.bookmarked
        metadata.bookmarked = not previous_value
        try:
            self.save_metadata(metadata)
        except (ChatNotFoundError, OSError) as exc:
            metadata.bookmarked = previous_value
            self.console.print(
                rich_message(
                    ERROR_LABEL,
                    f"Could not update bookmark for {metadata.id}: "
                    f"{type(exc).__name__}",
                    dim=True,
                )
            )
            return None

        return metadata.bookmarked

    def generate_smart_title(self, chat: Chat, llm_client, model: str) -> None:
        """Generate a better title using LLM for chats with >3 message pairs."""
        # Caller should check smart_title_generated flag
        try:
            new_title = self.smart_title_generator.generate(chat, llm_client, model)
            if new_title and new_title != chat.metadata.title:
                chat.metadata.title = new_title

            self._mark_title_generation_attempted(chat)
        except Exception as exc:
            self.console.print(
                rich_message(
                    WARNING_LABEL,
                    f"Smart title generation skipped: {type(exc).__name__}",
                    dim=True,
                )
            )
            self._mark_title_generation_attempted(chat)

    def _mark_title_generation_attempted(self, chat: Chat) -> None:
        """Persist that smart-title generation has been attempted."""
        chat.metadata.smart_title_generated = True
        try:
            self.save_chat(chat)
        except OSError as exc:
            self.console.print(
                rich_message(
                    ERROR_LABEL,
                    f"Could not persist smart-title status: {type(exc).__name__}",
                    dim=True,
                )
            )

    def _load_chat_for_selector(self, chat_id: str) -> Optional[Chat]:
        """Load a chat for interactive selection, returning None on unreadable entries."""
        return self.repository.try_load_chat(
            chat_id,
            on_error=partial(self._on_chat_load_error, "Skipping unreadable chat"),
        )

    def _on_chat_dir_read_error(self, chat_dir, exc: OSError) -> None:
        self.console.print(
            rich_message(
                ERROR_LABEL,
                f"Unable to read chat directory {chat_dir}: {exc}",
                dim=True,
            )
        )

    def _on_unreadable_chat_metadata(self, folder_name: str, exc: Exception) -> None:
        self.console.print(
            rich_message(
                WARNING_LABEL,
                f"Skipping unreadable chat metadata in {folder_name}: "
                f"{type(exc).__name__}",
                dim=True,
            )
        )

    def _on_chat_load_error(self, prefix: str, chat_id: str, exc: Exception) -> None:
        self.console.print(
            rich_message(
                WARNING_LABEL,
                f"{prefix}: {chat_id} ({type(exc).__name__})",
                dim=True,
            )
        )
