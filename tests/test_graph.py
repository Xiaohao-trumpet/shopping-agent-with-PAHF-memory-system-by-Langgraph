"""Tests for PAHF-based LangGraph flow."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
from unittest.mock import Mock

from backend.agents.graph import create_chat_graph, create_generation_graph, create_memory_writeback_graph
from backend.pahf_memory.service import PAHFMemoryService


class FakeEmbedding:
    """Deterministic embedding stub compatible with PAHF MemoryBank API."""

    @staticmethod
    def _vector(text: str) -> list[float]:
        text = (text or "").lower()
        return [
            float("shoe" in text),
            float("size" in text),
            float("name" in text),
            float("xiaohao" in text),
            float("30" in text),
            float("31" in text),
        ]

    def embed_documents(self, texts):
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)


class FakeLLM:
    """Prompt-driven stub for PAHF pre-clarification and extraction loops."""

    def generate(self, prompt: str, temperature: float = 0.0, max_tokens: int = 256) -> str:
        if "Decision: ASK or PROCEED" in prompt:
            return "Decision: PROCEED\nQuestion:"

        if "Store: YES or NO" in prompt:
            lower = prompt.lower()
            if "actually my shoe size is 31" in lower:
                return "Store: YES\nSummary: Xiaohao shoe size is 31."
            if "my name is xiaohao and my shoe size is 30" in lower:
                return "Store: YES\nSummary: Xiaohao shoe size is 30."
            return "Store: NO\nSummary:"

        if "Are these two memories about the same user-preference topic/domain?" in prompt:
            return "Yes"

        if "Please create a concise, integrated summary" in prompt:
            marker = "New information:"
            if marker in prompt:
                return prompt.split(marker, 1)[1].strip().splitlines()[0]
            return "Merged memory."

        return "No"


def _tmp_dir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"{prefix}_"))


def build_service(tmp_dir: Path) -> PAHFMemoryService:
    return PAHFMemoryService(
        backend="sqlite",
        sqlite_db_path=str(tmp_dir / "pahf.db"),
        faiss_path=str(tmp_dir / "pahf_index"),
        top_k=5,
        similarity_threshold=0.2,
        llm_client=FakeLLM(),
        query_encoder="unused-query",
        context_encoder="unused-context",
        device=None,
        enable_pre_clarification=True,
        enable_post_correction=True,
        embedding_model=FakeEmbedding(),
    )


def test_graph_executes_pahf_memory_flow():
    tmp_dir = _tmp_dir("graph_pahf")
    try:
        mock_model = Mock()
        mock_model.chat = Mock(return_value="Assistant reply")
        service = build_service(tmp_dir)

        graph = create_chat_graph(
            model_client=mock_model,
            pahf_memory_service=service,
            tool_planner=None,
            tool_executor=None,
            tool_registry=None,
            prompt_builder=None,
            tools_enabled=False,
        )

        first = graph.invoke(
            {
                "user_id": "u1",
                "user_message": "My name is Xiaohao and my shoe size is 30.",
                "response": None,
                "temperature": None,
                "max_tokens": None,
                "session": None,
            }
        )
        assert first["memory_update"]["updated"] is True
        assert first["memory_update"]["action"] == "added"

        second = graph.invoke(
            {
                "user_id": "u1",
                "user_message": "What is my shoe size?",
                "response": None,
                "temperature": None,
                "max_tokens": None,
                "session": None,
            }
        )
        assert second["retrieved_memories"]
        assert "shoe size is 30" in second["retrieved_memories"][0]["text"].lower()

        third = graph.invoke(
            {
                "user_id": "u1",
                "user_message": "Actually my shoe size is 31.",
                "response": None,
                "temperature": None,
                "max_tokens": None,
                "session": None,
            }
        )
        assert third["memory_update"]["updated"] is True
        assert third["memory_update"]["action"] == "updated"

        memories = service.get_all_memories("u1")
        assert len(memories) == 1
        assert "31" in memories[0].text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_generation_graph_stops_before_memory_writeback():
    """The generation-only graph (used by the live serving paths) must return
    a response without running PAHF's post-action extraction/update -- those
    run afterwards, out of the request's critical path, via a separate graph."""
    tmp_dir = _tmp_dir("graph_split")
    try:
        mock_model = Mock()
        mock_model.chat = Mock(return_value="Assistant reply")
        service = build_service(tmp_dir)

        gen_graph = create_generation_graph(
            model_client=mock_model,
            pahf_memory_service=service,
            tool_planner=None,
            tool_executor=None,
            tool_registry=None,
            prompt_builder=None,
            tools_enabled=False,
        )

        result = gen_graph.invoke(
            {
                "user_id": "u2",
                "user_message": "My name is Xiaohao and my shoe size is 30.",
                "response": None,
                "temperature": None,
                "max_tokens": None,
                "session": None,
            }
        )

        assert result["response"] == "Assistant reply"
        assert "memory_candidate" not in result
        assert "memory_update" not in result
        # Nothing has been written to memory yet -- extraction/update haven't run.
        assert service.get_all_memories("u2") == []

        writeback_graph = create_memory_writeback_graph(service)
        final = writeback_graph.invoke(result)

        assert final["memory_update"]["updated"] is True
        assert final["memory_update"]["action"] == "added"
        memories = service.get_all_memories("u2")
        assert len(memories) == 1
        assert "30" in memories[0].text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
