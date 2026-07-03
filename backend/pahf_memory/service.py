"""PAHF-backed memory service integration for chat runtime."""

from __future__ import annotations

import re
import hashlib
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from ..config import AppConfig, ModelConfig
from ..utils.httpx_compat import patch_httpx_for_openai

patch_httpx_for_openai()
import numpy as np
from openai import OpenAI

try:
    from PAHF.memory.banks import DragonPlusEmbedding, FAISSMemoryBank, MemoryBank, SQLiteMemoryBank
    from PAHF.prompts.shopping_prompts import integration_prompt
except ModuleNotFoundError:
    DragonPlusEmbedding = None
    FAISSMemoryBank = None

    class MemoryBank:
        def add(self, text: str) -> None:
            raise NotImplementedError

        def search(self, query: str, top_k: int = 2) -> List[Tuple[float, str]]:
            raise NotImplementedError

        def find_similar_memory(self, text: str, threshold: Optional[float] = None) -> Optional[int]:
            raise NotImplementedError

        def update_memory(self, memory_id: int, new_text: str) -> None:
            raise NotImplementedError

        def get_memory(self, memory_id: int) -> Optional[str]:
            raise NotImplementedError

        def get_all_memories(self) -> List[Tuple[int, str]]:
            raise NotImplementedError

        def close(self) -> None:
            raise NotImplementedError

    class SQLiteMemoryBank(MemoryBank):
        """Small PAHF-compatible SQLite bank used when the PAHF package is not bundled."""

        def __init__(
            self,
            db_path: str = "shared_memory_bank.db",
            person_id: str = "",
            embedding_model=None,
        ):
            if person_id is None:
                raise ValueError("person_id is required for SQLiteMemoryBank")

            self.db_path = db_path
            self.person_id = person_id
            self.embeddings = embedding_model or HashEmbedding()
            db_dir = Path(db_path).parent
            if str(db_dir) not in {"", "."}:
                db_dir.mkdir(parents=True, exist_ok=True)

            self.conn = sqlite3.connect(db_path, check_same_thread=False)
            self.cursor = self.conn.cursor()
            self._create_table()

        def _create_table(self) -> None:
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS docs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    embedding BLOB NOT NULL
                )
                """
            )
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_person_id ON docs(person_id)")
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_person_text ON docs(person_id, text)")
            self.conn.commit()

        def _embed_document(self, text: str) -> np.ndarray:
            return np.array(self.embeddings.embed_documents([text])[0], dtype=np.float32)

        def _embed_query(self, text: str) -> np.ndarray:
            return np.array(self.embeddings.embed_query(text), dtype=np.float32)

        def add(self, text: str) -> None:
            self.cursor.execute(
                "SELECT 1 FROM docs WHERE person_id = ? AND text = ? LIMIT 1",
                (self.person_id, text),
            )
            if self.cursor.fetchone():
                return

            emb = self._embed_document(text)
            self.cursor.execute(
                "INSERT INTO docs (person_id, text, embedding) VALUES (?, ?, ?)",
                (self.person_id, text, emb.tobytes()),
            )
            self.conn.commit()

        def _rows_with_scores(self, query: str) -> List[Tuple[int, str, float]]:
            query_emb = self._embed_query(query)
            self.cursor.execute(
                "SELECT id, text, embedding FROM docs WHERE person_id = ?",
                (self.person_id,),
            )
            scored: List[Tuple[int, str, float]] = []
            for memory_id, text, raw_embedding in self.cursor.fetchall():
                embedding = np.frombuffer(raw_embedding, dtype=np.float32)
                if embedding.shape != query_emb.shape:
                    continue
                score = float(embedding @ query_emb)
                scored.append((int(memory_id), text, score))
            scored.sort(key=lambda row: row[2], reverse=True)
            return scored

        def search(self, query: str, top_k: int = 2) -> List[Tuple[float, str]]:
            return [(score, text) for _, text, score in self._rows_with_scores(query)[:top_k]]

        def find_similar_memory(self, text: str, threshold: Optional[float] = None) -> Optional[int]:
            rows = self._rows_with_scores(text)
            if not rows:
                return None

            memory_id, _, score = rows[0]
            if threshold is None or score > threshold:
                return memory_id
            return None

        def update_memory(self, memory_id: int, new_text: str) -> None:
            emb = self._embed_document(new_text)
            self.cursor.execute(
                """
                UPDATE docs
                SET text = ?, embedding = ?
                WHERE id = ? AND person_id = ?
                """,
                (new_text, emb.tobytes(), memory_id, self.person_id),
            )
            self.conn.commit()

        def get_memory(self, memory_id: int) -> Optional[str]:
            self.cursor.execute(
                "SELECT text FROM docs WHERE id = ? AND person_id = ?",
                (memory_id, self.person_id),
            )
            row = self.cursor.fetchone()
            return row[0] if row else None

        def get_all_memories(self) -> List[Tuple[int, str]]:
            self.cursor.execute(
                "SELECT id, text FROM docs WHERE person_id = ? ORDER BY id",
                (self.person_id,),
            )
            return [(int(memory_id), text) for memory_id, text in self.cursor.fetchall()]

        def close(self) -> None:
            self.conn.close()

    integration_prompt = (
        "Please create a concise integrated memory.\n"
        "Existing memory: {existing_memory}\n"
        "New information: {summ_info}\n"
        "Return only the updated memory sentence."
    )


@dataclass
class PahfMemoryItem:
    """PAHF memory item exposed to API and runtime."""

    id: int
    person_id: str
    text: str


@dataclass
class PahfMemorySearchHit:
    """PAHF search hit with score."""

    memory: PahfMemoryItem
    score: float


class PahfLLMClient:
    """Minimal OpenAI-compatible LLM client mirroring PAHF retry behavior."""

    def __init__(self, model: str, base_url: str, api_key: str):
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def generate(
        self,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> str:
        max_attempts = 5
        for i in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return (response.choices[0].message.content or "").strip()
            except Exception as exc:
                if i >= max_attempts - 1:
                    raise
                error_text = str(exc).lower()
                if "rate" in error_text or "limit" in error_text:
                    time.sleep(5 * (i + 1))
                else:
                    time.sleep(2 * (i + 1))
        raise RuntimeError("PAHF LLM generation failed unexpectedly")


class HashEmbedding:
    """Small deterministic embedding for lightweight SQLite deployments.

    It keeps PAHF memory searchable without requiring torch/transformers during
    demos or serverless deployments. DRAGON+ remains available by setting
    ``PAHF_EMBEDDING_MODE=dragon``.
    """

    def __init__(self, dimension: int = 96):
        self.dimension = max(16, int(dimension))

    def _tokens(self, text: str) -> List[str]:
        ascii_tokens = re.findall(r"[a-zA-Z0-9_]+", text.lower())
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
        return ascii_tokens + cjk_chars

    def _embed(self, text: str) -> List[float]:
        vector = [0.0] * self.dimension
        tokens = self._tokens(text)
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign

        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed(text)


class PAHFMemoryService:
    """PAHF-only memory service using official MemoryBank backends."""

    def __init__(
        self,
        *,
        backend: str,
        sqlite_db_path: str,
        faiss_path: str,
        top_k: int,
        similarity_threshold: Optional[float],
        llm_client: PahfLLMClient,
        query_encoder: str,
        context_encoder: str,
        device: Optional[str],
        enable_pre_clarification: bool,
        enable_post_correction: bool,
        embedding_mode: str = "hash",
        embedding_model: Any = None,
    ):
        normalized_backend = backend.strip().lower()
        if normalized_backend not in {"sqlite", "faiss"}:
            raise ValueError("PAHF_BACKEND must be one of: sqlite, faiss")

        self.backend = normalized_backend
        self.sqlite_db_path = str(Path(sqlite_db_path))
        self.faiss_path = str(Path(faiss_path))
        self.top_k = max(1, top_k)
        self.similarity_threshold = similarity_threshold
        self.llm_client = llm_client
        self.query_encoder = query_encoder
        self.context_encoder = context_encoder
        self.device = device
        self.embedding_mode = (embedding_mode or "hash").strip().lower()
        self.enable_pre_clarification = enable_pre_clarification
        self.enable_post_correction = enable_post_correction

        self._external_embedding_model = embedding_model
        self._shared_embedding_model: Optional[Any] = None
        self._banks: Dict[str, MemoryBank] = {}
        self._lock = Lock()

        self._prepare_paths()
        self._validate_backend_compatibility()

    def _prepare_paths(self) -> None:
        Path(self.sqlite_db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.faiss_path).parent.mkdir(parents=True, exist_ok=True)

    def _validate_backend_compatibility(self) -> None:
        if self.backend != "faiss":
            return
        if FAISSMemoryBank is None:
            raise ImportError(
                "PAHF_BACKEND=faiss requires the PAHF package and faiss dependency. "
                "Use PAHF_BACKEND=sqlite for lightweight Vercel deployments."
            )

        class _NoopEmbedding:
            def embed_documents(self, texts: List[str]) -> List[List[float]]:
                return [[0.0, 1.0] for _ in texts]

            def embed_query(self, text: str) -> List[float]:
                return [0.0, 1.0]

        bank = FAISSMemoryBank(
            embedding_model=_NoopEmbedding(),
            persistence_path=self.faiss_path,
            person_id="__pahf_backend_check__",
        )
        bank.close()

    def _get_embedding_model(self):
        if self._external_embedding_model is not None:
            return self._external_embedding_model
        if self._shared_embedding_model is None:
            if self.embedding_mode in {"dragon", "dragonplus", "dragon+"}:
                if DragonPlusEmbedding is None:
                    raise ImportError(
                        "PAHF_EMBEDDING_MODE=dragon requires the PAHF package plus torch/transformers. "
                        "Use PAHF_EMBEDDING_MODE=hash for lightweight deployments."
                    )
                self._shared_embedding_model = DragonPlusEmbedding(
                    query_encoder=self.query_encoder,
                    context_encoder=self.context_encoder,
                    device=self.device,
                )
            else:
                self._shared_embedding_model = HashEmbedding()
        return self._shared_embedding_model

    def _create_bank(self, person_id: str) -> MemoryBank:
        embedding_model = self._get_embedding_model()
        if self.backend == "sqlite":
            return SQLiteMemoryBank(
                db_path=self.sqlite_db_path,
                person_id=person_id,
                embedding_model=embedding_model,
            )
        return FAISSMemoryBank(
            embedding_model=embedding_model,
            use_dot_product=True,
            persistence_path=self.faiss_path,
            person_id=person_id,
        )

    def _bank(self, person_id: str) -> MemoryBank:
        if not person_id:
            raise ValueError("person_id is required")
        with self._lock:
            if person_id not in self._banks:
                self._banks[person_id] = self._create_bank(person_id=person_id)
            return self._banks[person_id]

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    def _all_items(self, person_id: str) -> List[PahfMemoryItem]:
        rows = self._bank(person_id).get_all_memories()
        return [PahfMemoryItem(id=int(mid), person_id=person_id, text=text) for mid, text in rows]

    def _find_id_by_text(self, person_id: str, text: str) -> Optional[int]:
        target = self._clean_text(text)
        if not target:
            return None
        for item in self._all_items(person_id):
            if self._clean_text(item.text) == target:
                return item.id
        return None

    @staticmethod
    def _parse_decision_line(output: str, key: str) -> str:
        for line in output.splitlines():
            if line.strip().lower().startswith(f"{key.lower()}:"):
                return line.split(":", 1)[1].strip()
        return ""

    def get_all_memories(self, person_id: str) -> List[PahfMemoryItem]:
        return self._all_items(person_id=person_id)

    def get_memory(self, person_id: str, memory_id: int) -> Optional[PahfMemoryItem]:
        text = self._bank(person_id).get_memory(memory_id)
        if text is None:
            return None
        return PahfMemoryItem(id=memory_id, person_id=person_id, text=text)

    def add_memory(self, person_id: str, text: str) -> PahfMemoryItem:
        normalized = self._clean_text(text)
        if not normalized:
            raise ValueError("Memory text cannot be empty")

        bank = self._bank(person_id)
        bank.add(normalized)
        memory_id = self._find_id_by_text(person_id, normalized)
        if memory_id is None:
            raise RuntimeError("PAHF add succeeded but memory ID was not found")
        return PahfMemoryItem(id=memory_id, person_id=person_id, text=normalized)

    def update_memory(self, person_id: str, memory_id: int, new_text: str) -> Optional[PahfMemoryItem]:
        normalized = self._clean_text(new_text)
        if not normalized:
            raise ValueError("Updated memory text cannot be empty")

        current = self.get_memory(person_id, memory_id)
        if current is None:
            return None
        self._bank(person_id).update_memory(memory_id, normalized)
        updated = self.get_memory(person_id, memory_id)
        return updated

    def search(self, person_id: str, query: str, top_k: Optional[int] = None) -> List[PahfMemorySearchHit]:
        q = self._clean_text(query)
        if not q:
            return []
        limit = max(1, top_k or self.top_k)
        hits = self._bank(person_id).search(q, top_k=limit)
        items = self._all_items(person_id)
        text_to_id: Dict[str, int] = {}
        for item in items:
            text_to_id.setdefault(self._clean_text(item.text), item.id)

        out: List[PahfMemorySearchHit] = []
        for score, text in hits:
            cleaned = self._clean_text(text)
            memory_id = text_to_id.get(cleaned)
            if memory_id is None:
                continue
            out.append(
                PahfMemorySearchHit(
                    memory=PahfMemoryItem(id=memory_id, person_id=person_id, text=text),
                    score=float(score),
                )
            )
        return out

    def find_similar_memory(
        self,
        person_id: str,
        text: str,
        threshold: Optional[float] = None,
    ) -> Optional[PahfMemoryItem]:
        normalized = self._clean_text(text)
        if not normalized:
            return None
        memory_id = self._bank(person_id).find_similar_memory(
            normalized,
            threshold=self.similarity_threshold if threshold is None else threshold,
        )
        if memory_id is None:
            return None
        return self.get_memory(person_id, int(memory_id))

    def retrieve_for_chat(self, person_id: str, user_message: str) -> List[PahfMemorySearchHit]:
        return self.search(person_id=person_id, query=user_message, top_k=self.top_k)

    def render_retrieval_context(self, hits: List[Any]) -> str:
        if not hits:
            return "No relevant personalized information found in memory."
        lines: List[str] = []
        for hit in hits:
            text = ""
            if isinstance(hit, dict):
                text = str(hit.get("text", ""))
            elif hasattr(hit, "memory") and getattr(hit.memory, "text", None):
                text = str(hit.memory.text)
            if text:
                lines.append(f"- {text}")
        if not lines:
            return "No relevant personalized information found in memory."
        return "\n".join(lines)

    def maybe_generate_pre_clarification(
        self,
        user_message: str,
        hits: List[PahfMemorySearchHit],
    ) -> Optional[str]:
        if not self.enable_pre_clarification:
            return None

        memory_context = self.render_retrieval_context(hits)
        prompt = (
            "You are the PAHF pre-action clarification controller.\n"
            "Decide if a clarifying question is needed BEFORE assistant action.\n"
            "Ask only when the user request is ambiguous or preference-dependent and memory is insufficient.\n\n"
            f"User message:\n{user_message}\n\n"
            f"Retrieved memory context:\n{memory_context}\n\n"
            "Output exactly two lines:\n"
            "Decision: ASK or PROCEED\n"
            "Question: <question text or empty>\n"
        )
        result = self.llm_client.generate(prompt=prompt, temperature=0.0, max_tokens=120)
        decision = self._parse_decision_line(result, "Decision").upper()
        question = self._parse_decision_line(result, "Question")

        if decision == "ASK":
            question = self._clean_text(question)
            if not question:
                raise RuntimeError("PAHF pre-action clarification requested ASK without a question")
            return question
        return None

    def extract_memory_candidate(
        self,
        person_id: str,
        user_message: str,
        assistant_message: str,
        hits: List[Any],
    ) -> Optional[str]:
        if not self.enable_post_correction:
            return None

        memory_context = self.render_retrieval_context(hits)
        prompt = (
            "You are the PAHF post-action correction extractor.\n"
            "Determine whether the latest user message reveals durable personal information.\n"
            "Include stable profile facts, preferences, constraints, and corrections like 'actually/now I prefer'.\n"
            "If no durable personalization signal exists, do not store memory.\n\n"
            f"Person ID: {person_id}\n"
            f"Latest user message:\n{user_message}\n\n"
            f"Assistant response:\n{assistant_message}\n\n"
            f"Retrieved memory context:\n{memory_context}\n\n"
            "Output exactly two lines:\n"
            "Store: YES or NO\n"
            "Summary: <one concise sentence when Store is YES, otherwise empty>\n"
        )
        result = self.llm_client.generate(prompt=prompt, temperature=0.0, max_tokens=180)
        store = self._parse_decision_line(result, "Store").upper()
        summary = self._clean_text(self._parse_decision_line(result, "Summary"))

        if store == "YES":
            if not summary:
                raise RuntimeError("PAHF extraction returned Store=YES without Summary")
            return summary
        return None

    def apply_memory_update(self, person_id: str, candidate_summary: str) -> Dict[str, Any]:
        if not self.enable_post_correction:
            return {"updated": False, "action": "post_correction_disabled"}

        candidate_summary = self._clean_text(candidate_summary)
        if not candidate_summary:
            return {"updated": False, "action": "empty_candidate"}

        bank = self._bank(person_id)
        similar = self.find_similar_memory(person_id, candidate_summary)
        if similar is None:
            created = self.add_memory(person_id, candidate_summary)
            return {"updated": True, "action": "added", "memory_id": created.id, "text": created.text}

        existing_text = similar.text
        product_check_prompt = (
            "Memory 1: {existing}\n"
            "Memory 2: {new}\n\n"
            "Are these two memories about the same user-preference topic/domain?\n"
            "Answer only Yes or No."
        ).format(existing=existing_text, new=candidate_summary)
        same_topic = self.llm_client.generate(
            prompt=product_check_prompt,
            temperature=0.0,
            max_tokens=16,
        )
        if "yes" in same_topic.lower():
            merge_prompt = integration_prompt.format(
                existing_memory=existing_text,
                summ_info=candidate_summary,
            )
            merged = self._clean_text(
                self.llm_client.generate(
                    prompt=merge_prompt,
                    temperature=0.0,
                    max_tokens=180,
                )
            )
            if not merged:
                raise RuntimeError("PAHF integration prompt produced empty merged memory")
            updated = self.update_memory(person_id, similar.id, merged)
            if updated is None:
                raise RuntimeError("PAHF update failed for an existing similar memory")
            return {"updated": True, "action": "updated", "memory_id": updated.id, "text": updated.text}

        created = self.add_memory(person_id, candidate_summary)
        return {"updated": True, "action": "added", "memory_id": created.id, "text": created.text}

    def close(self) -> None:
        with self._lock:
            for bank in self._banks.values():
                bank.close()
            self._banks.clear()


def build_pahf_memory_service(app_config: AppConfig, model_config: ModelConfig) -> PAHFMemoryService:
    threshold = None
    if app_config.PAHF_SIMILARITY_THRESHOLD.strip():
        threshold = float(app_config.PAHF_SIMILARITY_THRESHOLD)

    llm_client = PahfLLMClient(
        model=app_config.PAHF_LLM_MODEL or model_config.model_name,
        base_url=model_config.base_url,
        api_key=model_config.api_key,
    )
    return PAHFMemoryService(
        backend=app_config.PAHF_BACKEND,
        sqlite_db_path=app_config.PAHF_SQLITE_DB_PATH,
        faiss_path=app_config.PAHF_FAISS_PATH,
        top_k=app_config.PAHF_TOP_K,
        similarity_threshold=threshold,
        llm_client=llm_client,
        query_encoder=app_config.PAHF_QUERY_ENCODER,
        context_encoder=app_config.PAHF_CONTEXT_ENCODER,
        device=app_config.PAHF_EMBED_DEVICE or None,
        embedding_mode=app_config.PAHF_EMBEDDING_MODE,
        enable_pre_clarification=app_config.PAHF_ENABLE_PRE_CLARIFICATION,
        enable_post_correction=app_config.PAHF_ENABLE_POST_CORRECTION,
    )
