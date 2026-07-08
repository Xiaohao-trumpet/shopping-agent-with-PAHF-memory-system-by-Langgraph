import pytest

from backend.realtime.conversation_store import ConversationStore
from backend.realtime.events import EventBus
from backend.realtime.service import ChatService


class DummyGraph:
    def invoke(self, state):
        return {**state, "response": "AI response"}


@pytest.mark.asyncio
async def test_handoff_request_survives_store_reopen_and_agent_can_reply(tmp_path):
    db_path = tmp_path / "conversations.db"
    service = ChatService(
        conversations=ConversationStore(str(db_path)),
        event_bus=EventBus(),
        chat_graph=DummyGraph(),
    )

    result = await service.handle_customer_message("c1001", "我要转人工")
    assert result["status"] == "queued"
    conversation_id = result["conversation_id"]

    reopened_store = ConversationStore(str(db_path))
    queued = reopened_store.list_conversations(status="queued")
    assert [row["conversation_id"] for row in queued] == [conversation_id]

    service_after_refresh = ChatService(
        conversations=reopened_store,
        event_bus=EventBus(),
        chat_graph=DummyGraph(),
    )
    claimed = await service_after_refresh.claim(conversation_id, "agent-1", "客服小美")
    assert claimed["status"] == "human"
    assert claimed["assigned_agent"] == "agent-1"

    reply = await service_after_refresh.agent_send(conversation_id, "agent-1", "您好，我来帮您处理。")
    assert reply["role"] == "agent"

    final_store = ConversationStore(str(db_path))
    conversation = final_store.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation["status"] == "human"

    messages = final_store.list_messages(conversation_id)
    assert any(message["content"] == "您好，我来帮您处理。" for message in messages)


@pytest.mark.asyncio
async def test_claim_is_idempotent_for_same_agent(tmp_path):
    db_path = tmp_path / "conversations.db"
    store = ConversationStore(str(db_path))
    service = ChatService(
        conversations=store,
        event_bus=EventBus(),
        chat_graph=DummyGraph(),
    )
    conversation = store.get_or_create_active("c1002")
    conversation_id = conversation["conversation_id"]
    store.set_status(conversation_id, "queued")

    first = await service.claim(conversation_id, "agent-1", "Agent One")
    second = await service.claim(conversation_id, "agent-1", "Agent One")

    assert first["status"] == "human"
    assert second["status"] == "human"
    assert second["assigned_agent"] == "agent-1"
    system_messages = [
        message
        for message in store.list_messages(conversation_id)
        if message["role"] == "system"
    ]
    assert len(system_messages) == 1


@pytest.mark.asyncio
async def test_claim_can_restore_snapshot_on_fresh_serverless_instance(tmp_path):
    first_store = ConversationStore(str(tmp_path / "first.db"))
    first_service = ChatService(
        conversations=first_store,
        event_bus=EventBus(),
        chat_graph=DummyGraph(),
    )
    result = await first_service.handle_customer_message("c1003", "我要转人工")
    conversation_id = result["conversation_id"]
    snapshot = first_store.get_conversation(conversation_id)
    messages = first_store.list_messages(conversation_id)

    second_store = ConversationStore(str(tmp_path / "second.db"))
    second_service = ChatService(
        conversations=second_store,
        event_bus=EventBus(),
        chat_graph=DummyGraph(),
    )
    claimed = await second_service.claim(
        conversation_id,
        "agent-1",
        "Agent One",
        conversation_snapshot=snapshot,
        messages_snapshot=messages,
    )

    assert claimed["status"] == "human"
    assert claimed["assigned_agent"] == "agent-1"
    restored = second_store.get_conversation(conversation_id)
    assert restored is not None
    assert restored["customer_id"] == "c1003"
    assert len(second_store.list_messages(conversation_id)) >= len(messages)


@pytest.mark.asyncio
async def test_agent_send_can_restore_claimed_snapshot_on_fresh_serverless_instance(tmp_path):
    source_store = ConversationStore(str(tmp_path / "source.db"))
    source_service = ChatService(
        conversations=source_store,
        event_bus=EventBus(),
        chat_graph=DummyGraph(),
    )
    result = await source_service.handle_customer_message("c1004", "我要转人工")
    conversation_id = result["conversation_id"]
    claimed = await source_service.claim(conversation_id, "agent-1", "Agent One")
    assert claimed["status"] == "human"
    snapshot = source_store.get_conversation(conversation_id)
    messages = source_store.list_messages(conversation_id)

    fresh_store = ConversationStore(str(tmp_path / "fresh.db"))
    fresh_service = ChatService(
        conversations=fresh_store,
        event_bus=EventBus(),
        chat_graph=DummyGraph(),
    )
    reply = await fresh_service.agent_send(
        conversation_id,
        "agent-1",
        "您好，我来处理。",
        conversation_snapshot=snapshot,
        messages_snapshot=messages,
    )

    assert reply["role"] == "agent"
    assert fresh_store.get_conversation(conversation_id)["status"] == "human"
