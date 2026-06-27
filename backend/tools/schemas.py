"""Typed schemas for tool planning and execution."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """A single planned tool call."""

    tool: str = Field(..., min_length=1)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None


class ToolResult(BaseModel):
    """Result of one tool call."""

    tool: str
    success: bool
    input: Dict[str, Any] = Field(default_factory=dict)
    output: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    latency_ms: float = 0.0


class PlannerOutput(BaseModel):
    """Planner decision for the current turn."""

    intent: str = "general_chat"
    needs_tools: bool = False
    plan: List[ToolCall] = Field(default_factory=list)


class KBSearchInput(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)


class KBSearchOutput(BaseModel):
    query: str
    hits: List[Dict[str, Any]] = Field(default_factory=list)


class CreateTicketInput(BaseModel):
    user_id: str = Field(..., min_length=1)
    subject: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1, max_length=2000)
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    tags: List[str] = Field(default_factory=list)


class CreateTicketOutput(BaseModel):
    ticket_id: str
    status: str
    user_id: str
    subject: str
    priority: str
    created_at: float


class GetTicketInput(BaseModel):
    ticket_id: str = Field(..., min_length=1)


class GetTicketOutput(BaseModel):
    found: bool
    ticket: Optional[Dict[str, Any]] = None


class ListTicketsInput(BaseModel):
    user_id: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class ListTicketsOutput(BaseModel):
    user_id: str
    tickets: List[Dict[str, Any]] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# E-commerce (virtual store) tool schemas
# --------------------------------------------------------------------------- #


class ProductSearchInput(BaseModel):
    query: str = Field(..., min_length=1)
    category: Optional[str] = None
    max_price: Optional[float] = Field(default=None, ge=0)
    top_k: int = Field(default=5, ge=1, le=20)


class ProductSearchOutput(BaseModel):
    query: str
    hits: List[Dict[str, Any]] = Field(default_factory=list)


class GetProductInput(BaseModel):
    product_id: str = Field(..., min_length=1)


class GetProductOutput(BaseModel):
    found: bool
    product: Optional[Dict[str, Any]] = None


class CheckInventoryInput(BaseModel):
    product_id: Optional[str] = None
    sku_code: Optional[str] = None


class CheckInventoryOutput(BaseModel):
    variants: List[Dict[str, Any]] = Field(default_factory=list)


class GetOrderInput(BaseModel):
    order_id: str = Field(..., min_length=1)


class GetOrderOutput(BaseModel):
    found: bool
    order: Optional[Dict[str, Any]] = None


class ListOrdersInput(BaseModel):
    customer_id: str = Field(..., min_length=1)
    limit: int = Field(default=5, ge=1, le=50)


class ListOrdersOutput(BaseModel):
    customer_id: str
    orders: List[Dict[str, Any]] = Field(default_factory=list)


class TrackShipmentInput(BaseModel):
    order_id: Optional[str] = None
    tracking_no: Optional[str] = None


class TrackShipmentOutput(BaseModel):
    found: bool
    shipment: Optional[Dict[str, Any]] = None


class RecommendInput(BaseModel):
    customer_id: str = Field(..., min_length=1)
    query: str = ""
    top_k: int = Field(default=4, ge=1, le=12)


class RecommendOutput(BaseModel):
    recommendations: List[Dict[str, Any]] = Field(default_factory=list)


class ListCouponsInput(BaseModel):
    min_spend: Optional[float] = Field(default=None, ge=0)


class ListCouponsOutput(BaseModel):
    coupons: List[Dict[str, Any]] = Field(default_factory=list)


class ApplyCouponInput(BaseModel):
    code: str = Field(..., min_length=1)
    order_total: float = Field(..., ge=0)


class ApplyCouponOutput(BaseModel):
    valid: bool
    reason: str
    discount: float = 0.0
    final_total: Optional[float] = None
    code: Optional[str] = None
    description: Optional[str] = None
    min_spend: Optional[float] = None


class InitiateReturnInput(BaseModel):
    order_id: str = Field(..., min_length=1)
    customer_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1, max_length=500)
    sku_code: Optional[str] = None


class InitiateReturnOutput(BaseModel):
    created: bool
    reason: Optional[str] = None
    return_id: Optional[str] = None
    order_id: Optional[str] = None
    status: Optional[str] = None
    refund_amount: Optional[float] = None

