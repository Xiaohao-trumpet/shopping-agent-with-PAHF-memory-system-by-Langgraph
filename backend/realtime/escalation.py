"""Escalation router: decide when the AI should hand off to a human agent.

Deterministic and explainable — every decision carries the signal that caused
it, so the agent console and trace can show *why* a chat was escalated. Signals
(highest priority wins):

  urgent(4): complaint / legal / media threats
  high(3)  : explicit "talk to a human" · repeated frustration · account/payment
             security · large refunds
  medium(2): tool execution failed · a record lookup returned nothing

If no signal fires, the AI keeps auto-answering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


PRIORITY_LABEL = {1: "low", 2: "medium", 3: "high", 4: "urgent"}

HIGH_VALUE_REFUND = 1000.0  # CNY threshold for refunds needing human review

_EXPLICIT = ["转人工", "人工客服", "人工服务", "要人工", "真人", "找客服", "speak to a human",
             "talk to a human", "talk to an agent", "real person", "human agent"]
_COMPLAINT = ["投诉", "差评", "曝光", "315", "工商", "消协", "消费者协会", "起诉", "律师",
              "媒体", "维权", "举报", "complaint", "sue", "lawyer", "lawsuit"]
_FRUSTRATION = ["没用", "不对", "听不懂", "没听懂", "还是不行", "没解决", "答非所问", "你没懂",
                "牛头不对马嘴", "重复", "说了几遍", "到底行不行", "useless", "not helpful",
                "doesn't help", "you don't understand"]
_NEGATIVE = ["垃圾", "骗子", "欺骗", "态度差", "态度", "坑人", "无语", "气死", "愤怒", "差劲",
             "退钱", "scam", "terrible", "worst", "angry"]
_SENSITIVE = ["账号被盗", "被盗", "盗刷", "支付失败", "扣款", "乱扣", "多扣", "发票", "账户安全",
              "密码", "实名", "身份证", "银行卡", "unauthorized", "fraud", "charged twice"]

_LOOKUP_INTENTS = {"order_lookup", "order_tracking", "after_sales_return"}


@dataclass
class EscalationDecision:
    handoff: bool = False
    reason: str = "none"
    priority: int = 2
    message: str = ""
    signals: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "handoff": self.handoff,
            "reason": self.reason,
            "priority": self.priority,
            "priority_label": PRIORITY_LABEL.get(self.priority, "medium"),
            "message": self.message,
            "signals": self.signals,
        }


_HOLDING = {
    "user_requested_human": "好的，正在为您转接人工客服，请稍候～",
    "complaint_or_legal": "非常抱歉给您带来不好的体验，已为您优先转接人工客服处理。",
    "user_frustrated": "抱歉没能帮您解决，正在为您转接人工客服跟进。",
    "sensitive_account": "涉及账户/资金安全，已为您转接人工客服核实处理，请稍候。",
    "high_value_return": "您的退款金额较大，已为您转接人工客服审核，请稍候。",
    "tool_failure": "系统查询遇到一点问题，正在为您转接人工客服协助。",
    "no_answer_found": "这个问题我需要请人工同事帮您核实，正在为您转接。",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower())


def _hit(text: str, words: List[str]) -> Optional[str]:
    for w in words:
        if w.lower() in text:
            return w
    return None


def _bigrams(text: str) -> set:
    t = re.sub(r"\s+", "", text)
    if len(t) < 2:
        return {t} if t else set()
    return {t[i : i + 2] for i in range(len(t) - 1)}


def _similar(a: str, b: str) -> float:
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 1.0 if a == b else 0.0
    return len(ba & bb) / len(ba | bb)


def _empty_result(r: Dict[str, Any]) -> bool:
    out = r.get("output", {}) or {}
    if "found" in out:
        return not out.get("found")
    for key in ("hits", "orders", "variants", "recommendations", "coupons"):
        if key in out:
            return len(out.get(key) or []) == 0
    return False


def evaluate_escalation(
    user_message: str,
    recent_customer_messages: Optional[List[str]] = None,
    intent: Optional[str] = None,
    tool_results: Optional[List[Dict[str, Any]]] = None,
    tool_errors: Optional[List[str]] = None,
) -> EscalationDecision:
    """Evaluate handoff. Safe to call message-only (pre-generation) or with the
    full turn trace (post-generation)."""
    text = _norm(user_message)
    recent = recent_customer_messages or []
    tool_results = tool_results or []
    tool_errors = tool_errors or []
    signals: List[str] = []

    # Track the strongest handoff candidate.
    best: Optional[EscalationDecision] = None

    def consider(reason: str, priority: int) -> None:
        nonlocal best
        signals.append(reason)
        if best is None or priority > best.priority:
            best = EscalationDecision(
                handoff=True, reason=reason, priority=priority,
                message=_HOLDING.get(reason, "正在为您转接人工客服。"),
            )

    # --- explicit request -------------------------------------------------
    if _hit(text, _EXPLICIT):
        consider("user_requested_human", 3)

    # --- complaint / legal ------------------------------------------------
    if _hit(text, _COMPLAINT):
        consider("complaint_or_legal", 4)

    # --- account / payment security --------------------------------------
    if _hit(text, _SENSITIVE):
        consider("sensitive_account", 3)

    # --- frustration / repetition ----------------------------------------
    frustrated = _hit(text, _FRUSTRATION) is not None
    repeated = any(_similar(text, _norm(m)) >= 0.7 for m in recent)
    strong_negative = _hit(text, _NEGATIVE) is not None
    if frustrated or repeated or (strong_negative and recent):
        consider("user_frustrated", 3)

    # --- large refund needs review ---------------------------------------
    for r in tool_results:
        if r.get("tool") == "initiate_return":
            amount = (r.get("output") or {}).get("refund_amount") or 0
            if amount and float(amount) >= HIGH_VALUE_REFUND:
                consider("high_value_return", 3)

    # --- tool execution failure ------------------------------------------
    if tool_errors:
        consider("tool_failure", 2)

    # --- record lookup returned nothing ----------------------------------
    if intent in _LOOKUP_INTENTS:
        for r in tool_results:
            if _empty_result(r):
                consider("no_answer_found", 2)
                break

    if best is None:
        return EscalationDecision(handoff=False, reason="none", priority=2, signals=signals)
    best.signals = signals
    return best
