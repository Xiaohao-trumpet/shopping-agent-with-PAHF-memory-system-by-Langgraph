"""ChatService: the human-in-the-loop engine.

Coordinates the conversation store, event bus, escalation router, the LangGraph
AI pipeline, and human agents. Customer messages flow through here; the service
decides whether the AI answers or the chat is escalated to a human, pushes
realtime events, and exposes agent-console operations (claim / reply / release /
resolve / AI-suggested reply / 360° context).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from .conversation_store import ConversationStore
from .events import EventBus
from .escalation import evaluate_escalation, EscalationDecision


class ChatService:
    def __init__(
        self,
        conversations: ConversationStore,
        event_bus: EventBus,
        chat_graph,
        memory_writeback_graph=None,
        memory_writeback_mode: str = "background",
        model_client=None,
        catalog_store=None,
        pahf_memory_service=None,
        logger=None,
        notify_webhook: str = "",
    ):
        self.conversations = conversations
        self.bus = event_bus
        self.chat_graph = chat_graph
        self.memory_writeback_graph = memory_writeback_graph
        self.memory_writeback_mode = memory_writeback_mode
        self.model_client = model_client
        self.catalog = catalog_store
        self.pahf = pahf_memory_service
        self.logger = logger
        self.notify_webhook = notify_webhook
        # In-memory agent presence: agent_id -> {name, online, active:set(conv_id)}
        self._agents: Dict[str, Dict[str, Any]] = {}

    def _log(self, level: str, msg: str, **extra) -> None:
        if self.logger is not None:
            getattr(self.logger, level, self.logger.info)(msg)

    # ---------------------------------------------------------- agent presence
    def register_agent(self, agent_id: str, name: str = "") -> Dict[str, Any]:
        a = self._agents.setdefault(agent_id, {"name": name or agent_id, "online": False, "active": set()})
        if name:
            a["name"] = name
        return self.agent_public(agent_id)

    def set_online(self, agent_id: str, online: bool) -> None:
        a = self._agents.setdefault(agent_id, {"name": agent_id, "online": False, "active": set()})
        a["online"] = online

    def agent_public(self, agent_id: str) -> Dict[str, Any]:
        a = self._agents.get(agent_id, {"name": agent_id, "online": False, "active": set()})
        return {"agent_id": agent_id, "name": a["name"], "online": a["online"], "active": len(a["active"])}

    def online_agent_count(self) -> int:
        return sum(1 for a in self._agents.values() if a["online"])

    def list_agents(self) -> List[Dict[str, Any]]:
        return [self.agent_public(aid) for aid in self._agents]

    # ------------------------------------------------------------- publishing
    async def _emit_conv(self, conversation_id: str, event: dict) -> None:
        await self.bus.publish(f"conv:{conversation_id}", event)

    async def _emit_agents(self, event: dict) -> None:
        await self.bus.publish("agents", event)

    def _queue_snapshot(self) -> dict:
        return {
            "type": "queue_update",
            "counts": self.conversations.counts_by_status(),
            "online_agents": self.online_agent_count(),
        }

    async def _run_memory_writeback(self, state: dict) -> None:
        """Fire-and-forget PAHF post-action correction. Failures are logged,
        never raised, so a flaky memory write never surfaces to the customer."""
        if self.memory_writeback_graph is None:
            return
        try:
            await asyncio.to_thread(self.memory_writeback_graph.invoke, state)
        except Exception as exc:
            self._log("error", f"memory writeback failed: {exc}")

    # ----------------------------------------------------- customer messaging
    async def handle_customer_message(self, customer_id: str, content: str) -> Dict[str, Any]:
        conv = self.conversations.get_or_create_active(customer_id)
        cid = conv["conversation_id"]

        recent = self.conversations.recent_customer_messages(cid, limit=6)
        cust_msg = self.conversations.add_message(cid, role="customer", content=content, sender=customer_id)
        await self._emit_conv(cid, {"type": "message", "message": cust_msg})
        await self._emit_agents({"type": "customer_message", "conversation_id": cid, "message": cust_msg})

        status = conv["status"]

        # Human is handling: don't auto-reply, but give the agent an AI draft (copilot).
        if status == "human":
            asyncio.create_task(self._suggest_async(cid))
            return {"conversation_id": cid, "status": "human", "response": None}

        # Already queued: keep waiting; remind once.
        if status == "queued":
            note = "您的问题正在排队等待人工客服，请稍候～"
            msg = self.conversations.add_message(cid, role="system", content=note, sender="system")
            await self._emit_conv(cid, {"type": "message", "message": msg})
            return {"conversation_id": cid, "status": "queued", "response": note}

        # status == bot: pre-check explicit/risk signals before spending an LLM call.
        pre = evaluate_escalation(content, recent_customer_messages=recent)
        if pre.handoff:
            return await self._escalate(conv, pre, ai_draft=None)

        # Run the AI pipeline (sync graph) off the event loop.
        state = {
            "user_id": customer_id,
            "user_message": content,
            "response": None,
            "temperature": None,
            "max_tokens": None,
            "session": None,
        }
        try:
            result = await asyncio.to_thread(self.chat_graph.invoke, state)
        except Exception as exc:  # graph/model failure -> escalate
            self._log("error", f"graph invoke failed: {exc}")
            fail = EscalationDecision(
                handoff=True, reason="tool_failure", priority=2,
                message="系统繁忙，正在为您转接人工客服。", signals=["graph_error"],
            )
            return await self._escalate(conv, fail, ai_draft=None)

        # PAHF post-action memory correction (extraction + add/update) doesn't
        # affect what we reply with, so it runs in the background instead of
        # adding 2-3 more sequential LLM calls before the customer sees a
        # reply. On serverless the instance freezes once the response is sent,
        # so "sync" mode completes it before returning instead.
        if self.memory_writeback_mode == "sync":
            await self._run_memory_writeback(result)
        else:
            asyncio.create_task(self._run_memory_writeback(result))

        response_text = result.get("response", "") or ""
        trace = {
            "intent": result.get("intent"),
            "tool_plan": result.get("tool_plan", []),
            "tool_results": result.get("tool_results", []),
            "tool_errors": result.get("tool_errors", []),
            "retrieved_memories": result.get("retrieved_memories", []),
        }

        # Post-check using the turn trace (tool failures / empty lookups / refunds).
        post = evaluate_escalation(
            content,
            recent_customer_messages=recent,
            intent=trace["intent"],
            tool_results=trace["tool_results"],
            tool_errors=trace["tool_errors"],
        )
        if post.handoff:
            return await self._escalate(conv, post, ai_draft=response_text)

        ai_msg = self.conversations.add_message(
            cid, role="ai", content=response_text, sender="ai", meta={"trace": trace}
        )
        await self._emit_conv(cid, {"type": "message", "message": ai_msg})
        await self._emit_agents({"type": "ai_message", "conversation_id": cid, "message": ai_msg})
        return {"conversation_id": cid, "status": "bot", "response": response_text, "trace": trace}

    async def _escalate(self, conv: dict, decision: EscalationDecision, ai_draft: Optional[str]) -> Dict[str, Any]:
        cid = conv["conversation_id"]
        self.conversations.set_status(
            cid, "queued", escalation_reason=decision.reason, priority=decision.priority
        )
        sys_note = f"[升级] 原因：{decision.reason} · 优先级：{decision.priority}"
        sys_msg = self.conversations.add_message(
            cid, role="system", content=sys_note, sender="system",
            meta={"escalation": decision.to_dict(), "ai_draft": ai_draft},
        )
        hold = self.conversations.add_message(
            cid, role="ai", content=decision.message, sender="system",
            meta={"escalation": decision.to_dict()},
        )
        await self._emit_conv(cid, {"type": "message", "message": hold})
        await self._emit_conv(cid, {"type": "status", "status": "queued", "reason": decision.reason})
        await self._emit_agents({
            "type": "escalation",
            "conversation_id": cid,
            "customer_id": conv["customer_id"],
            "reason": decision.reason,
            "priority": decision.priority,
            "system_message": sys_msg,
        })
        await self._emit_agents(self._queue_snapshot())

        if self.online_agent_count() == 0:
            await self._notify_offline(conv, decision)

        self._log("warning", f"escalated {cid} reason={decision.reason} prio={decision.priority}")
        return {
            "conversation_id": cid,
            "status": "queued",
            "response": decision.message,
            "escalation": decision.to_dict(),
        }

    async def _notify_offline(self, conv: dict, decision: EscalationDecision) -> None:
        """Fallback alert when no agent is online. Emits an alert event and, if a
        webhook is configured, posts to it best-effort (IM bot / email gateway)."""
        alert = {
            "type": "alert",
            "conversation_id": conv["conversation_id"],
            "customer_id": conv["customer_id"],
            "reason": decision.reason,
            "priority": decision.priority,
            "ts": time.time(),
        }
        await self._emit_agents(alert)
        self._log("warning", f"[OFFLINE-ALERT] no agent online for {conv['conversation_id']} ({decision.reason})")
        if self.notify_webhook:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=3.0) as client:
                    await client.post(self.notify_webhook, json=alert)
            except Exception as exc:  # best-effort only
                self._log("error", f"offline webhook failed: {exc}")

    # --------------------------------------------------------- agent actions
    def _restore_if_missing(
        self,
        conversation_id: str,
        conversation_snapshot: Optional[Dict[str, Any]] = None,
        messages_snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not conversation_snapshot:
            return None
        if conversation_snapshot.get("conversation_id") != conversation_id:
            return None
        return self.conversations.restore_snapshot(conversation_snapshot, messages_snapshot or [])

    async def claim(
        self,
        conversation_id: str,
        agent_id: str,
        agent_name: str = "",
        conversation_snapshot: Optional[Dict[str, Any]] = None,
        messages_snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        self.register_agent(agent_id, agent_name)
        existing = self.conversations.get_conversation(conversation_id)
        if existing is None:
            existing = self._restore_if_missing(conversation_id, conversation_snapshot, messages_snapshot)
        if existing is None:
            raise ValueError("conversation_not_found")
        if existing["status"] == "human":
            assigned = existing.get("assigned_agent")
            if assigned and assigned != agent_id:
                raise ValueError("conversation_assigned_to_other_agent")
            self._agents[agent_id]["active"].add(conversation_id)
            return existing
        if existing["status"] != "queued":
            raise ValueError(f"conversation_not_claimable:{existing['status']}")
        conv = self.conversations.assign_agent(conversation_id, agent_id)
        self._agents[agent_id]["active"].add(conversation_id)
        name = self._agents[agent_id]["name"]
        sys_msg = self.conversations.add_message(
            conversation_id, role="system", content=f"客服 {name} 已接入", sender="system"
        )
        await self._emit_conv(conversation_id, {"type": "message", "message": sys_msg})
        await self._emit_conv(conversation_id, {
            "type": "status", "status": "human", "agent": name,
            "customer_note": "人工客服已接入，将由专人为您服务～",
        })
        await self._emit_agents({"type": "claimed", "conversation_id": conversation_id, "agent_id": agent_id})
        await self._emit_agents(self._queue_snapshot())
        return conv

    async def agent_send(
        self,
        conversation_id: str,
        agent_id: str,
        content: str,
        conversation_snapshot: Optional[Dict[str, Any]] = None,
        messages_snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        conv = self.conversations.get_conversation(conversation_id)
        if conv is None:
            conv = self._restore_if_missing(conversation_id, conversation_snapshot, messages_snapshot)
        if conv is None:
            raise ValueError("conversation_not_found")
        if conv["status"] != "human":
            raise ValueError(f"conversation_not_human:{conv['status']}")
        if conv.get("assigned_agent") and conv["assigned_agent"] != agent_id:
            raise ValueError("conversation_assigned_to_other_agent")
        name = self._agents.get(agent_id, {}).get("name", agent_id)
        msg = self.conversations.add_message(
            conversation_id, role="agent", content=content, sender=name
        )
        await self._emit_conv(conversation_id, {"type": "message", "message": msg})
        return msg

    async def release(self, conversation_id: str, agent_id: str) -> Dict[str, Any]:
        conv = self.conversations.release_to_bot(conversation_id)
        if agent_id in self._agents:
            self._agents[agent_id]["active"].discard(conversation_id)
        sys_msg = self.conversations.add_message(
            conversation_id, role="system", content="已转回智能助手为您服务", sender="system"
        )
        await self._emit_conv(conversation_id, {"type": "message", "message": sys_msg})
        await self._emit_conv(conversation_id, {"type": "status", "status": "bot"})
        await self._emit_agents({"type": "released", "conversation_id": conversation_id})
        await self._emit_agents(self._queue_snapshot())
        return conv

    async def resolve(self, conversation_id: str, agent_id: str = "", csat: Optional[int] = None) -> Dict[str, Any]:
        conv = self.conversations.resolve(conversation_id, csat=csat)
        if agent_id in self._agents:
            self._agents[agent_id]["active"].discard(conversation_id)
        sys_msg = self.conversations.add_message(
            conversation_id, role="system", content="会话已结束，感谢您的咨询～", sender="system"
        )
        await self._emit_conv(conversation_id, {"type": "message", "message": sys_msg})
        await self._emit_conv(conversation_id, {"type": "status", "status": "resolved"})
        await self._emit_agents({"type": "resolved", "conversation_id": conversation_id})
        await self._emit_agents(self._queue_snapshot())
        return conv

    # ----------------------------------------------------- agent copilot/context
    async def suggest_reply(self, conversation_id: str) -> str:
        if self.model_client is None:
            return ""
        return await asyncio.to_thread(self._suggest_sync, conversation_id)

    async def _suggest_async(self, conversation_id: str) -> None:
        try:
            draft = await self.suggest_reply(conversation_id)
            if draft:
                await self._emit_conv(conversation_id, {"type": "ai_suggestion", "text": draft})
        except Exception as exc:
            self._log("error", f"suggest failed: {exc}")

    def _suggest_sync(self, conversation_id: str) -> str:
        conv = self.conversations.get_conversation(conversation_id)
        if conv is None:
            return ""
        msgs = self.conversations.list_messages(conversation_id, limit=12)
        transcript = "\n".join(
            f"{'用户' if m['role']=='customer' else ('客服' if m['role']=='agent' else '助手')}: {m['content']}"
            for m in msgs if m["role"] in ("customer", "agent", "ai")
        )
        prompt = (
            "你是电商客服助理，请根据以下对话，为人工客服起草一条简洁、专业、友好的中文回复建议"
            "（只输出回复内容本身，不要解释）：\n\n" + transcript + "\n\n回复建议："
        )
        try:
            return self.model_client.chat(
                user_id=conv["customer_id"], message=prompt, use_history=False
            ).strip()
        except Exception as exc:
            self._log("error", f"suggest model call failed: {exc}")
            return ""

    def get_context(self, conversation_id: str) -> Dict[str, Any]:
        """360° context for the agent console: conversation, messages, the
        customer's orders and PAHF memory profile."""
        conv = self.conversations.get_conversation(conversation_id)
        if conv is None:
            return {}
        messages = self.conversations.list_messages(conversation_id)
        customer_id = conv["customer_id"]
        orders: List[Dict[str, Any]] = []
        memories: List[Dict[str, Any]] = []
        if self.catalog is not None:
            try:
                orders = self.catalog.list_orders(customer_id, limit=10)
            except Exception:
                orders = []
        if self.pahf is not None:
            try:
                memories = [
                    {"id": int(m.id), "text": m.text}
                    for m in self.pahf.get_all_memories(person_id=customer_id)
                ]
            except Exception:
                memories = []
        return {
            "conversation": conv,
            "messages": messages,
            "customer": {"customer_id": customer_id, "orders": orders, "memories": memories},
        }
