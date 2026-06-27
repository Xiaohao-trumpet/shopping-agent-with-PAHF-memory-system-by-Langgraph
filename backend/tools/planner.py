"""Rule-based intent router and tool planner."""

from __future__ import annotations

import re
from typing import List

from .schemas import PlannerOutput, ToolCall


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _priority_from_text(text: str) -> str:
    if any(word in text for word in ["urgent", "asap", "critical", "immediately", "紧急", "马上"]):
        return "urgent"
    if any(word in text for word in ["cannot", "can't", "not working", "down", "失败", "故障"]):
        return "high"
    if any(word in text for word in ["minor", "later"]):
        return "low"
    return "medium"


class ToolPlanner:
    """Deterministic planner for tool routing."""

    def __init__(self, tools_enabled: bool = True, max_calls_per_turn: int = 3):
        self.tools_enabled = tools_enabled
        self.max_calls_per_turn = max(1, max_calls_per_turn)

    def plan(self, user_id: str, user_message: str) -> PlannerOutput:
        text = _normalize(user_message)
        if not self.tools_enabled:
            return PlannerOutput(intent="general_chat", needs_tools=False, plan=[])

        # E-commerce intents take priority. When one matches we return early so
        # the legacy KB/ticket routing below stays byte-for-byte unchanged for
        # everything else (policy questions, support tickets, general chat).
        commerce = self._plan_commerce(user_id=user_id, text=text, user_message=user_message)
        if commerce is not None:
            plan = commerce.plan[: self.max_calls_per_turn]
            return PlannerOutput(intent=commerce.intent, needs_tools=bool(plan), plan=plan)

        plan: List[ToolCall] = []
        intent = "general_chat"

        if any(k in text for k in ["refund", "policy", "return policy", "退款", "退货"]):
            intent = "kb_question"
            plan.append(
                ToolCall(
                    tool="kb_search",
                    arguments={"query": user_message, "top_k": 3},
                    reason="User asks for policy/FAQ information.",
                )
            )

        ticket_id_match = re.search(r"\b(t[0-9a-f]{6,12})\b", text, flags=re.IGNORECASE)
        if any(k in text for k in ["ticket status", "check ticket", "ticket", "工单状态", "查询工单"]) and ticket_id_match:
            intent = "ticket_lookup"
            plan.append(
                ToolCall(
                    tool="get_ticket",
                    arguments={"ticket_id": ticket_id_match.group(1).upper()},
                    reason="User asks for a specific ticket status.",
                )
            )
        elif any(k in text for k in ["my tickets", "list tickets", "show tickets", "我的工单"]):
            intent = "ticket_list"
            plan.append(
                ToolCall(
                    tool="list_tickets",
                    arguments={"user_id": user_id, "limit": 5},
                    reason="User asks for their recent tickets.",
                )
            )
        elif any(
            k in text
            for k in [
                "open a ticket",
                "create ticket",
                "support ticket",
                "raise a ticket",
                "internet not working",
                "network down",
                "创建工单",
                "报障",
                "网络故障",
            ]
        ):
            intent = "ticket_create"
            subject = user_message.strip().split(".")[0][:100] or "Support request"
            plan.append(
                ToolCall(
                    tool="create_ticket",
                    arguments={
                        "user_id": user_id,
                        "subject": subject,
                        "description": user_message.strip(),
                        "priority": _priority_from_text(text),
                        "tags": ["auto", "chat"],
                    },
                    reason="User requests support and likely needs a new ticket.",
                )
            )

        if len(plan) > self.max_calls_per_turn:
            plan = plan[: self.max_calls_per_turn]

        return PlannerOutput(
            intent=intent,
            needs_tools=len(plan) > 0,
            plan=plan,
        )

    # Keyword tables for e-commerce routing (text is already lowercased).
    _TRACK_KW = ["track", "tracking", "物流", "快递", "到哪", "什么时候到", "运单", "包裹", "发货了吗"]
    _RETURN_KW = ["退货", "退掉", "申请退", "return", "refund", "退款"]
    _ORDER_LIST_KW = ["我的订单", "所有订单", "订单列表", "历史订单", "my orders", "list orders"]
    _ORDER_KW = ["订单", "order", "查单", "我的单"]
    _COUPON_KW = ["优惠券", "优惠", "折扣", "促销", "满减", "coupon", "discount", "voucher"]
    _RECOMMEND_KW = ["推荐", "recommend", "帮我选", "选购建议", "有什么好"]
    _INVENTORY_KW = ["库存", "有货", "有没有货", "还有吗", "现货", "in stock", "stock"]
    _DETAIL_KW = ["详情", "规格", "参数", "配置", "介绍一下", "detail", "spec"]
    _SEARCH_KW = ["买", "购买", "有没有", "多少钱", "价格", "想要", "卖", "找", "有卖", "求推荐",
                  "search", "buy", "price", "looking for"]

    def _plan_commerce(self, user_id: str, text: str, user_message: str):
        """Rule-based routing for shopping intents. Returns PlannerOutput or None."""
        order_id_m = re.search(r"\b(so\d{6,})\b", text, flags=re.IGNORECASE)
        tracking_m = re.search(r"\b([a-z]{2}\d{8,})\b", text, flags=re.IGNORECASE)
        sku_m = re.search(r"\b(p\d{3,}-[a-z0-9-]+)\b", text, flags=re.IGNORECASE)
        product_m = re.search(r"\b(p\d{3,})\b", text, flags=re.IGNORECASE)

        order_id = order_id_m.group(1).upper() if order_id_m else None
        tracking_no = tracking_m.group(1).upper() if tracking_m else None
        sku_code = sku_m.group(1).upper() if sku_m else None
        product_id = product_m.group(1).upper() if product_m else None
        # An order id (SO + digits) also matches the generic tracking pattern;
        # don't treat the order id itself as a tracking number.
        if tracking_no and tracking_no == order_id:
            tracking_no = None

        def hit(words: List[str]) -> bool:
            return any(w in text for w in words)

        call = None
        intent = "general_chat"

        if hit(self._TRACK_KW) and (order_id or tracking_no):
            intent = "order_tracking"
            args = {"order_id": order_id} if order_id else {"tracking_no": tracking_no}
            call = ToolCall(tool="track_shipment", arguments=args, reason="User asks where a shipment is.")
        elif hit(self._RETURN_KW) and order_id:
            intent = "after_sales_return"
            call = ToolCall(
                tool="initiate_return",
                arguments={
                    "order_id": order_id,
                    "customer_id": user_id,
                    "reason": user_message.strip()[:500] or "customer requested return",
                    **({"sku_code": sku_code} if sku_code else {}),
                },
                reason="User requests a return/refund for a specific order.",
            )
        elif hit(self._ORDER_LIST_KW):
            intent = "order_list"
            call = ToolCall(
                tool="list_orders",
                arguments={"customer_id": user_id, "limit": 5},
                reason="User asks for their recent orders.",
            )
        elif hit(self._ORDER_KW) and order_id:
            intent = "order_lookup"
            call = ToolCall(
                tool="get_order",
                arguments={"order_id": order_id},
                reason="User asks about a specific order.",
            )
        elif hit(self._COUPON_KW):
            intent = "coupon_query"
            call = ToolCall(tool="list_coupons", arguments={}, reason="User asks about coupons/discounts.")
        elif hit(self._RECOMMEND_KW):
            intent = "product_recommend"
            call = ToolCall(
                tool="recommend_products",
                arguments={"customer_id": user_id, "query": user_message.strip(), "top_k": 4},
                reason="User asks for product recommendations.",
            )
        elif hit(self._INVENTORY_KW) and (sku_code or product_id):
            intent = "inventory_check"
            args = {"sku_code": sku_code} if sku_code else {"product_id": product_id}
            call = ToolCall(tool="check_inventory", arguments=args, reason="User asks about stock for a product/SKU.")
        elif product_id and hit(self._DETAIL_KW):
            intent = "product_detail"
            call = ToolCall(
                tool="get_product_detail",
                arguments={"product_id": product_id},
                reason="User asks for product details/specs.",
            )
        elif hit(self._SEARCH_KW) or hit(self._INVENTORY_KW):
            intent = "product_search"
            call = ToolCall(
                tool="product_search",
                arguments={"query": user_message.strip(), "top_k": 5},
                reason="User is browsing/searching for products.",
            )

        if call is None:
            return None
        return PlannerOutput(intent=intent, needs_tools=True, plan=[call])

