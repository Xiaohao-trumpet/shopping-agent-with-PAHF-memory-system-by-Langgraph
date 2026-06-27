"""Realtime + human-in-the-loop subsystem (Phase B & C).

Adds conversation persistence, an in-process event bus, an escalation router
and a HITL service so the shopping agent can hand off to human agents and push
messages over WebSocket.
"""

from .conversation_store import ConversationStore
from .events import EventBus
from .escalation import evaluate_escalation, EscalationDecision
from .service import ChatService
from .feedback_store import FeedbackStore, SUGGESTED_TAGS

__all__ = [
    "ConversationStore",
    "EventBus",
    "evaluate_escalation",
    "EscalationDecision",
    "ChatService",
    "FeedbackStore",
    "SUGGESTED_TAGS",
]
