"""Review-analytics subsystem.

Turns raw product reviews into an analyzable, explainable view of each product's
— and the whole store's — development potential, with an AI layer for generating
reviews and narrating insights (with deterministic offline fallbacks).
"""

from .review_store import ReviewStore, REVIEW_TAGS, DEMO_REVIEWS_VERSION
from .ai_reviewer import AIReviewer
from .service import AnalyticsService
from . import potential

__all__ = [
    "ReviewStore",
    "REVIEW_TAGS",
    "DEMO_REVIEWS_VERSION",
    "AIReviewer",
    "AnalyticsService",
    "potential",
]
