"""AnalyticsService: the review-analytics façade.

Ties together the review store (raw signal), the catalog (product metadata +
demand), the potential engine (deterministic scoring) and the AI reviewer
(narrative + generation). This is what the API layer talks to.

AI narratives are computed lazily and cached, keyed by the review count so the
cache self-invalidates whenever new reviews arrive.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .review_store import ReviewStore
from .ai_reviewer import AIReviewer
from . import potential as potential_engine


class AnalyticsService:
    def __init__(
        self,
        review_store: ReviewStore,
        catalog_store,
        ai_reviewer: AIReviewer,
        feedback_store=None,
        logger=None,
    ):
        self.reviews = review_store
        self.catalog = catalog_store
        self.ai = ai_reviewer
        self.feedback = feedback_store
        self.logger = logger
        self._insight_cache: Dict[str, Dict[str, Any]] = {}
        self._store_cache: Dict[str, Any] = {}

    def _log(self, msg: str) -> None:
        if self.logger is not None:
            self.logger.info(msg)

    # --------------------------------------------------------------- seeding
    def ensure_seeded(self, auto_seed: bool = True) -> None:
        if not auto_seed:
            return
        try:
            if self.reviews.needs_demo_seed():
                products = self.catalog.list_products_for_admin(limit=500)
                n = self.reviews.seed_demo_reviews(products)
                self._log(f"seeded {n} demo product reviews")
                self._invalidate()
        except Exception as exc:  # never block startup on demo seed
            self._log(f"review demo seed skipped: {exc}")

    def _invalidate(self) -> None:
        self._insight_cache.clear()
        self._store_cache.clear()

    # --------------------------------------------------------------- helpers
    def _product_map(self, limit: int = 500) -> Dict[str, Dict[str, Any]]:
        return {p["product_id"]: p for p in self.catalog.list_products_for_admin(limit=limit)}

    def _score_product(
        self, product: Dict[str, Any], sales: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        pid = product["product_id"]
        stats = self.reviews.product_review_stats(pid)
        demand = sales.get(pid, {"units": 0, "orders": 0, "revenue": 0.0})
        pot = potential_engine.product_potential(stats, demand)
        return {
            "product_id": pid,
            "title": product.get("title", ""),
            "category": product.get("category", ""),
            "brand": product.get("brand", ""),
            "price": product.get("price", 0.0),
            "image_url": product.get("image_url", ""),
            "review_count": stats["count"],
            "avg_rating": stats["avg_rating"],
            "positive_share": stats["positive_share"],
            "negative_share": stats["negative_share"],
            "rating_trend": stats["rating_trend"],
            "recent_share": stats["recent_share"],
            "score": pot["score"],
            "tier": pot["tier"],
            "components": pot["components"],
            "confidence": pot["confidence"],
            "demand": demand,
        }

    # ---------------------------------------------------- product analytics
    _SORTS = {
        "score": lambda p: p["score"],
        "rating": lambda p: p["avg_rating"],
        "reviews": lambda p: p["review_count"],
        "trend": lambda p: p["rating_trend"],
    }

    def list_product_analytics(
        self, sort: str = "score", limit: int = 200,
        category: Optional[str] = None, order: str = "desc",
    ) -> List[Dict[str, Any]]:
        sales = self.catalog.product_sales()
        products = self.catalog.list_products_for_admin(limit=500)
        rows = [self._score_product(p, sales) for p in products]
        if category:
            rows = [r for r in rows if r["category"] == category]
        key = self._SORTS.get(sort, self._SORTS["score"])
        rows.sort(key=key, reverse=(order != "asc"))
        return rows[: max(1, min(int(limit), 500))]

    def product_detail_analytics(
        self, product_id: str, with_ai: bool = False, review_limit: int = 20,
    ) -> Optional[Dict[str, Any]]:
        product = self.catalog.get_product(product_id)
        if product is None:
            return None
        sales = self.catalog.product_sales()
        demand = sales.get(product_id, {"units": 0, "orders": 0, "revenue": 0.0})
        stats = self.reviews.product_review_stats(product_id)
        reviews = self.reviews.list_reviews(product_id, limit=review_limit)
        # Full potential here (includes the explainable driver breakdown).
        pot = potential_engine.product_potential(stats, demand)
        insight = self._get_insight(product, stats, pot, reviews, with_ai)
        return {
            "product": {
                "product_id": product_id,
                "title": product.get("title", ""),
                "category": product.get("category", ""),
                "brand": product.get("brand", ""),
                "price": product.get("price", 0.0),
                "image_url": product.get("image_url", ""),
                "description": product.get("description", ""),
            },
            "potential": pot,
            "stats": stats,
            "demand": demand,
            "reviews": reviews,
            "insight": insight,
        }

    def _get_insight(self, product, stats, pot, reviews, with_ai) -> Dict[str, Any]:
        pid = product["product_id"]
        if not with_ai:
            return self.ai._heuristic_insight(product, stats, pot)
        cached = self._insight_cache.get(pid)
        if cached and cached.get("rev") == stats["count"]:
            return cached["insight"]
        insight = self.ai.analyze_product(product, stats, pot, reviews)
        self._insight_cache[pid] = {"rev": stats["count"], "insight": insight, "ts": time.time()}
        return insight

    # ------------------------------------------------------ store analytics
    def store_analytics(self, with_ai: bool = False) -> Dict[str, Any]:
        product_scores = self.list_product_analytics(sort="score", limit=500)
        store_stats = self.reviews.store_review_stats()
        sp = potential_engine.store_potential(product_scores, store_stats)
        summary = self._get_store_summary(sp, store_stats, with_ai)
        return {
            "generated_at": time.time(),
            "store": sp,
            "stats": store_stats,
            "summary": summary,
        }

    def _get_store_summary(self, sp, store_stats, with_ai) -> Dict[str, Any]:
        if not with_ai:
            return self.ai._heuristic_store_summary(sp)
        if self._store_cache.get("rev") == store_stats["count"]:
            return self._store_cache["summary"]
        summary = self.ai.summarize_store(sp, store_stats)
        self._store_cache = {"rev": store_stats["count"], "summary": summary, "ts": time.time()}
        return summary

    # ----------------------------------------------------------- mutations
    def submit_review(
        self, product_id: str, customer_id: str, rating: int,
        title: str = "", content: str = "", tags: Optional[List[str]] = None,
        author_name: str = "",
    ) -> Dict[str, Any]:
        if self.catalog.get_product(product_id) is None:
            raise ValueError("product not found")
        result = self.reviews.add_review(
            product_id=product_id, customer_id=customer_id, rating=rating,
            title=title, content=content, tags=tags or [], aspects=[],
            author_name=author_name, source="user",
        )
        self._bump_catalog_rating(product_id, rating)
        self._insight_cache.pop(product_id, None)
        self._store_cache.clear()
        return result

    def generate_reviews_for(
        self, product_id: str, n: int = 5, skew: str = "mixed", persist: bool = True,
    ) -> Dict[str, Any]:
        product = self.catalog.get_product(product_id)
        if product is None:
            raise ValueError("product not found")
        gen = self.ai.generate_reviews(product, n=n, skew=skew)
        written = 0
        if persist:
            now = time.time()
            for i, r in enumerate(gen["reviews"]):
                # Land generated reviews across the recent window so they read as
                # fresh feedback and meaningfully move momentum.
                self.reviews.add_review(
                    product_id=product_id,
                    customer_id=f"ai-gen-{product_id}-{int(now)}-{i}",
                    rating=r["rating"], title=r.get("title", ""),
                    content=r.get("content", ""), tags=[], aspects=r.get("aspects", []),
                    author_name="AI 生成", source="ai",
                    created_at=now - i * 3600.0,
                )
                written += 1
            self._insight_cache.pop(product_id, None)
            self._store_cache.clear()
        return {
            "generated_by": gen["generated_by"],
            "persisted": written,
            "reviews": gen["reviews"],
        }

    def _bump_catalog_rating(self, product_id: str, rating: int) -> None:
        """Incrementally fold a new user rating into the product's headline
        rating/count (keeps the large seeded counts intact)."""
        try:
            product = self.catalog.get_product(product_id)
            if product is None:
                return
            old_avg = float(product.get("rating", 0.0))
            old_count = int(product.get("rating_count", 0))
            new_count = old_count + 1
            new_avg = (old_avg * old_count + rating) / new_count if new_count else rating
            self.catalog.update_product_rating(product_id, new_avg, new_count)
        except Exception as exc:
            self._log(f"catalog rating sync skipped: {exc}")
