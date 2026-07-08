"""REST + WebSocket routes for the storefront and the agent console.

Mounted onto the main FastAPI app. Reads singletons from ``runtime.RT`` which
is populated during the app lifespan.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .runtime import RT
from .feedback_store import SUGGESTED_TAGS
from ..analytics import REVIEW_TAGS
from ..auth_deps import admin_session_for_token, require_admin

router = APIRouter()


# ------------------------------------------------------------------ schemas
class ShopChatRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)


class ClaimRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    agent_name: str = ""
    conversation_snapshot: Optional[dict[str, Any]] = None
    messages_snapshot: list[dict[str, Any]] = Field(default_factory=list)


class AgentMessageRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    conversation_snapshot: Optional[dict[str, Any]] = None
    messages_snapshot: list[dict[str, Any]] = Field(default_factory=list)


class AgentOpRequest(BaseModel):
    agent_id: str = ""
    csat: Optional[int] = Field(default=None, ge=1, le=5)


class EndRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)


class ReturnRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)
    order_id: str = Field(..., min_length=1)
    sku_code: Optional[str] = None
    reason: str = Field(default="用户在商城端申请售后", min_length=1)


class CartAddRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)
    product_id: str = Field(..., min_length=1)
    sku_code: str = ""
    qty: int = Field(default=1, ge=1, le=99)


class CartUpdateRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)
    sku_code: str = Field(..., min_length=1)
    qty: int = Field(default=1, ge=0, le=99)


class CartCheckoutRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)
    shipping_address: str = ""
    shipping_method: str = "待选择"


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


class ProductReviewRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)
    rating: int = Field(..., ge=1, le=5)
    title: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    author_name: str = ""


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


# ---------------------------------------------------------- product reviews
@router.get("/api/v1/shop/review-tags")
async def shop_review_tags():
    return {"tags": REVIEW_TAGS}


@router.get("/api/v1/shop/products/{product_id}/reviews")
async def shop_product_reviews(product_id: str, limit: int = 20, sentiment: str = ""):
    stats = RT.review_store.product_review_stats(product_id)
    reviews = RT.review_store.list_reviews(
        product_id, limit=limit, sentiment=sentiment or None
    )
    return {"stats": stats, "reviews": reviews}


@router.post("/api/v1/shop/products/{product_id}/reviews")
async def shop_submit_review(product_id: str, req: ProductReviewRequest):
    try:
        return RT.analytics_service.submit_review(
            product_id=product_id,
            customer_id=req.customer_id,
            rating=req.rating,
            title=req.title,
            content=req.content,
            tags=req.tags,
            author_name=req.author_name,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="product not found")


@router.get("/api/v1/shop/orders")
async def shop_orders(customer_id: str, limit: int = 10):
    rows = RT.catalog_store.list_orders(customer_id=customer_id, limit=max(1, min(limit, 50)))
    orders = []
    for row in rows:
        detail = RT.catalog_store.get_order(row["order_id"])
        orders.append(detail or row)
    return {"orders": orders}


@router.get("/api/v1/shop/orders/{order_id}")
async def shop_order_detail(order_id: str, customer_id: Optional[str] = None):
    order = RT.catalog_store.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if customer_id and order["customer_id"] != customer_id:
        raise HTTPException(status_code=403, detail="order does not belong to customer")
    return order


@router.get("/api/v1/shop/orders/{order_id}/shipment")
async def shop_order_shipment(order_id: str, customer_id: Optional[str] = None):
    order = RT.catalog_store.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if customer_id and order["customer_id"] != customer_id:
        raise HTTPException(status_code=403, detail="order does not belong to customer")
    shipment = order.get("shipment")
    if shipment is None:
        raise HTTPException(status_code=404, detail="shipment not found")
    return shipment


@router.post("/api/v1/shop/returns")
async def shop_create_return(req: ReturnRequest):
    result = RT.catalog_store.create_return(
        order_id=req.order_id,
        customer_id=req.customer_id,
        sku_code=req.sku_code,
        reason=req.reason,
    )
    if not result.get("created"):
        raise HTTPException(status_code=400, detail=result)
    return result


# --------------------------------------------------------------- cart/order
def _cart_error(exc: ValueError) -> HTTPException:
    detail = str(exc)
    if detail == "customer_not_found":
        return HTTPException(status_code=404, detail="customer not found")
    if detail in {"sku_not_found", "product_not_found"}:
        return HTTPException(status_code=404, detail="sku or product not found")
    if detail == "sku_out_of_stock" or detail.startswith("insufficient_stock"):
        return HTTPException(status_code=409, detail=detail)
    if detail == "cart_empty":
        return HTTPException(status_code=400, detail="cart is empty")
    return HTTPException(status_code=400, detail=detail)


@router.get("/api/v1/shop/cart")
async def shop_cart(customer_id: str):
    return RT.catalog_store.get_cart(customer_id)


@router.post("/api/v1/shop/cart/items")
async def shop_add_cart_item(req: CartAddRequest):
    try:
        return RT.catalog_store.add_cart_item(
            customer_id=req.customer_id,
            product_id=req.product_id,
            sku_code=req.sku_code,
            qty=req.qty,
        )
    except ValueError as exc:
        raise _cart_error(exc)


@router.put("/api/v1/shop/cart/items")
async def shop_update_cart_item(req: CartUpdateRequest):
    return RT.catalog_store.update_cart_item(req.customer_id, req.sku_code, req.qty)


@router.delete("/api/v1/shop/cart")
async def shop_clear_cart(customer_id: str):
    return RT.catalog_store.clear_cart(customer_id)


@router.post("/api/v1/shop/cart/checkout")
async def shop_checkout_cart(req: CartCheckoutRequest):
    try:
        return RT.catalog_store.checkout_cart(
            customer_id=req.customer_id,
            shipping_address=req.shipping_address,
            shipping_method=req.shipping_method,
        )
    except ValueError as exc:
        raise _cart_error(exc)


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
# All routes below expose customer chat content and full PAHF memory dumps,
# so they require the same admin session as the backoffice (/api/v1/admin/*).
@router.get("/api/v1/agent/conversations")
async def agent_conversations(status: str = "all", limit: int = 50, _admin: dict = Depends(require_admin)):
    return {"conversations": RT.conversation_store.list_conversations(status=status, limit=limit)}


@router.get("/api/v1/agent/conversations/{conversation_id}")
async def agent_conversation_detail(conversation_id: str, _admin: dict = Depends(require_admin)):
    ctx = RT.chat_service.get_context(conversation_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ctx


@router.post("/api/v1/agent/conversations/{conversation_id}/claim")
async def agent_claim(conversation_id: str, req: ClaimRequest, _admin: dict = Depends(require_admin)):
    try:
        return await RT.chat_service.claim(
            conversation_id,
            req.agent_id,
            req.agent_name,
            conversation_snapshot=req.conversation_snapshot,
            messages_snapshot=req.messages_snapshot,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "conversation_not_found":
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=409, detail=detail)


@router.post("/api/v1/agent/conversations/{conversation_id}/message")
async def agent_message(conversation_id: str, req: AgentMessageRequest, _admin: dict = Depends(require_admin)):
    try:
        return await RT.chat_service.agent_send(
            conversation_id,
            req.agent_id,
            req.content,
            conversation_snapshot=req.conversation_snapshot,
            messages_snapshot=req.messages_snapshot,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "conversation_not_found":
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=409, detail=detail)


@router.post("/api/v1/agent/conversations/{conversation_id}/release")
async def agent_release(conversation_id: str, req: AgentOpRequest, _admin: dict = Depends(require_admin)):
    return await RT.chat_service.release(conversation_id, req.agent_id)


@router.post("/api/v1/agent/conversations/{conversation_id}/resolve")
async def agent_resolve(conversation_id: str, req: AgentOpRequest, _admin: dict = Depends(require_admin)):
    return await RT.chat_service.resolve(conversation_id, req.agent_id, csat=req.csat)


@router.get("/api/v1/agent/conversations/{conversation_id}/suggest")
async def agent_suggest(conversation_id: str, _admin: dict = Depends(require_admin)):
    return {"suggestion": await RT.chat_service.suggest_reply(conversation_id)}


@router.get("/api/v1/agent/stats")
async def agent_stats(_admin: dict = Depends(require_admin)):
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
async def ws_agent(ws: WebSocket, agent_id: str, token: Optional[str] = None):
    if admin_session_for_token(token) is None:
        await ws.close(code=4401)
        return
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
async def ws_conversation(ws: WebSocket, conversation_id: str, token: Optional[str] = None):
    """Read-only live feed of one conversation (used by the agent console while
    a chat is open)."""
    if admin_session_for_token(token) is None:
        await ws.close(code=4401)
        return
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
