"""Shared runtime holder for realtime singletons.

Populated during the FastAPI lifespan (in ``backend/main.py``) and read by the
realtime API router. Kept dependency-free to avoid import cycles.
"""

from __future__ import annotations

from typing import Any, Optional


class _Runtime:
    chat_service: Optional[Any] = None
    event_bus: Optional[Any] = None
    catalog_store: Optional[Any] = None
    conversation_store: Optional[Any] = None
    feedback_store: Optional[Any] = None
    review_store: Optional[Any] = None
    analytics_service: Optional[Any] = None
    admin_store: Optional[Any] = None


RT = _Runtime()
