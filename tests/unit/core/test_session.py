import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from oi.config.settings import Config
from oi.core.message_utils import flatten_history
from oi.core.chat_repository import ChatRepository
from oi.core.session import Chat, ChatMetadata
from oi.exceptions import ChatNotFoundError
from oi.llm_types import ModelCapabilities


class TestChatMetadata:
    def test_create_metadata(self):
        now = datetime.now()
        metadata = ChatMetadata(
            id="test-123",
            title="Test Chat",
            created_at=now,
            updated_at=now,
            model="gpt-4o",
            message_count=2,
            bookmarked=True,
            smart_title_generated=False,
        )

        assert metadata.id == "test-123"
        assert metadata.title == "Test Chat"
        assert metadata.model == "gpt-4o"
        assert metadata.message_count == 2
        assert metadata.bookmarked
        assert not metadata.smart_title_generated

    def test_to_dict(self):
        now = datetime.now()
        metadata = ChatMetadata(
            id="test-123",
            title="Test Chat",
            created_at=now,
            updated_at=now,
            model="gpt-4o",
            message_count=2,
        )
        metadata.set_model_capabilities_snapshot(
            ModelCapabilities(
                supports_search=True,
                supports_thinking=True,
                max_tokens=8192,
                extra_params={"foo": {"bar": 1}},
            )
        )

        data = metadata.to_dict()
        assert data["id"] == "test-123"
        assert data["title"] == "Test Chat"
        assert data["model"] == "gpt-4o"
        assert data["message_count"] == 2
        assert data["bookmarked"] is False
        assert data["model_capabilities_snapshot"]["supports_search"] is True
        assert data["model_capabilities_snapshot"]["supports_thinking"] is True
        assert data["model_capabilities_snapshot"]["max_tokens"] == 8192
        assert data["model_capabilities_snapshot"]["extra_params"] == {
            "foo": {"bar": 1}
        }
        assert "created_at" in data
        assert "updated_at" in data

    def test_from_dict(self):
        data = {
            "id": "test-123",
            "title": "Test Chat",
            "created_at": "2024-01-01T12:00:00",
            "updated_at": "2024-01-01T12:30:00",
            "model": "gpt-4o",
            "message_count": 2,
            "bookmarked": True,
            "preview": "Hello world",
            "smart_title_generated": True,
            "model_capabilities_snapshot": {
                "supports_search": True,
                "supports_thinking": False,
                "max_tokens": 4096,
                "extra_params": {"temperature": 0.2},
            },
        }

        metadata = ChatMetadata.from_dict(data)
        assert metadata.id == "test-123"
        assert metadata.title == "Test Chat"
        assert metadata.model == "gpt-4o"
        assert metadata.message_count == 2
        assert metadata.bookmarked
        assert metadata.smart_title_generated
        snapshot = metadata.get_model_capabilities_snapshot()
        assert snapshot is not None
        assert snapshot.supports_search is True
        assert snapshot.supports_thinking is False
        assert snapshot.max_tokens == 4096
        assert snapshot.extra_params == {"temperature": 0.2}
        assert not hasattr(metadata, "preview")

    def test_from_dict_defaults_bookmark_to_false(self):
        data = {
            "id": "test-123",
            "title": "Test Chat",
            "created_at": "2024-01-01T12:00:00",
            "updated_at": "2024-01-01T12:30:00",
            "model": "gpt-4o",
            "message_count": 2,
        }

        metadata = ChatMetadata.from_dict(data)

        assert metadata.bookmarked is False


class TestChat:
    def test_should_be_saved_with_messages(self):
        metadata = ChatMetadata(
            id="test-123",
            title="Test",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            model="gpt-4o",
            message_count=2,
        )

        chat = Chat(metadata=metadata)
        chat.append_user_message("Hello")
        chat.append_assistant_response("Hi there!")

        assert chat.should_be_saved()

    def test_should_not_be_saved_empty(self):
        metadata = ChatMetadata(
            id="test-123",
            title="Test",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            model="gpt-4o",
            message_count=0,
        )

        chat = Chat(metadata=metadata, messages=[])
        assert not chat.should_be_saved()

    def test_should_not_be_saved_system_only(self):
        metadata = ChatMetadata(
            id="test-123",
            title="Test",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            model="gpt-4o",
            message_count=0,
        )

        chat = Chat(metadata=metadata)
        chat.pending_system_prompt = "You are a helpful assistant."

        assert not chat.should_be_saved()

    def test_discard_pending_user_message_restores_system_prompt(self):
        chat = Chat.create_new("gpt-4o", "You are terse.")
        chat.append_user_message("Hello")
        assert chat.pending_system_prompt is None

        chat.discard_pending_user_message()

        assert chat.messages == []
        assert chat.pending_system_prompt == "You are terse."
        # The next message must carry the system prompt again.
        chat.append_user_message("Hello again")
        roles = [type(part).__name__ for part in chat.messages[0].parts]
        assert roles == ["SystemPromptPart", "UserPromptPart"]

    def test_discard_pending_user_message_keeps_prompt_unset_mid_chat(self):
        chat = Chat.create_new("gpt-4o", "You are terse.")
        chat.append_user_message("Hello")
        chat.append_assistant_response("Hi!")
        chat.append_user_message("Second question")

        chat.discard_pending_user_message()

        assert len(chat.messages) == 2  # first exchange intact
        assert chat.pending_system_prompt is None

    def test_discard_pending_user_message_ignores_trailing_response(self):
        chat = Chat.create_new("gpt-4o", "You are terse.")
        chat.append_user_message("Hello")
        chat.append_assistant_response("Hi!")

        chat.discard_pending_user_message()

        assert len(chat.messages) == 2


class TestChatRepository:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ChatRepository(Config(chat_dir=temp_dir, vim_mode=False))

            metadata = ChatMetadata(
                id="test-123",
                title="Test Chat",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                model="gpt-4o",
                message_count=2,
            )

            chat = Chat(metadata=metadata)
            chat.append_user_message("Hello")
            chat.append_assistant_response("Hi there!")
            repository.save_chat(chat)

            chat_dir = Path(temp_dir) / "test-123"
            assert chat_dir.exists()
            assert (chat_dir / "metadata.json").exists()
            assert (chat_dir / "messages.json").exists()

            loaded_chat = repository.load_chat("test-123")
            assert loaded_chat.metadata.id == "test-123"
            assert loaded_chat.metadata.title == "Test Chat"
            assert len(loaded_chat.messages) == 2
            history = flatten_history(loaded_chat.messages)
            assert history[0][0] == "user"
            assert history[0][1] == "Hello"

    def test_load_nonexistent_chat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ChatRepository(Config(chat_dir=temp_dir, vim_mode=False))

            with pytest.raises(ChatNotFoundError) as exc_info:
                repository.load_chat("nonexistent-id")

            assert "Chat not found: nonexistent-id" in str(exc_info.value)

    def test_try_load_chat_returns_none_and_reports_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ChatRepository(Config(chat_dir=temp_dir, vim_mode=False))
            on_error = Mock()

            loaded_chat = repository.try_load_chat("nonexistent-id", on_error=on_error)

            assert loaded_chat is None
            on_error.assert_called_once()
            assert on_error.call_args[0][0] == "nonexistent-id"
            assert isinstance(on_error.call_args[0][1], ChatNotFoundError)

    def test_list_chat_metadata_sorts_and_skips_unreadable_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ChatRepository(Config(chat_dir=temp_dir, vim_mode=False))

            older = ChatMetadata(
                id="older",
                title="Older Chat",
                created_at=datetime.fromisoformat("2024-01-01T10:00:00"),
                updated_at=datetime.fromisoformat("2024-01-01T10:00:00"),
                model="gpt-4o",
                message_count=2,
            )
            newer = ChatMetadata(
                id="newer",
                title="Newer Chat",
                created_at=datetime.fromisoformat("2024-01-02T10:00:00"),
                updated_at=datetime.fromisoformat("2024-01-02T10:00:00"),
                model="gpt-4o",
                message_count=4,
            )

            for metadata in (older, newer):
                chat_dir = repository.chat_path(metadata.id)
                chat_dir.mkdir(parents=True)
                (chat_dir / "metadata.json").write_text(json.dumps(metadata.to_dict()))

            bad_dir = repository.chat_path("broken")
            bad_dir.mkdir(parents=True)
            (bad_dir / "metadata.json").write_text("{not json")

            on_unreadable_metadata = Mock()

            chats = repository.list_chat_metadata(
                on_unreadable_metadata=on_unreadable_metadata
            )

            assert [chat.id for chat in chats] == ["newer", "older"]
            on_unreadable_metadata.assert_called_once()
            assert on_unreadable_metadata.call_args[0][0] == "broken"

    def test_list_chat_metadata_reports_root_read_error(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ChatRepository(Config(chat_dir=temp_dir, vim_mode=False))
            on_root_read_error = Mock()

            def raise_oserror(self):
                raise OSError("boom")

            monkeypatch.setattr(Path, "iterdir", raise_oserror)

            chats = repository.list_chat_metadata(on_root_read_error=on_root_read_error)

            assert chats == []
            on_root_read_error.assert_called_once()
            assert on_root_read_error.call_args[0][0] == repository.chat_root

    def test_save_metadata_updates_bookmark_without_touching_timestamp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ChatRepository(Config(chat_dir=temp_dir, vim_mode=False))

            created_at = datetime.fromisoformat("2024-01-01T10:00:00")
            updated_at = datetime.fromisoformat("2024-01-02T10:00:00")
            metadata = ChatMetadata(
                id="test-123",
                title="Test Chat",
                created_at=created_at,
                updated_at=updated_at,
                model="gpt-4o",
                message_count=2,
            )

            chat = Chat(metadata=metadata)
            chat.append_user_message("Hello")
            chat.append_assistant_response("Hi there!")
            repository.save_chat(chat)

            chat.metadata.bookmarked = True
            previous_updated_at = chat.metadata.updated_at
            repository.save_metadata(chat.metadata)

            loaded_chat = repository.load_chat("test-123")
            assert loaded_chat.metadata.bookmarked is True
            assert loaded_chat.metadata.updated_at == previous_updated_at


def _request(text: str, system: str | None = None):
    from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart

    parts = [SystemPromptPart(system)] if system else []
    parts.append(UserPromptPart(text))
    return ModelRequest(parts=parts)


def _response(text: str):
    from pydantic_ai.messages import ModelResponse, TextPart

    return ModelResponse(parts=[TextPart(content=text)])


def _texts(messages):
    return [content for _, content in flatten_history(messages)]


def _chat(*turns: str, system: str | None = None) -> Chat:
    chat = Chat.create_new("m", system or "")
    for i, text in enumerate(turns):
        chat.messages.append(_request(text, system if i == 0 else None))
        chat.messages.append(_response(f"re {text}"))
    chat.pending_system_prompt = None
    return chat


class TestBranches:
    def test_fork_parks_the_tail_and_starts_a_sibling(self):
        chat = _chat("a", "b", "c")
        displaced = chat.fork_at(2, "b2")

        assert _texts(chat.messages) == ["a", "re a", "b2"]
        assert _texts(displaced.tail) == ["b", "re b", "c", "re c"]
        assert chat.branches == [displaced]
        assert chat.sibling_position(2) == (2, 2)
        assert chat.sibling_position(0) == (1, 1)

    def test_switch_swaps_the_tails(self):
        chat = _chat("a", "b", "c")
        chat.fork_at(2, "b2")
        chat.messages.append(_response("re b2"))
        (old,) = chat.branches

        chat.switch_to(old)
        assert _texts(chat.messages) == ["a", "re a", "b", "re b", "c", "re c"]
        assert chat.sibling_position(2) == (1, 2)
        (new,) = chat.branches
        assert _texts(new.tail) == ["b2", "re b2"]

        chat.switch_to(new)
        assert _texts(chat.messages) == ["a", "re a", "b2", "re b2"]

    def test_nested_forks_travel_with_their_branch(self):
        chat = _chat("a", "b", "c")
        chat.fork_at(4, "c2")  # sibling of c, under b
        chat.messages.append(_response("re c2"))
        chat.fork_at(2, "b2")  # displaces b (and the c/c2 fork with it)
        chat.messages.append(_response("re b2"))

        assert [b.at for b in chat.branches] == [2]
        (b_branch,) = chat.branches
        assert [b.at for b in b_branch.branches] == [4]

        chat.switch_to(b_branch)
        assert _texts(chat.messages) == ["a", "re a", "b", "re b", "c2", "re c2"]
        assert sorted(b.at for b in chat.branches) == [2, 4]
        assert chat.sibling_position(4) == (2, 2)

    def test_fork_at_the_first_message_carries_the_system_prompt(self):
        chat = _chat("a", "b", system="sys")
        chat.fork_at(0, "a2")

        assert chat.pending_system_prompt is None
        from pydantic_ai.messages import SystemPromptPart

        assert isinstance(chat.messages[0].parts[0], SystemPromptPart)
        assert chat.messages[0].parts[0].content == "sys"

    def test_switching_back_after_an_unsent_fork_leaves_no_trace(self):
        chat = _chat("a", "b", system="sys")
        displaced = chat.fork_at(0, "a2")
        chat.discard_pending_user_message()
        assert chat.pending_system_prompt == "sys"

        chat.switch_to(displaced)
        assert chat.branches == []
        assert chat.pending_system_prompt is None
        assert _texts(chat.messages) == ["a", "re a", "b", "re b"]

    def test_branches_round_trip_through_the_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = ChatRepository(Config(chat_dir=tmp))
            chat = _chat("a", "b")
            chat.fork_at(2, "b2")
            chat.messages.append(_response("re b2"))
            repo.save_chat(chat)

            assert (Path(tmp) / chat.metadata.id / "branches.json").exists()
            loaded = repo.load_chat(chat.metadata.id)
            assert _texts(loaded.messages) == ["a", "re a", "b2", "re b2"]
            assert len(loaded.branches) == 1
            assert _texts(loaded.branches[0].tail) == ["b", "re b"]

            loaded.switch_to(loaded.branches[0])
            loaded.branches.clear()
            repo.save_chat(loaded)
            assert not (Path(tmp) / chat.metadata.id / "branches.json").exists()
