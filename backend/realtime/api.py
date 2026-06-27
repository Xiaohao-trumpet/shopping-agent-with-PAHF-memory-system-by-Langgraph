"""REST + WebSocket routes for the storefront and the agent console.

Mounted onto the main FastAPI app. Reads singletons from ``runtime.RT`` which
is populated during the app lifespan.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .runtime import RT
from .feedback_store import SUGGESTED_TAGS

router = APIRouter()


# ------------------------------------------------------------------ schemas
class ShopChatRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)


class ClaimRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    agent_name: str = ""


class AgentMessageRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)


class AgentOpRequest(BaseModel):
    agent_id: str = ""
    csat: Optional[int] = Field(default=None, ge=1, le=5)


class EndRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)


class MessageFeedbackRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1)
    message_id: int
    customer_id: str = Field(..., min_length=1)
    value: str = Field(..., pattern="^(up|down)$")


class RatingRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1)
    customer_id: str = Field(..., min_length=1)
    stars: int = Field(..., ge=1, le=5)
    tags: list[str] = Field(default_factory=list)
    comment: str = ""


# ------------------------------------------------------------ shop browsing
@router.get("/api/v1/shop/categories")
async def shop_categories():
    return {"categories": RT.catalog_store.list_categories()}


@router.get("/api/v1/shop/products")
async def shop_products(
    query: str = "", category: str = "", max_price: Optional[float] = None, limit: int = 24
):
    hits = RT.catalog_store.search_products(
        query=query,
        category=category or None,
        max_price=max_price,
        top_k=limit,
    )
    return {"products": hits}


@router.get("/api/v1/shop/products/{product_id}")
async def shop_product_detail(product_id: str):
    product = RT.catalog_store.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    return product


# ----------------------------------------------------- customer chat (REST)
@router.post("/api/v1/shop/chat")
async def shop_chat(req: ShopChatRequest):
    """REST fallback for sending a customer message (WS is the realtime path)."""
    return await RT.chat_service.handle_customer_message(req.customer_id, req.message)


@router.get("/api/v1/shop/conversation/{customer_id}")
async def shop_conversation(customer_id: str):
    conv = RT.conversation_store.get_or_create_active(customer_id)
    return {
        "conversation": conv,
        "messages": RT.conversation_store.list_messages(conv["conversation_id"]),
    }


@router.post("/api/v1/shop/end")
async def shop_end(req: EndRequest):
    """Customer-initiated end of the consultation -> resolve + emit status so the
    storefront can pop the CSAT rating dialog."""
    conv = RT.conversation_store.get_or_create_active(req.customer_id)
    return await RT.chat_service.resolve(conv["conversation_id"])


# ---------------------------------------------------------------- feedback
@router.get("/api/v1/feedback/tags")
async def feedback_tags():
    return {"tags": SUGGESTED_TAGS}


@router.post("/api/v1/feedback/message")
async def feedback_message(req: MessageFeedbackRequest):
    return RT.feedback_store.add_message_feedback(
        conversation_id=req.conversation_id,
        message_id=req.message_id,
        customer_id=req.customer_id,
        value=req.value,
    )


@router.post("/api/v1/feedback/rating")
async def feedback_rating(req: RatingRequest):
    result = RT.feedback_store.add_rating(
        conversation_id=req.conversation_id,
        customer_id=req.customer_id,
        stars=req.stars,
        tags=req.tags,
        comment=req.comment,
    )
    # Mirror the overall score onto the conversation record.
    RT.conversation_store.set_csat(req.conversation_id, req.stars)
    return result


@router.get("/api/v1/feedback/summary")
async def feedback_summary():
    return RT.feedback_store.summary()


@router.get("/api/v1/feedback/ratings")
async def feedback_ratings(limit: int = 200):
    return {"ratings": RT.feedback_store.list_ratings(limit=limit)}


# ------------------------------------------------------------ agent console
@router.get("/api/v1/agent/conversations")
async def agent_conversations(status: str = "all", limit: int = 50):
    return {"conversations": RT.conversation_store.list_conversations(status=status, limit=limit)}


@router.get("/api/v1/agent/conversations/{conversation_id}")
async def agent_conversation_detail(conversation_id: str):
    ctx = RT.chat_service.get_context(conversation_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ctx


@router.post("/api/v1/agent/conversations/{conversation_id}/claim")
async def agent_claim(conversation_id: str, req: ClaimRequest):
    return await RT.chat_service.claim(conversation_id, req.agent_id, req.agent_name)


@router.post("/api/v1/agent/conversations/{conversation_id}/message")
async def agent_message(conversation_id: str, req: AgentMessageRequest):
    return await RT.chat_service.agent_send(conversation_id, req.agent_id, req.content)


@router.post("/api/v1/agent/conversations/{conversation_id}/release")
async def agent_release(conversation_id: str, req: AgentOpRequest):
    return await RT.chat_service.release(conversation_id, req.agent_id)


@router.post("/api/v1/agent/conversations/{conversation_id}/resolve")
async def agent_resolve(conversation_id: str, req: AgentOpRequest):
    return await RT.chat_service.resolve(conversation_id, req.agent_id, csat=req.csat)


@router.get("/api/v1/agent/conversations/{conversation_id}/suggest")
async def agent_suggest(conversation_id: str):
    return {"suggestion": await RT.chat_service.suggest_reply(conversation_id)}


@router.get("/api/v1/agent/stats")
async def agent_stats():
    return {
        "counts": RT.conversation_store.counts_by_status(),
        "online_agents": RT.chat_service.online_agent_count(),
        "agents": RT.chat_service.list_agents(),
    }


# --------------------------------------------------------------- websockets
async def _pump(ws: WebSocket, q):
    """Forward events from a pre-registered subscriber queue to the websocket."""
    while True:
        await ws.send_json(await q.get())


@router.websocket("/ws/customer/{customer_id}")
async def ws_customer(ws: WebSocket, customer_id: str):
    await ws.accept()
    svc = RT.chat_service
    conv = RT.conversation_store.get_or_create_active(customer_id)
    cid = conv["conversation_id"]
    topic = f"conv:{cid}"
    # Register the subscriber synchronously BEFORE sending history / awaiting,
    # so events published while we process the first message are not missed.
    q = RT.event_bus.register(topic)
    await ws.send_json({
        "type": "history",
        "conversation": conv,
        "messages": RT.conversation_store.list_messages(cid),
    })
    pump_task = asyncio.create_task(_pump(ws, q))
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "message":
                content = (data.get("content") or "").strip()
                if content:
                    await svc.handle_customer_message(customer_id, content)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        pump_task.cancel()
        RT.event_bus.unregister(topic, q)


@router.websocket("/ws/agent/{agent_id}")
async def ws_agent(ws: WebSocket, agent_id: str):
    await ws.accept()
    svc = RT.chat_service
    q = RT.event_bus.register("agents")
    svc.register_agent(agent_id)
    svc.set_online(agent_id, True)
    await svc._emit_agents(svc._queue_snapshot())
    pump_task = asyncio.create_task(_pump(ws, q))
    try:
        while True:
            # Inbound is only used to keep the socket alive / heartbeat.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        pump_task.cancel()
        RT.event_bus.unregister("agents", q)
        svc.set_online(agent_id, False)
        await svc._emit_agents(svc._queue_snapshot())


@router.websocket("/ws/conversation/{conversation_id}")
async def ws_conversation(ws: WebSocket, conversation_id: str):
    """Read-only live feed of one conversation (used by the agent console while
    a chat is open)."""
    await ws.accept()
    topic = f"conv:{conversation_id}"
    q = RT.event_bus.register(topic)
    pump_task = asyncio.create_task(_pump(ws, q))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        pump_task.cancel()
        RT.event_bus.unregister(topic, q)
