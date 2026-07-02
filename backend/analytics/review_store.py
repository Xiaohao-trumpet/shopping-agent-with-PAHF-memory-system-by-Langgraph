"""SQLite-backed product review store.

This is the raw signal layer for the review-analytics module. Unlike
``conversation_ratings`` (which scores the *service* experience), these rows
score individual *products*, which is what lets us analyse a product's — and by
aggregation the whole store's — development potential.

Design matches the other stores (single SQLite file, ``sqlite3.Row`` access,
no ORM, per-call connections). A deterministic demo seed generates a realistic
spread of reviews (rising / declining / polarized products) so the analytics
dashboard has something meaningful to show offline.
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional


DEMO_REVIEWS_VERSION = "2026-07-reviews-v1"
_DAY = 86400.0
_WINDOW_DAYS = 30.0          # "recent" window for momentum
_HORIZON_DAYS = 90.0         # how far back the demo distributes reviews

# Suggested aspect tags surfaced by the storefront review form.
REVIEW_TAGS = [
    "质量好", "性价比高", "物流快", "外观好看", "描述相符",
    "有点小贵", "包装一般", "尺寸偏差", "客服响应慢", "与图不符",
]


def _sentiment_of(rating: int) -> str:
    if rating >= 4:
        return "positive"
    if rating <= 2:
        return "negative"
    return "neutral"


# --------------------------------------------------------------- demo content
_ASPECTS_BY_CATEGORY = {
    "数码3C": ["续航", "做工", "性能", "屏幕", "手感", "散热", "系统流畅度", "性价比"],
    "服饰鞋包": ["版型", "面料", "做工", "尺码", "舒适度", "配色", "性价比"],
    "家居日用": ["用料", "做工", "实用性", "静音效果", "颜值", "性价比"],
    "美妆个护": ["肤感", "成分", "香味", "效果", "包装", "性价比"],
    "母婴宠物": ["安全性", "材质", "做工", "实用性", "性价比"],
    "食品饮料": ["口感", "新鲜度", "分量", "包装", "性价比"],
    "运动户外": ["做工", "耐用度", "便携性", "舒适度", "性价比"],
    "图书文具": ["纸质", "手感", "做工", "实用性", "性价比"],
}
_DEFAULT_ASPECTS = ["质量", "做工", "实用性", "性价比"]

_POS_EXTRA = ["物有所值", "质感超出预期", "客服态度也很好", "包装很用心", "细节处理到位", "会再来回购"]
_NEG_EXTRA = ["客服响应有点慢", "包装比较简陋", "希望后续能优化", "和预期有差距", "发货有点慢"]
_NEU_EXTRA = ["价格还算合适", "谈不上惊艳", "日常使用够用", "中规中矩"]

_AUTHORS = [
    "匿名用户", "J***n", "小满", "阿哲", "Lynn", "momo", "用户8261", "四月",
    "老张", "Cathy", "二狗", "星野", "Emma", "大鹏", "Nina", "阿宝", "team_lily",
]

# Per-product behaviour profiles used by the seed. The mix is intentional so the
# store shows stars, rising potential, stable and at-risk products.
_PROFILES = ["star", "rising", "stable", "declining", "polarized", "stable", "rising"]


def _rating_for(profile: str, is_recent: bool, is_old: bool, rng: random.Random) -> int:
    if profile == "star":
        return rng.choice([5, 5, 5, 4, 4, 5])
    if profile == "rising":
        if is_old:
            return rng.choice([3, 4, 3, 4, 2])
        if is_recent:
            return rng.choice([5, 5, 4, 5])
        return rng.choice([4, 4, 5, 3])
    if profile == "declining":
        if is_old:
            return rng.choice([5, 5, 4, 5])
        if is_recent:
            return rng.choice([2, 3, 2, 3, 1])
        return rng.choice([3, 4, 3, 2])
    if profile == "polarized":
        return rng.choice([5, 5, 2, 1, 5, 3, 4, 1])
    # stable
    return rng.choice([4, 4, 5, 4, 3, 4])


def _compose_comment(category: str, brand: str, aspect: str, sentiment: str, rng: random.Random) -> str:
    if sentiment == "positive":
        extra = rng.choice(_POS_EXTRA)
        return rng.choice([
            f"{aspect}很不错，{extra}，推荐入手。",
            f"用了一段时间，{aspect}让我惊喜，{extra}。",
            f"{brand}这款{aspect}确实到位，{extra}。",
        ])
    if sentiment == "negative":
        extra = rng.choice(_NEG_EXTRA)
        return rng.choice([
            f"{aspect}一般，{extra}，有点失望。",
            f"{aspect}和描述有差距，{extra}，希望改进。",
            f"冲着{brand}买的，但{aspect}不太行，{extra}。",
        ])
    extra = rng.choice(_NEU_EXTRA)
    return f"整体还行，{aspect}中规中矩，{extra}。"


def _title_for(sentiment: str, rng: random.Random) -> str:
    if sentiment == "positive":
        return rng.choice(["很满意", "超出预期", "值得推荐", "回购好评", "香"])
    if sentiment == "negative":
        return rng.choice(["有待改进", "略失望", "一般般", "货不对板"])
    return rng.choice(["中规中矩", "还可以", "凑合"])


class ReviewStore:
    """SQLite store for per-product customer reviews."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------ infra
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS product_reviews (
                    review_id    TEXT PRIMARY KEY,
                    product_id   TEXT NOT NULL,
                    customer_id  TEXT NOT NULL,
                    author_name  TEXT NOT NULL,
                    rating       INTEGER NOT NULL,
                    title        TEXT NOT NULL DEFAULT '',
                    content      TEXT NOT NULL DEFAULT '',
                    tags_json    TEXT NOT NULL DEFAULT '[]',
                    aspects_json TEXT NOT NULL DEFAULT '[]',
                    sentiment    TEXT NOT NULL,
                    source       TEXT NOT NULL DEFAULT 'user',  -- user | ai | seed
                    helpful      INTEGER NOT NULL DEFAULT 0,
                    created_at   REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rev_product ON product_reviews(product_id);
                CREATE INDEX IF NOT EXISTS idx_rev_created ON product_reviews(created_at);

                CREATE TABLE IF NOT EXISTS review_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def close(self) -> None:  # parity with the other stores
        return None

    @staticmethod
    def _row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "review_id": row["review_id"],
            "product_id": row["product_id"],
            "customer_id": row["customer_id"],
            "author_name": row["author_name"],
            "rating": int(row["rating"]),
            "title": row["title"],
            "content": row["content"],
            "tags": json.loads(row["tags_json"] or "[]"),
            "aspects": json.loads(row["aspects_json"] or "[]"),
            "sentiment": row["sentiment"],
            "source": row["source"],
            "helpful": int(row["helpful"]),
            "created_at": float(row["created_at"]),
        }

    # ------------------------------------------------------------------ write
    def add_review(
        self,
        product_id: str,
        customer_id: str,
        rating: int,
        title: str = "",
        content: str = "",
        tags: Optional[List[str]] = None,
        aspects: Optional[List[str]] = None,
        author_name: str = "",
        source: str = "user",
        created_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        rating = max(1, min(5, int(rating)))
        review_id = f"RV{uuid.uuid4().hex[:12].upper()}"
        now = created_at if created_at is not None else time.time()
        row = (
            review_id, product_id, customer_id,
            author_name or (customer_id[:2] + "***" if customer_id else "匿名用户"),
            rating, title or "", content or "",
            json.dumps(tags or [], ensure_ascii=False),
            json.dumps(aspects or [], ensure_ascii=False),
            _sentiment_of(rating), source, 0, now,
        )
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO product_reviews(
                       review_id, product_id, customer_id, author_name, rating,
                       title, content, tags_json, aspects_json, sentiment,
                       source, helpful, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
            conn.commit()
        return {"review_id": review_id, "product_id": product_id, "rating": rating,
                "sentiment": _sentiment_of(rating)}

    # ------------------------------------------------------------------- read
    def list_reviews(
        self,
        product_id: str,
        limit: int = 50,
        sentiment: Optional[str] = None,
        sort: str = "recent",
    ) -> List[Dict[str, Any]]:
        order = "helpful DESC, created_at DESC" if sort == "helpful" else "created_at DESC"
        params: List[Any] = [product_id]
        clause = "WHERE product_id = ?"
        if sentiment in ("positive", "neutral", "negative"):
            clause += " AND sentiment = ?"
            params.append(sentiment)
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM product_reviews {clause} ORDER BY {order} LIMIT ?",
                params,
            ).fetchall()
        return [self._row(r) for r in rows]

    def review_count(self, product_id: Optional[str] = None) -> int:
        with self._connect() as conn:
            if product_id:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM product_reviews WHERE product_id = ?",
                    (product_id,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS c FROM product_reviews").fetchone()
        return int(row["c"])

    # --------------------------------------------------------------- analytics
    @staticmethod
    def _empty_stats() -> Dict[str, Any]:
        return {
            "count": 0,
            "avg_rating": 0.0,
            "distribution": {str(i): 0 for i in range(1, 6)},
            "sentiment": {"positive": 0, "neutral": 0, "negative": 0},
            "positive_share": 0.0,
            "negative_share": 0.0,
            "recent_avg": 0.0,
            "baseline_avg": 0.0,
            "recent_count": 0,
            "recent_share": 0.0,
            "rating_trend": 0.0,
            "top_tags": [],
            "top_aspects": [],
            "trend": [],
        }

    def _stats_from_rows(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        stats = self._empty_stats()
        if not rows:
            return stats
        now = time.time()
        count = len(rows)
        total = sum(r["rating"] for r in rows)
        dist = {str(i): 0 for i in range(1, 6)}
        sent = {"positive": 0, "neutral": 0, "negative": 0}
        tag_c: Counter = Counter()
        aspect_c: Counter = Counter()
        recent_sum = recent_n = 0
        base_sum = base_n = 0
        buckets = [
            {"period": "60-90天", "lo": 60 * _DAY, "hi": 90 * _DAY, "sum": 0, "n": 0},
            {"period": "30-60天", "lo": 30 * _DAY, "hi": 60 * _DAY, "sum": 0, "n": 0},
            {"period": "近30天", "lo": 0.0, "hi": 30 * _DAY, "sum": 0, "n": 0},
        ]
        for r in rows:
            dist[str(r["rating"])] += 1
            sent[r["sentiment"]] = sent.get(r["sentiment"], 0) + 1
            for t in r["tags"]:
                tag_c[t] += 1
            for a in r["aspects"]:
                aspect_c[a] += 1
            age = now - r["created_at"]
            if age <= _WINDOW_DAYS * _DAY:
                recent_sum += r["rating"]
                recent_n += 1
            else:
                base_sum += r["rating"]
                base_n += 1
            for b in buckets:
                if b["lo"] <= age < b["hi"]:
                    b["sum"] += r["rating"]
                    b["n"] += 1
                    break

        avg = round(total / count, 2)
        recent_avg = round(recent_sum / recent_n, 2) if recent_n else avg
        baseline_avg = round(base_sum / base_n, 2) if base_n else avg
        return {
            "count": count,
            "avg_rating": avg,
            "distribution": dist,
            "sentiment": sent,
            "positive_share": round(sent["positive"] / count, 3),
            "negative_share": round(sent["negative"] / count, 3),
            "recent_avg": recent_avg,
            "baseline_avg": baseline_avg,
            "recent_count": recent_n,
            "recent_share": round(recent_n / count, 3),
            "rating_trend": round(recent_avg - baseline_avg, 2),
            "top_tags": [{"tag": t, "count": c} for t, c in tag_c.most_common(8)],
            "top_aspects": [{"aspect": a, "count": c} for a, c in aspect_c.most_common(8)],
            "trend": [
                {"period": b["period"], "count": b["n"],
                 "avg": round(b["sum"] / b["n"], 2) if b["n"] else None}
                for b in buckets
            ],
        }

    def product_review_stats(self, product_id: str) -> Dict[str, Any]:
        return self._stats_from_rows(self.list_reviews(product_id, limit=500))

    def store_review_stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM product_reviews").fetchall()
        return self._stats_from_rows([self._row(r) for r in rows])

    # -------------------------------------------------------------- demo seed
    def _meta(self, key: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM review_meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    def needs_demo_seed(self) -> bool:
        return self.review_count() == 0 or self._meta("demo_reviews_version") != DEMO_REVIEWS_VERSION

    def seed_demo_reviews(self, products: List[Dict[str, Any]], reset: bool = True) -> int:
        """Generate a deterministic, realistic review corpus for the given demo
        products. ``products`` items need ``product_id``, ``category``, ``brand``
        (``title`` optional). Returns the number of reviews written."""
        now = time.time()
        written = 0
        with self._connect() as conn:
            if reset:
                conn.execute("DELETE FROM product_reviews")
            for product in products:
                pid = product["product_id"]
                category = product.get("category", "")
                brand = product.get("brand", "") or "该品牌"
                # Deterministic per-product RNG + profile.
                seed = int(uuid.uuid5(uuid.NAMESPACE_DNS, pid).int % (2 ** 32))
                rng = random.Random(seed)
                profile = _PROFILES[seed % len(_PROFILES)]
                aspects_pool = _ASPECTS_BY_CATEGORY.get(category, _DEFAULT_ASPECTS)
                n = 6 + (seed % 9)  # 6..14 reviews
                for i in range(n):
                    # Even spread oldest->newest across the horizon, with jitter.
                    frac = (i + 0.5) / n
                    age_days = _HORIZON_DAYS * (1.0 - frac) + rng.uniform(-2.0, 2.0)
                    age_days = max(0.2, min(_HORIZON_DAYS, age_days))
                    is_recent = age_days <= _WINDOW_DAYS
                    is_old = age_days >= 60.0
                    rating = _rating_for(profile, is_recent, is_old, rng)
                    sentiment = _sentiment_of(rating)
                    aspect = rng.choice(aspects_pool)
                    tags = [aspect] + rng.sample(
                        REVIEW_TAGS, k=1 if sentiment != "neutral" else 0
                    )
                    review = {
                        "product_id": pid,
                        "customer_id": f"seed-{pid}-{i}",
                        "author_name": rng.choice(_AUTHORS),
                        "rating": rating,
                        "title": _title_for(sentiment, rng),
                        "content": _compose_comment(category, brand, aspect, sentiment, rng),
                        "tags": tags,
                        "aspects": [aspect],
                        "sentiment": sentiment,
                        "source": "seed",
                        "created_at": now - age_days * _DAY,
                    }
                    conn.execute(
                        """INSERT INTO product_reviews(
                               review_id, product_id, customer_id, author_name, rating,
                               title, content, tags_json, aspects_json, sentiment,
                               source, helpful, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            f"RV{uuid.uuid4().hex[:12].upper()}", review["product_id"],
                            review["customer_id"], review["author_name"], review["rating"],
                            review["title"], review["content"],
                            json.dumps(review["tags"], ensure_ascii=False),
                            json.dumps(review["aspects"], ensure_ascii=False),
                            review["sentiment"], review["source"],
                            rng.randint(0, 40), review["created_at"],
                        ),
                    )
                    written += 1
            conn.execute(
                """INSERT INTO review_meta(key, value) VALUES ('demo_reviews_version', ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (DEMO_REVIEWS_VERSION,),
            )
            conn.commit()
        return written
