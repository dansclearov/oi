"""Persistence layer for chat sessions."""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from llm_cli.config.settings import Config
from llm_cli.core.message_utils import (
    convert_legacy_messages,
    count_non_system_messages,
    deserialize_model_messages,
    serialize_model_messages,
)
from llm_cli.core.session import Chat, ChatMetadata
from llm_cli.exceptions import ChatNotFoundError

CHAT_LOAD_EXCEPTIONS = (
    ChatNotFoundError,
    OSError,
    json.JSONDecodeError,
    KeyError,
    TypeError,
    ValueError,
)


class ChatRepository:
    """Handles chat persistence to the filesystem."""

    def __init__(self, config: Config):
        self.config = config

    @property
    def chat_root(self) -> Path:
        return Path(self.config.chat_dir)

    def chat_path(self, chat_id: str) -> Path:
        return self.chat_root / chat_id

    def list_chat_metadata(
        self,
        *,
        on_root_read_error: Optional[Callable[[Path, OSError], None]] = None,
        on_unreadable_metadata: Optional[Callable[[str, Exception], None]] = None,
    ) -> list[ChatMetadata]:
        """List chats by metadata, sorted by newest first."""
        chat_dir = self.chat_root
        if not chat_dir.exists():
            return []

        try:
            chat_folders = list(chat_dir.iterdir())
        except OSError as exc:
            if on_root_read_error is not None:
                on_root_read_error(chat_dir, exc)
            return []

        chats: list[ChatMetadata] = []
        for chat_folder in chat_folders:
            if not chat_folder.is_dir():
                continue

            metadata_file = chat_folder / "metadata.json"
            if not metadata_file.exists():
                continue

            try:
                with open(metadata_file, "r") as f:
                    metadata = ChatMetadata.from_dict(json.load(f))
                    chats.append(metadata)
            except (
                OSError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                if on_unreadable_metadata is not None:
                    on_unreadable_metadata(chat_folder.name, exc)
                continue

        return sorted(chats, key=lambda c: c.updated_at, reverse=True)

    def save_chat(self, chat: Chat) -> None:
        """Persist a chat if it contains user/assistant messages."""
        if not chat.should_be_saved():
            return

        chat_dir = self.chat_path(chat.metadata.id)
        chat_dir.mkdir(parents=True, exist_ok=True)

        chat.metadata.updated_at = datetime.now()
        chat.metadata.message_count = count_non_system_messages(chat.messages)

        messages_payload = serialize_model_messages(chat.messages)

        self._write_metadata(chat_dir, chat.metadata)

        with open(chat_dir / "messages.json", "w") as f:
            json.dump(messages_payload, f, indent=2)

    def save_metadata(self, metadata: ChatMetadata) -> None:
        """Persist chat metadata without touching message files or timestamps."""
        chat_dir = self.chat_path(metadata.id)
        if not chat_dir.exists():
            raise ChatNotFoundError(f"Chat not found: {metadata.id}")

        self._write_metadata(chat_dir, metadata)

    def load_chat(self, chat_id: str) -> Chat:
        """Load a chat from disk."""
        chat_dir = self.chat_path(chat_id)
        if not chat_dir.exists():
            raise ChatNotFoundError(f"Chat not found: {chat_id}")

        with open(chat_dir / "metadata.json", "r") as f:
            metadata = ChatMetadata.from_dict(json.load(f))

        with open(chat_dir / "messages.json", "r") as f:
            raw_messages = json.load(f)

        if (
            raw_messages
            and isinstance(raw_messages, list)
            and isinstance(raw_messages[0], dict)
            and "kind" in raw_messages[0]
        ):
            messages = deserialize_model_messages(raw_messages)
        else:
            messages = convert_legacy_messages(raw_messages)

        return Chat(metadata=metadata, messages=messages)

    def try_load_chat(
        self,
        chat_id: str,
        *,
        on_error: Optional[Callable[[str, Exception], None]] = None,
    ) -> Optional[Chat]:
        """Best-effort chat load for flows that should skip unreadable entries."""
        try:
            return self.load_chat(chat_id)
        except CHAT_LOAD_EXCEPTIONS as exc:
            if on_error is not None:
                on_error(chat_id, exc)
            return None

    def delete_chat(self, chat_id: str) -> None:
        """Delete a chat by moving it to trash when available."""
        chat_dir = self.chat_path(chat_id)
        if not chat_dir.exists():
            return

        try:
            import send2trash

            send2trash.send2trash(str(chat_dir))
            return
        except ImportError:
            pass

        shutil.rmtree(chat_dir)

    def _write_metadata(self, chat_dir: Path, metadata: ChatMetadata) -> None:
        metadata_payload = metadata.to_dict()
        with open(chat_dir / "metadata.json", "w") as f:
            json.dump(metadata_payload, f, indent=2)
