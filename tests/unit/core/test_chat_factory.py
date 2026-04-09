from unittest.mock import patch
from datetime import datetime

from llm_cli.core.session import Chat


def test_create_new_chat():
    now = datetime(2026, 2, 24, 15, 30, 45)
    with (
        patch("llm_cli.core.session.datetime") as mock_dt,
        patch("llm_cli.core.session.uuid") as mock_uuid,
    ):
        mock_dt.now.return_value = now
        mock_uuid.uuid4.return_value = "12345678-dead-beef-cafe-0123456789ab"

        chat = Chat.create_new("sonnet", "You are helpful.")

    assert chat.metadata.id == "20260224_153045_12345678"
    assert chat.metadata.title == "Chat 2026-02-24 15:30"
    assert chat.metadata.created_at == now
    assert chat.metadata.updated_at == now
    assert chat.metadata.model == "sonnet"
    assert chat.metadata.message_count == 0
    assert chat.pending_system_prompt == "You are helpful."
    assert chat.messages == []
