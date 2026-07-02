"""Development-potential scoring for products and the store.

This is a deterministic, explainable engine (in the spirit of the escalation
router): given a product's review statistics and its demand signal, it produces
a 0-100 "development potential" score, a tier label, and the ranked drivers that
explain the score. The AI layer (``ai_reviewer``) narrates on top of this — the
numbers themselves never depend on an LLM being available.

The score blends five normalised sub-scores:

  satisfaction  how high the ratings are right now
  momentum      the trajectory — rating trend + review velocity (the core of
                "potential": is this product getting better and hotter?)
  sentiment     positive vs negative review share
  volume        how much validated feedback exists (log-scaled)
  demand        real pull-through from orders / sales

Weights sum to 1.0; ``momentum`` is weighted highest because potential is about
trajectory, not just the current snapshot.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


WEIGHTS = {
    "satisfaction": 0.28,
    "momentum": 0.30,
    "sentiment": 0.17,
    "volume": 0.15,
    "demand": 0.10,
}

_VOLUME_CAP = 15.0   # review count that maps to a full volume score
_DEMAND_CAP = 12.0   # units sold that maps to a full demand score

# Tier thresholds on the 0-100 score.
_TIERS = [
    (72.0, "star", "明星", "高分且势能强劲，可加大投放与备货"),
    (58.0, "rising", "潜力", "上升趋势明显，值得重点培育"),
    (42.0, "stable", "平稳", "表现稳健，保持运营节奏"),
    (0.0, "at_risk", "预警", "口碑或势能走弱，需要介入优化"),
]

_LABELS = {
    "satisfaction": "满意度",
    "momentum": "增长势能",
    "sentiment": "口碑情绪",
    "volume": "评价体量",
    "demand": "销售拉动",
}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _tier_for(score: float) -> Dict[str, str]:
    for threshold, key, label, advice in _TIERS:
        if score >= threshold:
            return {"key": key, "label": label, "advice": advice}
    return {"key": "at_risk", "label": "预警", "advice": _TIERS[-1][3]}


def _tone(value: float) -> str:
    if value >= 0.66:
        return "positive"
    if value <= 0.40:
        return "negative"
    return "neutral"


def _reason(component: str, value: float, stats: Dict[str, Any], demand: Dict[str, Any]) -> str:
    if component == "satisfaction":
        return f"均分 {stats.get('avg_rating', 0)}★"
    if component == "momentum":
        trend = stats.get("rating_trend", 0.0)
        arrow = "↑" if trend > 0.1 else ("↓" if trend < -0.1 else "→")
        return f"评分趋势 {arrow}{abs(trend):.2f}，近30天占比 {int(stats.get('recent_share', 0) * 100)}%"
    if component == "sentiment":
        return f"好评 {int(stats.get('positive_share', 0) * 100)}% / 差评 {int(stats.get('negative_share', 0) * 100)}%"
    if component == "volume":
        return f"{stats.get('count', 0)} 条评价"
    if component == "demand":
        return f"售出 {demand.get('units', 0)} 件 / {demand.get('orders', 0)} 单"
    return ""


def product_potential(
    review_stats: Dict[str, Any],
    demand: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Score a single product's development potential.

    ``review_stats`` is a ``ReviewStore.product_review_stats`` result.
    ``demand`` is optional ``{"units": int, "orders": int, "revenue": float}``.
    """
    demand = demand or {"units": 0, "orders": 0, "revenue": 0.0}
    count = int(review_stats.get("count", 0))

    if count == 0:
        components = {k: 0.0 for k in WEIGHTS}
        return {
            "score": 0.0,
            "tier": {"key": "unrated", "label": "待评价", "advice": "暂无评价，建议引导用户评价或用 AI 生成示例评价"},
            "components": components,
            "drivers": [],
            "confidence": "none",
        }

    avg = float(review_stats.get("avg_rating", 0.0))
    recent_avg = float(review_stats.get("recent_avg", avg))
    baseline_avg = float(review_stats.get("baseline_avg", avg))
    recent_share = float(review_stats.get("recent_share", 0.0))
    pos = float(review_stats.get("positive_share", 0.0))
    neg = float(review_stats.get("negative_share", 0.0))

    satisfaction = _clamp((avg - 3.0) / 1.5)
    rating_trend = _clamp(0.5 + (recent_avg - baseline_avg) / 2.0)
    velocity = _clamp(recent_share / 0.5)          # >50% of reviews recent -> full
    momentum = _clamp(0.6 * rating_trend + 0.4 * velocity)
    sentiment = _clamp(pos - neg * 1.2)
    volume = _clamp(math.log10(1 + count) / math.log10(1 + _VOLUME_CAP))
    units = float(demand.get("units", 0))
    demand_score = _clamp(math.log10(1 + units) / math.log10(1 + _DEMAND_CAP))

    components = {
        "satisfaction": round(satisfaction, 3),
        "momentum": round(momentum, 3),
        "sentiment": round(sentiment, 3),
        "volume": round(volume, 3),
        "demand": round(demand_score, 3),
    }
    score = round(100.0 * sum(WEIGHTS[k] * components[k] for k in WEIGHTS), 1)

    drivers: List[Dict[str, Any]] = []
    for key, value in components.items():
        contribution = round(WEIGHTS[key] * value * 100.0, 1)
        drivers.append({
            "key": key,
            "label": _LABELS[key],
            "value": value,
            "weight": WEIGHTS[key],
            "contribution": contribution,
            "tone": _tone(value),
            "reason": _reason(key, value, review_stats, demand),
        })
    # Most decisive drivers first (largest absolute deviation from a neutral 0.5).
    drivers.sort(key=lambda d: abs(d["value"] - 0.5) * d["weight"], reverse=True)

    confidence = "high" if count >= 8 else ("medium" if count >= 3 else "low")
    return {
        "score": score,
        "tier": _tier_for(score),
        "components": components,
        "drivers": drivers,
        "confidence": confidence,
    }


def store_potential(
    product_scores: List[Dict[str, Any]],
    store_stats: Dict[str, Any],
) -> Dict[str, Any]:
    """Aggregate product potentials into a store-level view.

    ``product_scores`` items are the enriched product analytics rows
    (``{product_id, category, score, tier, review_count, avg_rating, ...}``).
    """
    rated = [p for p in product_scores if p.get("review_count", 0) > 0]
    tier_counts = {"star": 0, "rising": 0, "stable": 0, "at_risk": 0, "unrated": 0}
    for p in product_scores:
        key = p.get("tier", {}).get("key", "unrated")
        tier_counts[key] = tier_counts.get(key, 0) + 1

    if rated:
        weight_total = sum(p["review_count"] + 1 for p in rated)
        store_score = round(
            sum(p["score"] * (p["review_count"] + 1) for p in rated) / weight_total, 1
        )
    else:
        store_score = 0.0

    # Category breakdown.
    cat_map: Dict[str, Dict[str, Any]] = {}
    for p in rated:
        cat = p.get("category", "未分类")
        entry = cat_map.setdefault(cat, {"category": cat, "score_sum": 0.0, "n": 0,
                                         "reviews": 0, "rating_sum": 0.0})
        entry["score_sum"] += p["score"]
        entry["n"] += 1
        entry["reviews"] += p["review_count"]
        entry["rating_sum"] += p.get("avg_rating", 0.0)
    categories = [
        {
            "category": e["category"],
            "avg_score": round(e["score_sum"] / e["n"], 1),
            "avg_rating": round(e["rating_sum"] / e["n"], 2),
            "products": e["n"],
            "reviews": e["reviews"],
        }
        for e in cat_map.values()
    ]
    categories.sort(key=lambda c: c["avg_score"], reverse=True)

    ranked = sorted(rated, key=lambda p: p["score"], reverse=True)
    top_products = ranked[:5]
    watch_products = [p for p in ranked if p.get("tier", {}).get("key") == "at_risk"][:5]
    if not watch_products:
        watch_products = ranked[-5:][::-1] if len(ranked) > 5 else []

    return {
        "score": store_score,
        "tier": _tier_for(store_score),
        "rated_products": len(rated),
        "total_products": len(product_scores),
        "tier_counts": tier_counts,
        "avg_rating": store_stats.get("avg_rating", 0.0),
        "total_reviews": store_stats.get("count", 0),
        "positive_share": store_stats.get("positive_share", 0.0),
        "negative_share": store_stats.get("negative_share", 0.0),
        "rating_trend": store_stats.get("rating_trend", 0.0),
        "recent_share": store_stats.get("recent_share", 0.0),
        "categories": categories,
        "top_products": [
            {"product_id": p["product_id"], "title": p.get("title", ""),
             "score": p["score"], "tier": p["tier"], "avg_rating": p.get("avg_rating", 0.0)}
            for p in top_products
        ],
        "watch_products": [
            {"product_id": p["product_id"], "title": p.get("title", ""),
             "score": p["score"], "tier": p["tier"], "avg_rating": p.get("avg_rating", 0.0)}
            for p in watch_products
        ],
    }
