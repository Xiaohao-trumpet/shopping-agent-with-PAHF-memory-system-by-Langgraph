"""Tests for the review-analytics subsystem (offline / heuristic path)."""

from backend.tools.catalog_store import CatalogStore
from backend.analytics import ReviewStore, AIReviewer, AnalyticsService
from backend.analytics import potential as pe


def _catalog(tmp_path):
    return CatalogStore(db_path=str(tmp_path / "catalog.db"), auto_seed=True)


def _service(tmp_path):
    catalog = _catalog(tmp_path)
    reviews = ReviewStore(db_path=str(tmp_path / "reviews.db"))
    svc = AnalyticsService(reviews, catalog, AIReviewer(model_client=None))
    svc.ensure_seeded(auto_seed=True)
    return catalog, reviews, svc


# ------------------------------------------------------------- review store
def test_review_store_seed_and_stats(tmp_path):
    _, reviews, _ = _service(tmp_path)
    assert reviews.review_count() > 100
    assert not reviews.needs_demo_seed()

    stats = reviews.product_review_stats("P1002")
    assert stats["count"] > 0
    assert 1.0 <= stats["avg_rating"] <= 5.0
    assert sum(stats["distribution"].values()) == stats["count"]
    assert len(stats["trend"]) == 3  # 3 time buckets


def test_review_store_add_and_filter(tmp_path):
    _, reviews, _ = _service(tmp_path)
    before = reviews.review_count("P1002")
    reviews.add_review("P1002", "tester", 1, content="很差", source="user")
    assert reviews.review_count("P1002") == before + 1
    negs = reviews.list_reviews("P1002", sentiment="negative")
    assert all(r["sentiment"] == "negative" for r in negs)


# -------------------------------------------------------------- potential
def test_product_potential_tiers():
    high = {
        "count": 12, "avg_rating": 4.8, "positive_share": 0.9, "negative_share": 0.0,
        "recent_avg": 4.9, "baseline_avg": 4.5, "recent_share": 0.5,
    }
    declining = {
        "count": 11, "avg_rating": 2.5, "positive_share": 0.2, "negative_share": 0.5,
        "recent_avg": 2.0, "baseline_avg": 4.0, "recent_share": 0.5,
    }
    top = pe.product_potential(high, {"units": 5, "orders": 3})
    bad = pe.product_potential(declining, {"units": 0, "orders": 0})
    empty = pe.product_potential({"count": 0}, None)

    assert top["score"] >= 72 and top["tier"]["key"] == "star"
    assert bad["score"] < 42 and bad["tier"]["key"] == "at_risk"
    assert empty["tier"]["key"] == "unrated"
    # drivers are explainable and cover all five components
    assert {d["key"] for d in top["drivers"]} == set(pe.WEIGHTS)


def test_store_potential_aggregate(tmp_path):
    _, _, svc = _service(tmp_path)
    rows = svc.list_product_analytics(sort="score", limit=500)
    store_stats = svc.reviews.store_review_stats()
    sp = pe.store_potential(rows, store_stats)

    assert 0 < sp["score"] <= 100
    assert sum(sp["tier_counts"].values()) == sp["total_products"]
    assert sp["categories"] == sorted(sp["categories"], key=lambda c: c["avg_score"], reverse=True)
    assert sp["top_products"]  # non-empty for a seeded store


# -------------------------------------------------------------- ai reviewer
def test_ai_reviewer_heuristic_fallback():
    ai = AIReviewer(model_client=None)
    assert ai.available is False

    product = {"product_id": "P1", "title": "测试耳机", "category": "数码3C", "brand": "Test"}
    gen = ai.generate_reviews(product, n=4, skew="positive")
    assert gen["generated_by"] == "heuristic"
    assert len(gen["reviews"]) == 4
    assert all(1 <= r["rating"] <= 5 and r["content"] for r in gen["reviews"])

    stats = {"count": 8, "avg_rating": 4.5, "positive_share": 0.8, "negative_share": 0.1,
             "rating_trend": 0.3, "recent_share": 0.5, "top_aspects": [{"aspect": "续航", "count": 3}],
             "top_tags": [{"tag": "质量好", "count": 4}]}
    pot = pe.product_potential(stats, {"units": 2, "orders": 2})
    insight = ai.analyze_product(product, stats, pot, [{"rating": 5, "content": "很好"}])
    assert insight["generated_by"] == "heuristic"
    assert insight["pros"] and insight["cons"] and insight["recommended_actions"]
    assert insight["risk_level"] in ("low", "medium", "high")

    sp = pe.store_potential([], stats)
    summary = ai.summarize_store(sp, stats)
    assert summary["generated_by"] == "heuristic"
    assert summary["headline"]


# --------------------------------------------------------- analytics service
def test_analytics_service_end_to_end(tmp_path):
    catalog, reviews, svc = _service(tmp_path)

    rows = svc.list_product_analytics(sort="score", limit=100)
    assert rows and rows[0]["score"] >= rows[-1]["score"]
    tiers = {r["tier"]["key"] for r in rows}
    # A realistic seed spans multiple tiers, including at-risk products.
    assert "star" in tiers and "at_risk" in tiers

    store = svc.store_analytics(with_ai=False)
    assert store["store"]["total_reviews"] == reviews.review_count()
    assert store["summary"]["generated_by"] == "heuristic"

    pid = rows[0]["product_id"]
    detail = svc.product_detail_analytics(pid, with_ai=False)
    assert detail["stats"]["count"] > 0
    assert detail["potential"]["drivers"]
    assert detail["insight"]["recommended_actions"]

    # AI-generate reviews persists and moves the count.
    before = reviews.review_count(pid)
    gen = svc.generate_reviews_for(pid, n=3, skew="positive")
    assert gen["persisted"] == 3
    assert reviews.review_count(pid) == before + 3

    # A user review folds into the product's headline rating count.
    prod_before = catalog.get_product(pid)
    svc.submit_review(pid, "demo-user", 5, content="满意")
    prod_after = catalog.get_product(pid)
    assert prod_after["rating_count"] == prod_before["rating_count"] + 1
