"""SQLite-backed virtual store: products, SKUs, customers, orders, shipments,
coupons and after-sales returns.

This is the business data layer that turns the assistant into a real shopping
customer-service agent. It follows the same lightweight, local-first design as
``TicketStore`` (single SQLite file, ``sqlite3.Row`` access, no ORM).
"""

from __future__ import annotations

import json
import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


DEMO_CATALOG_VERSION = "2026-07-rich-catalog-v3"
DEMO_CATALOG_MIN_PRODUCTS = 40
DEFAULT_CUSTOMER_PASSWORD = "Shop@2026!"


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z0-9_一-鿿]+", str(text).lower())


def _search_units(text: str) -> set:
    """Build matchable units from a query: ascii word tokens plus CJK character
    bigrams (and single chars). This lets short Chinese terms like ``卫衣`` match
    inside longer titles like ``云感纯棉圆领卫衣`` without a tokenizer."""
    text = str(text).lower()
    units = set(re.findall(r"[a-z0-9]+", text))
    for run in re.findall(r"[一-鿿]+", text):
        if len(run) == 1:
            units.add(run)
        else:
            units.update(run[i : i + 2] for i in range(len(run) - 1))
    return units


class CatalogStore:
    """SQLite store for the virtual shop's products, orders and after-sales."""

    def __init__(self, db_path: str, auto_seed: bool = True):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        if auto_seed and self.needs_demo_seed():
            self.seed_demo_data(reset=not self.is_empty())

    # ------------------------------------------------------------------ infra
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS products (
                    product_id   TEXT PRIMARY KEY,
                    title        TEXT NOT NULL,
                    description  TEXT NOT NULL,
                    category     TEXT NOT NULL,
                    brand        TEXT NOT NULL,
                    price        REAL NOT NULL,
                    currency     TEXT NOT NULL,
                    rating       REAL NOT NULL,
                    rating_count INTEGER NOT NULL,
                    image_url    TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    status       TEXT NOT NULL,
                    created_at   REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS skus (
                    sku_id     TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    sku_code   TEXT NOT NULL UNIQUE,
                    attributes_json TEXT NOT NULL,
                    price      REAL NOT NULL,
                    stock      INTEGER NOT NULL,
                    status     TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_skus_product ON skus(product_id);

                CREATE TABLE IF NOT EXISTS customers (
                    customer_id TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    email       TEXT NOT NULL,
                    phone       TEXT NOT NULL,
                    tier        TEXT NOT NULL,
                    password_hash TEXT NOT NULL DEFAULT '',
                    created_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orders (
                    order_id         TEXT PRIMARY KEY,
                    customer_id      TEXT NOT NULL,
                    status           TEXT NOT NULL,
                    total            REAL NOT NULL,
                    currency         TEXT NOT NULL,
                    shipping_address TEXT NOT NULL,
                    shipping_method  TEXT NOT NULL,
                    created_at       REAL NOT NULL,
                    paid_at          REAL
                );
                CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);

                CREATE TABLE IF NOT EXISTS order_items (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id       TEXT NOT NULL,
                    sku_code       TEXT NOT NULL,
                    product_id     TEXT NOT NULL,
                    title_snapshot TEXT NOT NULL,
                    qty            INTEGER NOT NULL,
                    unit_price     REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id);

                CREATE TABLE IF NOT EXISTS shipments (
                    shipment_id TEXT PRIMARY KEY,
                    order_id    TEXT NOT NULL,
                    carrier     TEXT NOT NULL,
                    tracking_no TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    events_json TEXT NOT NULL,
                    updated_at  REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ship_order ON shipments(order_id);

                CREATE TABLE IF NOT EXISTS coupons (
                    code        TEXT PRIMARY KEY,
                    kind        TEXT NOT NULL,
                    value       REAL NOT NULL,
                    min_spend   REAL NOT NULL,
                    description TEXT NOT NULL,
                    valid_from  REAL NOT NULL,
                    valid_to    REAL NOT NULL,
                    active      INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS return_requests (
                    return_id     TEXT PRIMARY KEY,
                    order_id      TEXT NOT NULL,
                    customer_id   TEXT NOT NULL,
                    sku_code      TEXT,
                    reason        TEXT NOT NULL,
                    status        TEXT NOT NULL,
                    refund_amount REAL NOT NULL,
                    created_at    REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_returns_order ON return_requests(order_id);

                CREATE TABLE IF NOT EXISTS catalog_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._ensure_customer_password_column(conn)
            conn.commit()

    @staticmethod
    def _ensure_customer_password_column(conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(customers)").fetchall()}
        if "password_hash" not in columns:
            conn.execute("ALTER TABLE customers ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _hash_password(password: str, salt: Optional[bytes] = None, iterations: int = 200_000) -> str:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return "pbkdf2_sha256${}${}${}".format(
            iterations,
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )

    @classmethod
    def _verify_password(cls, password: str, encoded: str) -> bool:
        try:
            algorithm, iterations_raw, salt_raw, digest_raw = encoded.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            iterations = int(iterations_raw)
            salt = base64.b64decode(salt_raw.encode("ascii"))
            expected = base64.b64decode(digest_raw.encode("ascii"))
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False

    def is_empty(self) -> bool:
        with self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) AS c FROM products")
            return int(cur.fetchone()["c"]) == 0

    def product_count(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) AS c FROM products WHERE status = 'active'")
            return int(cur.fetchone()["c"])

    def demo_catalog_version(self) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM catalog_meta WHERE key = 'demo_catalog_version'"
            ).fetchone()
        return str(row["value"]) if row else ""

    def needs_demo_seed(self) -> bool:
        if self.is_empty():
            return True
        return (
            self.demo_catalog_version() != DEMO_CATALOG_VERSION
            or self.product_count() < DEMO_CATALOG_MIN_PRODUCTS
        )

    @staticmethod
    def _clear_demo_data(conn: sqlite3.Connection) -> None:
        for table in (
            "return_requests",
            "shipments",
            "order_items",
            "orders",
            "coupons",
            "customers",
            "skus",
            "products",
            "catalog_meta",
        ):
            conn.execute(f"DELETE FROM {table}")

    def close(self) -> None:  # parity with other stores; connections are per-call
        return None

    # ------------------------------------------------------------------ rows
    @staticmethod
    def _image_url(product_id: str, image_url: str) -> str:
        if str(image_url).startswith("https://img.shop.local/"):
            return f"/product-images/{product_id}.webp"
        return image_url

    @staticmethod
    def _product_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "product_id": row["product_id"],
            "title": row["title"],
            "description": row["description"],
            "category": row["category"],
            "brand": row["brand"],
            "price": float(row["price"]),
            "currency": row["currency"],
            "rating": float(row["rating"]),
            "rating_count": int(row["rating_count"]),
            "image_url": CatalogStore._image_url(row["product_id"], row["image_url"]),
            "attributes": json.loads(row["attributes_json"] or "{}"),
            "status": row["status"],
        }

    @staticmethod
    def _customer_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "customer_id": row["customer_id"],
            "name": row["name"],
            "email": row["email"],
            "phone": row["phone"],
            "tier": row["tier"],
            "created_at": float(row["created_at"]),
        }

    @staticmethod
    def _sku_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "sku_id": row["sku_id"],
            "product_id": row["product_id"],
            "sku_code": row["sku_code"],
            "attributes": json.loads(row["attributes_json"] or "{}"),
            "price": float(row["price"]),
            "stock": int(row["stock"]),
            "in_stock": int(row["stock"]) > 0,
            "status": row["status"],
        }

    @staticmethod
    def _order_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "order_id": row["order_id"],
            "customer_id": row["customer_id"],
            "status": row["status"],
            "total": float(row["total"]),
            "currency": row["currency"],
            "shipping_address": row["shipping_address"],
            "shipping_method": row["shipping_method"],
            "created_at": float(row["created_at"]),
            "paid_at": float(row["paid_at"]) if row["paid_at"] is not None else None,
        }

    @staticmethod
    def _shipment_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "shipment_id": row["shipment_id"],
            "order_id": row["order_id"],
            "carrier": row["carrier"],
            "tracking_no": row["tracking_no"],
            "status": row["status"],
            "events": json.loads(row["events_json"] or "[]"),
            "updated_at": float(row["updated_at"]),
        }

    @staticmethod
    def _coupon_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "code": row["code"],
            "kind": row["kind"],
            "value": float(row["value"]),
            "min_spend": float(row["min_spend"]),
            "description": row["description"],
            "valid_from": float(row["valid_from"]),
            "valid_to": float(row["valid_to"]),
            "active": bool(row["active"]),
        }

    # ------------------------------------------------------------- products
    def search_products(
        self,
        query: str,
        category: Optional[str] = None,
        max_price: Optional[float] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM products WHERE status = 'active'")
            rows = cur.fetchall()
        q_units = _search_units(query)
        scored: List[tuple] = []
        for row in rows:
            product = self._product_row(row)
            if category and category.lower() not in product["category"].lower():
                continue
            if max_price is not None and product["price"] > max_price:
                continue
            haystack = " ".join(
                [
                    product["title"],
                    product["brand"],
                    product["category"],
                    product["description"],
                    " ".join(f"{k} {v}" for k, v in product["attributes"].items()),
                ]
            ).lower()
            overlap = sum(1 for u in q_units if u in haystack) if q_units else 0
            # Rating acts as a gentle tie-breaker / fallback when there is no query.
            score = overlap + product["rating"] / 10.0
            if q_units and overlap == 0:
                continue
            scored.append((score, product))
        scored.sort(key=lambda x: (x[0], x[1]["rating"]), reverse=True)
        results = [p for _, p in scored[: max(1, top_k)]]
        for product in results:
            product["in_stock"] = self.product_in_stock(product["product_id"])
        return results

    def list_categories(self) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM products WHERE status = 'active' ORDER BY category"
            ).fetchall()
        return [r["category"] for r in rows]

    def admin_summary(self) -> Dict[str, Any]:
        """Aggregate catalog/order metrics for the backoffice dashboard."""
        with self._connect() as conn:
            counts = {}
            for table in (
                "products",
                "skus",
                "customers",
                "orders",
                "coupons",
                "return_requests",
            ):
                row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
                counts[table] = int(row["c"])

            active_products = conn.execute(
                "SELECT COUNT(*) AS c FROM products WHERE status = 'active'"
            ).fetchone()
            stock = conn.execute(
                "SELECT COALESCE(SUM(stock), 0) AS total_stock FROM skus WHERE status = 'active'"
            ).fetchone()
            low_stock = conn.execute(
                "SELECT COUNT(*) AS c FROM skus WHERE status = 'active' AND stock BETWEEN 1 AND 10"
            ).fetchone()
            revenue = conn.execute(
                "SELECT COALESCE(SUM(total), 0) AS total FROM orders WHERE status != 'pending_payment'"
            ).fetchone()
            status_rows = conn.execute(
                "SELECT status, COUNT(*) AS c FROM orders GROUP BY status ORDER BY status"
            ).fetchall()
            category_rows = conn.execute(
                """SELECT category, COUNT(*) AS c
                   FROM products WHERE status = 'active'
                   GROUP BY category ORDER BY c DESC, category ASC"""
            ).fetchall()

        return {
            **counts,
            "active_products": int(active_products["c"]),
            "total_stock": int(stock["total_stock"]),
            "low_stock_skus": int(low_stock["c"]),
            "revenue": float(revenue["total"]),
            "orders_by_status": {r["status"]: int(r["c"]) for r in status_rows},
            "categories": [{"category": r["category"], "count": int(r["c"])} for r in category_rows],
        }

    def get_customer(self, customer_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM customers WHERE customer_id = ?",
                (customer_id.strip(),),
            ).fetchone()
        return self._customer_row(row) if row else None

    def authenticate_customer(self, customer_id: str, password: str) -> Optional[Dict[str, Any]]:
        customer_id = customer_id.strip()
        if not customer_id or not password:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM customers WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()
            if row is None or not self._verify_password(password, row["password_hash"]):
                return None
        return self._customer_row(row)

    def list_customer_accounts(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM customers
                   ORDER BY created_at ASC, customer_id ASC
                   LIMIT ?""",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [self._customer_row(row) for row in rows]

    def list_products_for_admin(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*,
                       COUNT(s.sku_id) AS sku_count,
                       COALESCE(SUM(s.stock), 0) AS stock_total
                FROM products p
                LEFT JOIN skus s ON s.product_id = p.product_id
                GROUP BY p.product_id
                ORDER BY p.created_at DESC, p.product_id ASC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        products = []
        for row in rows:
            product = self._product_row(row)
            product["sku_count"] = int(row["sku_count"])
            product["stock_total"] = int(row["stock_total"])
            product["in_stock"] = int(row["stock_total"]) > 0
            products.append(product)
        return products

    def get_product(self, product_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE product_id = ?", (product_id,)
            ).fetchone()
        if row is None:
            return None
        product = self._product_row(row)
        product["variants"] = self.list_skus(product_id)
        product["in_stock"] = any(v["in_stock"] for v in product["variants"])
        return product

    def list_skus(self, product_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM skus WHERE product_id = ? ORDER BY sku_code", (product_id,)
            ).fetchall()
        return [self._sku_row(r) for r in rows]

    def product_in_stock(self, product_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(stock), 0) AS s FROM skus WHERE product_id = ?",
                (product_id,),
            ).fetchone()
        return int(row["s"]) > 0

    def product_sales(self) -> Dict[str, Dict[str, Any]]:
        """Per-product sales pull-through from paid orders (excludes carts that
        are still ``pending_payment``). Used as the demand signal for potential
        scoring. Returns ``{product_id: {units, orders, revenue}}``."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT oi.product_id AS product_id,
                       COALESCE(SUM(oi.qty), 0) AS units,
                       COUNT(DISTINCT oi.order_id) AS orders,
                       COALESCE(SUM(oi.qty * oi.unit_price), 0) AS revenue
                FROM order_items oi
                JOIN orders o ON o.order_id = oi.order_id
                WHERE o.status != 'pending_payment'
                GROUP BY oi.product_id
                """
            ).fetchall()
        return {
            r["product_id"]: {
                "units": int(r["units"]),
                "orders": int(r["orders"]),
                "revenue": round(float(r["revenue"]), 2),
            }
            for r in rows
        }

    def update_product_rating(self, product_id: str, rating: float, rating_count: int) -> None:
        """Sync a product's headline rating with real review aggregates."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE products SET rating = ?, rating_count = ? WHERE product_id = ?",
                (round(float(rating), 2), int(rating_count), product_id),
            )
            conn.commit()

    def check_inventory(
        self, product_id: Optional[str] = None, sku_code: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if sku_code:
                rows = conn.execute(
                    "SELECT * FROM skus WHERE sku_code = ?", (sku_code,)
                ).fetchall()
            elif product_id:
                rows = conn.execute(
                    "SELECT * FROM skus WHERE product_id = ? ORDER BY sku_code",
                    (product_id,),
                ).fetchall()
            else:
                rows = []
        return [self._sku_row(r) for r in rows]

    def recommend(
        self, query: str = "", exclude_ids: Optional[List[str]] = None, top_k: int = 4
    ) -> List[Dict[str, Any]]:
        """Lightweight recommendation: query-relevance when provided, otherwise
        top-rated in-stock products. ``query`` can be seeded from PAHF memory
        (e.g. the customer's known preferences) by the caller."""
        exclude = set(exclude_ids or [])
        pool = self.search_products(query=query, top_k=top_k * 3) if query.strip() else []
        if not pool:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM products WHERE status = 'active'"
                ).fetchall()
            pool = [self._product_row(r) for r in rows]
            pool.sort(key=lambda p: (p["rating"], p["rating_count"]), reverse=True)
            for product in pool:
                product["in_stock"] = self.product_in_stock(product["product_id"])
        results = [p for p in pool if p["product_id"] not in exclude and p.get("in_stock", True)]
        return results[: max(1, top_k)]

    # --------------------------------------------------------------- orders
    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            if row is None:
                return None
            order = self._order_row(row)
            items = conn.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
            ).fetchall()
        order["items"] = [
            {
                "sku_code": it["sku_code"],
                "product_id": it["product_id"],
                "title": it["title_snapshot"],
                "qty": int(it["qty"]),
                "unit_price": float(it["unit_price"]),
            }
            for it in items
        ]
        shipment = self.get_shipment_by_order(order_id)
        order["shipment"] = shipment
        return order

    def list_orders(self, customer_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM orders WHERE customer_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (customer_id, max(1, limit)),
            ).fetchall()
        return [self._order_row(r) for r in rows]

    def get_shipment_by_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM shipments WHERE order_id = ?", (order_id,)
            ).fetchone()
        return self._shipment_row(row) if row else None

    def get_shipment_by_tracking(self, tracking_no: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM shipments WHERE tracking_no = ?", (tracking_no,)
            ).fetchone()
        return self._shipment_row(row) if row else None

    # -------------------------------------------------------------- coupons
    def list_coupons(self, min_spend: Optional[float] = None) -> List[Dict[str, Any]]:
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM coupons WHERE active = 1 AND valid_from <= ? AND valid_to >= ?",
                (now, now),
            ).fetchall()
        coupons = [self._coupon_row(r) for r in rows]
        if min_spend is not None:
            coupons = [c for c in coupons if c["min_spend"] <= min_spend]
        return coupons

    def get_coupon(self, code: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM coupons WHERE code = ?", (code.upper(),)
            ).fetchone()
        return self._coupon_row(row) if row else None

    def evaluate_coupon(self, code: str, order_total: float) -> Dict[str, Any]:
        coupon = self.get_coupon(code)
        now = time.time()
        if coupon is None:
            return {"valid": False, "reason": "coupon_not_found", "discount": 0.0}
        if not coupon["active"] or not (coupon["valid_from"] <= now <= coupon["valid_to"]):
            return {"valid": False, "reason": "coupon_expired", "discount": 0.0}
        if order_total < coupon["min_spend"]:
            return {
                "valid": False,
                "reason": "below_min_spend",
                "min_spend": coupon["min_spend"],
                "discount": 0.0,
            }
        if coupon["kind"] == "percent":
            discount = round(order_total * coupon["value"] / 100.0, 2)
        else:
            discount = round(min(coupon["value"], order_total), 2)
        return {
            "valid": True,
            "reason": "ok",
            "code": coupon["code"],
            "discount": discount,
            "final_total": round(order_total - discount, 2),
            "description": coupon["description"],
        }

    # ------------------------------------------------------------- returns
    def create_return(
        self,
        order_id: str,
        customer_id: str,
        reason: str,
        sku_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        order = self.get_order(order_id)
        if order is None:
            return {"created": False, "reason": "order_not_found"}
        if order["customer_id"] != customer_id:
            return {"created": False, "reason": "order_not_owned_by_customer"}

        if sku_code:
            refund = sum(
                it["unit_price"] * it["qty"]
                for it in order["items"]
                if it["sku_code"] == sku_code
            )
            if refund <= 0:
                return {"created": False, "reason": "sku_not_in_order"}
        else:
            refund = order["total"]

        now = time.time()
        return_id = f"R{uuid.uuid4().hex[:10].upper()}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO return_requests(
                    return_id, order_id, customer_id, sku_code, reason,
                    status, refund_amount, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (return_id, order_id, customer_id, sku_code, reason,
                 "pending_review", round(refund, 2), now),
            )
            conn.commit()
        return {
            "created": True,
            "return_id": return_id,
            "order_id": order_id,
            "status": "pending_review",
            "refund_amount": round(refund, 2),
        }

    # ---------------------------------------------------------------- seed
    def seed_demo_data(self, reset: bool = False) -> None:
        """Populate a realistic virtual shop catalog for demos and tests."""
        now = time.time()
        day = 86400.0
        cny = "CNY"

        # (product_id, title, desc, category, brand, price, rating, count, image, attrs,
        #  [ (sku_suffix, {attrs}, price, stock) ... ])
        products = [
            ("P1001", "星云 Pro 13 旗舰手机", "6.7英寸 OLED 屏，5000mAh 大电池，超广角三摄。",
             "数码3C", "Nebula", 4299.0, 4.7, 1820,
             "https://img.shop.local/p1001.jpg", {"屏幕": "6.7英寸 OLED", "续航": "5000mAh"},
             [("BLK-256", {"颜色": "曜石黑", "存储": "256GB"}, 4299.0, 32),
              ("BLU-512", {"颜色": "极光蓝", "存储": "512GB"}, 4799.0, 0)]),
            ("P1002", "声波 X 主动降噪耳机", "40小时续航，深度主动降噪，蓝牙5.3。",
             "数码3C", "SonicWave", 899.0, 4.6, 940,
             "https://img.shop.local/p1002.jpg", {"降噪": "主动降噪", "续航": "40小时"},
             [("WHT", {"颜色": "云白"}, 899.0, 58),
              ("BLK", {"颜色": "深空灰"}, 899.0, 12)]),
            ("P1003", "光刃 15 轻薄笔记本", "2.8K 高刷屏，16G+1TB，整机1.3kg。",
             "数码3C", "LumenBlade", 6499.0, 4.8, 510,
             "https://img.shop.local/p1003.jpg", {"内存": "16GB", "重量": "1.3kg"},
             [("16-1T", {"配置": "16G+1TB"}, 6499.0, 9)]),
            ("P2001", "云感纯棉圆领卫衣", "320g 加厚纯棉，宽松落肩版型，男女同款。",
             "服饰鞋包", "Cl0udy", 199.0, 4.5, 3200,
             "https://img.shop.local/p2001.jpg", {"材质": "纯棉", "版型": "宽松"},
             [("GRY-M", {"颜色": "花灰", "尺码": "M"}, 199.0, 80),
              ("GRY-L", {"颜色": "花灰", "尺码": "L"}, 199.0, 0),
              ("BLK-M", {"颜色": "黑色", "尺码": "M"}, 199.0, 45)]),
            ("P2002", "全天候轻量冲锋衣", "三层压胶防水，可拆卸抓绒内胆。",
             "服饰鞋包", "Summit", 599.0, 4.7, 760,
             "https://img.shop.local/p2002.jpg", {"防水": "10000mm", "场景": "户外"},
             [("GRN-L", {"颜色": "松林绿", "尺码": "L"}, 599.0, 20),
              ("BLK-XL", {"颜色": "黑色", "尺码": "XL"}, 599.0, 7)]),
            ("P3001", "静音人体工学办公椅", "网布透气，3D 扶手，腰托支撑。",
             "家居日用", "ErgoNest", 1099.0, 4.6, 1130,
             "https://img.shop.local/p3001.jpg", {"材质": "网布", "承重": "150kg"},
             [("BLK", {"颜色": "黑色"}, 1099.0, 25),
              ("GRY", {"颜色": "浅灰"}, 1099.0, 4)]),
            ("P3002", "暖光护眼台灯", "无频闪，五档色温，Type-C 充电。",
             "家居日用", "Glowy", 159.0, 4.4, 2050,
             "https://img.shop.local/p3002.jpg", {"色温": "可调", "充电": "Type-C"},
             [("WHT", {"颜色": "白色"}, 159.0, 120)]),
            ("P4001", "水光保湿精华 30ml", "玻尿酸+烟酰胺，温和不黏腻。",
             "美妆个护", "Aqua", 239.0, 4.5, 4100,
             "https://img.shop.local/p4001.jpg", {"功效": "保湿", "规格": "30ml"},
             [("STD", {"规格": "30ml"}, 239.0, 200)]),
            ("P4002", "氨基酸温和洁面 120g", "弱酸性配方，敏感肌可用。",
             "美妆个护", "Pure", 89.0, 4.6, 5600,
             "https://img.shop.local/p4002.jpg", {"功效": "清洁", "肤质": "敏感肌"},
             [("STD", {"规格": "120g"}, 89.0, 0)]),
        ]
        products.extend([
            ("P1004", "星云 Mini 8.8 平板电脑", "8.8英寸高刷护眼屏，适合网课、阅读与轻办公。",
             "数码3C", "Nebula", 1899.0, 4.6, 1420,
             "https://img.shop.local/p1004.jpg", {"屏幕": "8.8英寸 LCD", "存储": "128GB"},
             [("GRAY-128", {"颜色": "深空灰", "存储": "128GB"}, 1899.0, 36),
              ("BLUE-256", {"颜色": "海盐蓝", "存储": "256GB"}, 2299.0, 18)]),
            ("P1005", "清风 27 英寸 4K 显示器", "IPS 面板，Type-C 反向供电，适合办公与修图。",
             "数码3C", "ClearView", 1699.0, 4.7, 860,
             "https://img.shop.local/p1005.jpg", {"分辨率": "3840x2160", "接口": "HDMI/DP/Type-C"},
             [("STD", {"尺寸": "27英寸"}, 1699.0, 22)]),
            ("P1006", "悦写 K3 机械键盘", "三模连接，热插拔轴体，PBT 键帽。",
             "数码3C", "KeyMuse", 429.0, 4.5, 2100,
             "https://img.shop.local/p1006.jpg", {"连接": "蓝牙/2.4G/有线", "配列": "84键"},
             [("TEA", {"轴体": "茶轴", "颜色": "月岩灰"}, 429.0, 61),
              ("RED", {"轴体": "红轴", "颜色": "奶油白"}, 429.0, 33)]),
            ("P1007", "Pocket 65W 氮化镓快充头", "三口输出，兼容手机、平板和轻薄本。",
             "数码3C", "VoltGo", 129.0, 4.8, 4800,
             "https://img.shop.local/p1007.jpg", {"功率": "65W", "接口": "2C1A"},
             [("WHITE", {"颜色": "白色"}, 129.0, 180)]),
            ("P1008", "LinkPro AX3000 双频路由器", "Wi-Fi 6，四核处理器，支持 Mesh 组网。",
             "数码3C", "LinkPro", 299.0, 4.5, 1550,
             "https://img.shop.local/p1008.jpg", {"无线": "Wi-Fi 6", "速率": "AX3000"},
             [("STD", {"版本": "标准版"}, 299.0, 44)]),
            ("P1009", "腕上 Pulse S2 智能手表", "全天心率、血氧监测，14天续航。",
             "数码3C", "Pulse", 699.0, 4.4, 1260,
             "https://img.shop.local/p1009.jpg", {"续航": "14天", "防水": "5ATM"},
             [("BLACK", {"颜色": "曜石黑"}, 699.0, 39),
              ("PINK", {"颜色": "樱粉"}, 699.0, 14)]),
            ("P1010", "影巡 2K 家用摄像头", "云台旋转，夜视增强，哭声/异响提醒。",
             "数码3C", "HomeEye", 259.0, 4.3, 980,
             "https://img.shop.local/p1010.jpg", {"清晰度": "2K", "存储": "MicroSD/云存储"},
             [("STD", {"颜色": "白色"}, 259.0, 52)]),
            ("P3003", "静音人体工学办公椅 Pro", "自适应腰托、4D 扶手、网布透气。",
             "家居日用", "ErgoNest", 1299.0, 4.7, 1380,
             "https://img.shop.local/p3003.jpg", {"承重": "150kg", "材质": "网布"},
             [("BLACK", {"颜色": "黑色"}, 1299.0, 25),
              ("GRAY", {"颜色": "浅灰"}, 1299.0, 8)]),
            ("P3004", "透明抽屉式收纳箱 3只装", "可叠放设计，适合衣柜、玩具和杂物收纳。",
             "家居日用", "Orderly", 119.0, 4.6, 3900,
             "https://img.shop.local/p3004.jpg", {"容量": "18L/只", "数量": "3只装"},
             [("CLEAR", {"颜色": "透明"}, 119.0, 96)]),
            ("P3005", "云柔抗菌四件套", "60支长绒棉，抗菌整理，亲肤透气。",
             "家居日用", "SleepWell", 369.0, 4.7, 2500,
             "https://img.shop.local/p3005.jpg", {"材质": "长绒棉", "尺寸": "1.5/1.8m床"},
             [("BLUE-150", {"颜色": "雾蓝", "尺寸": "1.5m"}, 369.0, 28),
              ("GREEN-180", {"颜色": "松绿", "尺寸": "1.8m"}, 399.0, 18)]),
            ("P3006", "米白陶瓷不粘煎锅 28cm", "加厚锅底，少油烟，电磁炉燃气通用。",
             "家居日用", "CookMate", 159.0, 4.5, 3200,
             "https://img.shop.local/p3006.jpg", {"直径": "28cm", "适用": "电磁炉/燃气"},
             [("STD", {"颜色": "米白"}, 159.0, 73)]),
            ("P3007", "恒温电热水壶 1.7L", "316不锈钢内胆，五档温控，自动断电。",
             "家居日用", "WarmCup", 189.0, 4.6, 2100,
             "https://img.shop.local/p3007.jpg", {"容量": "1.7L", "内胆": "316不锈钢"},
             [("WHITE", {"颜色": "白色"}, 189.0, 64)]),
            ("P3008", "轻音无线吸尘器 V6", "绿光显尘，双电机地刷，60分钟续航。",
             "家居日用", "DustFree", 899.0, 4.5, 1160,
             "https://img.shop.local/p3008.jpg", {"续航": "60分钟", "重量": "1.5kg"},
             [("STD", {"版本": "标准版"}, 899.0, 19)]),
            ("P3009", "低噪空气净化器 A5", "适用 45 平方米，甲醛/PM2.5 双传感。",
             "家居日用", "AirLeaf", 1199.0, 4.6, 790,
             "https://img.shop.local/p3009.jpg", {"适用面积": "45㎡", "滤芯": "H13 HEPA"},
             [("STD", {"颜色": "白色"}, 1199.0, 16)]),
            ("P3010", "浴室速干毛巾 4条装", "新疆棉，A类标准，吸水速干不掉毛。",
             "家居日用", "SoftHome", 79.0, 4.5, 6400,
             "https://img.shop.local/p3010.jpg", {"材质": "纯棉", "数量": "4条"},
             [("MIX", {"颜色": "混色"}, 79.0, 220)]),
            ("P2003", "通勤防泼水双肩包 22L", "独立电脑仓，背负减压，适合通勤和短途旅行。",
             "服饰鞋包", "UrbanTrail", 259.0, 4.6, 1880,
             "https://img.shop.local/p2003.jpg", {"容量": "22L", "电脑仓": "15.6英寸"},
             [("BLACK", {"颜色": "黑色"}, 259.0, 42),
              ("KHAKI", {"颜色": "卡其"}, 259.0, 17)]),
            ("P2004", "云弹缓震跑步鞋", "轻量回弹中底，适合日常慢跑和健走。",
             "服饰鞋包", "FleetRun", 399.0, 4.6, 2750,
             "https://img.shop.local/p2004.jpg", {"鞋面": "工程网布", "场景": "慢跑"},
             [("BLK-42", {"颜色": "黑白", "尺码": "42"}, 399.0, 31),
              ("WHT-38", {"颜色": "米白", "尺码": "38"}, 399.0, 23)]),
            ("P2005", "抗皱商务衬衫", "易打理面料，修身剪裁，通勤不易皱。",
             "服饰鞋包", "DailyFit", 169.0, 4.4, 1980,
             "https://img.shop.local/p2005.jpg", {"材质": "棉混纺", "版型": "修身"},
             [("WHITE-M", {"颜色": "白色", "尺码": "M"}, 169.0, 44),
              ("BLUE-L", {"颜色": "浅蓝", "尺码": "L"}, 169.0, 27)]),
            ("P2006", "24寸轻量拉杆箱", "PC材质，静音万向轮，干湿分区。",
             "服饰鞋包", "GoCase", 399.0, 4.5, 1640,
             "https://img.shop.local/p2006.jpg", {"尺寸": "24寸", "材质": "PC"},
             [("SILVER", {"颜色": "银灰"}, 399.0, 21),
              ("GREEN", {"颜色": "薄荷绿"}, 399.0, 12)]),
            ("P2007", "高腰速干瑜伽裤", "裸感面料，四面弹力，高强度训练不勒腰。",
             "服饰鞋包", "Flexi", 129.0, 4.5, 3600,
             "https://img.shop.local/p2007.jpg", {"材质": "锦氨混纺", "长度": "九分"},
             [("BLACK-S", {"颜色": "黑色", "尺码": "S"}, 129.0, 52),
              ("GRAY-M", {"颜色": "雾灰", "尺码": "M"}, 129.0, 38)]),
            ("P2008", "德绒保暖内衣套装", "轻薄锁温，亲肤不起球，秋冬打底。",
             "服饰鞋包", "WarmFit", 199.0, 4.6, 4200,
             "https://img.shop.local/p2008.jpg", {"材质": "德绒", "厚度": "中厚"},
             [("M", {"颜色": "深灰", "尺码": "M"}, 199.0, 66),
              ("XL", {"颜色": "深灰", "尺码": "XL"}, 199.0, 29)]),
            ("P4003", "清透防晒乳 SPF50+ 50ml", "通勤户外可用，成膜快，不泛白。",
             "美妆个护", "Sunly", 129.0, 4.6, 5200,
             "https://img.shop.local/p4003.jpg", {"SPF": "50+", "规格": "50ml"},
             [("STD", {"规格": "50ml"}, 129.0, 140)]),
            ("P4004", "负离子高速吹风机", "11万转高速电机，恒温护发，低噪。",
             "美妆个护", "AeroDry", 499.0, 4.7, 1800,
             "https://img.shop.local/p4004.jpg", {"电机": "11万转", "风嘴": "2个"},
             [("WHITE", {"颜色": "珍珠白"}, 499.0, 34),
              ("GRAY", {"颜色": "钛灰"}, 499.0, 11)]),
            ("P4005", "玻尿酸补水面膜 20片", "三重玻尿酸，轻薄膜布，晒后补水。",
             "美妆个护", "Aqua", 99.0, 4.5, 6800,
             "https://img.shop.local/p4005.jpg", {"功效": "补水", "数量": "20片"},
             [("BOX", {"规格": "20片/盒"}, 99.0, 180)]),
            ("P4006", "声波电动牙刷 T5", "五档模式，压力提醒，IPX7 防水。",
             "美妆个护", "SmilePro", 199.0, 4.6, 3100,
             "https://img.shop.local/p4006.jpg", {"模式": "5档", "续航": "45天"},
             [("BLUE", {"颜色": "海盐蓝"}, 199.0, 57),
              ("PINK", {"颜色": "樱花粉"}, 199.0, 46)]),
            ("P4007", "三刀头电动剃须刀", "浮动刀头，Type-C 充电，全身水洗。",
             "美妆个护", "SharpEase", 269.0, 4.4, 1450,
             "https://img.shop.local/p4007.jpg", {"刀头": "三刀头", "防水": "IPX7"},
             [("STD", {"颜色": "黑色"}, 269.0, 38)]),
            ("P5001", "柔薄拉拉裤 L 码 76片", "弱酸亲肤表层，夜用大吸量。",
             "母婴宠物", "BabySoft", 159.0, 4.7, 6200,
             "https://img.shop.local/p5001.jpg", {"尺码": "L", "数量": "76片"},
             [("L76", {"尺码": "L", "数量": "76片"}, 159.0, 115)]),
            ("P5002", "婴儿恒温调奶器 1.2L", "24小时恒温，除氯沸腾，一键冲奶。",
             "母婴宠物", "BabyCare", 239.0, 4.6, 1300,
             "https://img.shop.local/p5002.jpg", {"容量": "1.2L", "温控": "40-90℃"},
             [("STD", {"颜色": "白色"}, 239.0, 26)]),
            ("P5003", "可折叠轻便婴儿推车", "一键收车，可登机，五点式安全带。",
             "母婴宠物", "TinyTrip", 899.0, 4.5, 760,
             "https://img.shop.local/p5003.jpg", {"重量": "6.2kg", "适龄": "6-36个月"},
             [("GRAY", {"颜色": "石墨灰"}, 899.0, 13)]),
            ("P5004", "PPSU 宽口奶瓶 240ml", "耐摔耐高温，防胀气奶嘴。",
             "母婴宠物", "BabyCare", 89.0, 4.6, 2800,
             "https://img.shop.local/p5004.jpg", {"材质": "PPSU", "容量": "240ml"},
             [("STD", {"规格": "240ml"}, 89.0, 86)]),
            ("P5005", "成猫全价猫粮 5kg", "鸡肉鱼肉配方，添加益生元。",
             "母婴宠物", "PawMeal", 189.0, 4.5, 3400,
             "https://img.shop.local/p5005.jpg", {"规格": "5kg", "适用": "成猫"},
             [("CHICKEN", {"口味": "鸡肉鱼肉"}, 189.0, 58)]),
            ("P5006", "豆腐猫砂 6L 4包", "低尘可冲厕，绿茶除味。",
             "母婴宠物", "CleanPaw", 99.0, 4.4, 4100,
             "https://img.shop.local/p5006.jpg", {"规格": "6Lx4", "香型": "绿茶"},
             [("GREEN", {"香型": "绿茶"}, 99.0, 102)]),
            ("P5007", "宠物自动饮水机 2.5L", "循环活水，静音水泵，三重过滤。",
             "母婴宠物", "PawHome", 149.0, 4.5, 1720,
             "https://img.shop.local/p5007.jpg", {"容量": "2.5L", "供电": "USB"},
             [("WHITE", {"颜色": "白色"}, 149.0, 45)]),
            ("P6001", "意式拼配咖啡豆 500g", "中深烘，坚果巧克力风味，适合美式和拿铁。",
             "食品饮料", "BeanTalk", 89.0, 4.6, 5300,
             "https://img.shop.local/p6001.jpg", {"烘焙": "中深烘", "规格": "500g"},
             [("500G", {"规格": "500g"}, 89.0, 76)]),
            ("P6002", "每日坚果 30包", "核桃、腰果、扁桃仁与果干独立小包装。",
             "食品饮料", "NutriDay", 129.0, 4.7, 8800,
             "https://img.shop.local/p6002.jpg", {"数量": "30包", "净含量": "750g"},
             [("BOX", {"规格": "30包"}, 129.0, 95)]),
            ("P6003", "低糖燕麦脆 1kg", "添加冻干草莓和坚果，早餐冲泡即食。",
             "食品饮料", "GrainUp", 59.0, 4.4, 2950,
             "https://img.shop.local/p6003.jpg", {"规格": "1kg", "糖分": "低糖"},
             [("STD", {"口味": "草莓坚果"}, 59.0, 120)]),
            ("P6004", "无糖乌龙茶 12瓶", "原叶萃取，0糖0脂，冷藏口感更佳。",
             "食品饮料", "TeaFlow", 49.0, 4.5, 6200,
             "https://img.shop.local/p6004.jpg", {"规格": "500ml x 12", "糖分": "无糖"},
             [("CASE", {"规格": "12瓶"}, 49.0, 160)]),
            ("P7001", "加厚防潮露营垫", "双面铝膜，蛋巢结构，帐篷内外可用。",
             "运动户外", "CampGo", 79.0, 4.4, 2180,
             "https://img.shop.local/p7001.jpg", {"尺寸": "190x60cm", "厚度": "2cm"},
             [("GREEN", {"颜色": "军绿"}, 79.0, 74)]),
            ("P7002", "全自动速开帐篷 3-4人", "一压成型，防泼水外帐，带门厅。",
             "运动户外", "CampGo", 499.0, 4.5, 980,
             "https://img.shop.local/p7002.jpg", {"人数": "3-4人", "防水": "PU2000"},
             [("KHAKI", {"颜色": "卡其"}, 499.0, 19)]),
            ("P7003", "可调节哑铃 10kg 一对", "旋钮调节重量，家用力量训练。",
             "运动户外", "FitLab", 299.0, 4.5, 1320,
             "https://img.shop.local/p7003.jpg", {"重量": "10kgx2", "调节": "5档"},
             [("PAIR", {"规格": "一对"}, 299.0, 25)]),
            ("P7004", "防滑 TPE 瑜伽垫 6mm", "双面防滑，回弹柔软，附绑带。",
             "运动户外", "Flexi", 89.0, 4.6, 5300,
             "https://img.shop.local/p7004.jpg", {"厚度": "6mm", "材质": "TPE"},
             [("PURPLE", {"颜色": "雾紫"}, 89.0, 88),
              ("GREEN", {"颜色": "豆绿"}, 89.0, 71)]),
            ("P7005", "一体成型骑行头盔", "轻量通风，后脑旋钮调节，夜间反光。",
             "运动户外", "RideSafe", 159.0, 4.4, 960,
             "https://img.shop.local/p7005.jpg", {"尺码": "M/L", "重量": "260g"},
             [("M", {"颜色": "白色", "尺码": "M"}, 159.0, 21),
              ("L", {"颜色": "黑色", "尺码": "L"}, 159.0, 17)]),
            ("P8001", "点阵活页笔记本 A5", "180度平摊，100g 道林纸，适合手账和会议记录。",
             "图书文具", "NoteWell", 39.0, 4.6, 7400,
             "https://img.shop.local/p8001.jpg", {"规格": "A5", "页数": "160页"},
             [("DOT", {"内页": "点阵"}, 39.0, 180)]),
            ("P8002", "0.5mm 速干中性笔 12支", "顺滑不洇墨，学生和办公常用。",
             "图书文具", "WritePro", 24.9, 4.5, 9600,
             "https://img.shop.local/p8002.jpg", {"笔尖": "0.5mm", "数量": "12支"},
             [("BLACK", {"颜色": "黑色"}, 24.9, 260),
              ("BLUE", {"颜色": "蓝色"}, 24.9, 90)]),
            ("P8003", "人体工学护腕鼠标垫", "凝胶护腕，锁边耐磨，适合长期办公。",
             "图书文具", "DeskMate", 49.0, 4.4, 1850,
             "https://img.shop.local/p8003.jpg", {"尺寸": "25x23cm", "材质": "布面+凝胶"},
             [("GRAY", {"颜色": "灰色"}, 49.0, 75)]),
            ("P8004", "双层桌面文件架", "金属喷涂，文件、书本、平板分类收纳。",
             "图书文具", "DeskMate", 69.0, 4.5, 1600,
             "https://img.shop.local/p8004.jpg", {"层数": "双层", "材质": "金属"},
             [("WHITE", {"颜色": "白色"}, 69.0, 54)]),
        ])

        with self._connect() as conn:
            if reset:
                self._clear_demo_data(conn)
            for (pid, title, desc, cat, brand, price, rating, count, img, attrs, skus) in products:
                conn.execute(
                    """INSERT INTO products(product_id, title, description, category,
                       brand, price, currency, rating, rating_count, image_url,
                       attributes_json, status, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, title, desc, cat, brand, price, cny, rating, count, img,
                     json.dumps(attrs, ensure_ascii=False), "active", now),
                )
                for suffix, sku_attrs, sku_price, stock in skus:
                    conn.execute(
                        """INSERT INTO skus(sku_id, product_id, sku_code, attributes_json,
                           price, stock, status) VALUES (?,?,?,?,?,?,?)""",
                        (f"{pid}-{suffix}", pid, f"{pid}-{suffix}",
                         json.dumps(sku_attrs, ensure_ascii=False), sku_price, stock, "active"),
                    )

            # Seed customers. customer_id is intended to equal the PAHF person_id /
            # chat user_id so memory and orders line up for the same person.
            customer_password_hash = self._hash_password(DEFAULT_CUSTOMER_PASSWORD)
            customers = [
                ("c9001", "林小满", "linxiaoman@shop.local", "13800000000", "gold", customer_password_hash, now - 200 * day),
                ("u1001", "李雷", "lilei@shop.local", "13900000001", "silver", customer_password_hash, now - 120 * day),
                ("u1002", "韩梅梅", "hanmeimei@shop.local", "13900000002", "gold", customer_password_hash, now - 90 * day),
            ]
            for cid, name, email, phone, tier, password_hash, created in customers:
                conn.execute(
                    """INSERT INTO customers(customer_id, name, email, phone, tier, password_hash, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (cid, name, email, phone, tier, password_hash, created),
                )

            # Orders + items + shipments.
            orders = [
                # order_id, customer, status, created_offset_days, address, method, items, shipment
                ("SO20260012", "c9001", "shipped", 3, "上海市浦东新区世纪大道100号", "顺丰标快",
                 [("P1002-WHT", "P1002", "声波 X 主动降噪耳机", 1, 899.0)],
                 ("SF", "SF2026070301", "in_transit",
                  [("已揽收", 3), ("到达上海转运中心", 2), ("运输中", 1)])),
                ("SO20260027", "c9001", "delivered", 12, "上海市浦东新区世纪大道100号", "京东物流",
                 [("P4001-STD", "P4001", "水光保湿精华 30ml", 2, 239.0)],
                 ("JD", "JD9988776655", "delivered",
                  [("已揽收", 12), ("运输中", 11), ("已签收", 10)])),
                ("SO20260041", "c9001", "pending_payment", 0, "上海市浦东新区世纪大道100号", "顺丰标快",
                 [("P2001-BLK-M", "P2001", "云感纯棉圆领卫衣", 1, 199.0)],
                 None),
                ("SO20260050", "u1001", "shipped", 2, "北京市海淀区中关村大街1号", "中通快递",
                 [("P3001-BLK", "P3001", "静音人体工学办公椅", 1, 1099.0)],
                 ("ZTO", "ZT5566778899", "in_transit",
                  [("已揽收", 2), ("运输中", 1)])),
                ("SO20260068", "u1002", "delivered", 6, "杭州市西湖区文三路88号", "京东物流",
                 [("P5001-L76", "P5001", "柔薄拉拉裤 L 码 76片", 2, 159.0),
                  ("P5004-STD", "P5004", "PPSU 宽口奶瓶 240ml", 1, 89.0)],
                 ("JD", "JD2026070201", "delivered",
                  [("已揽收", 6), ("到达杭州分拨中心", 5), ("派送中", 4), ("已签收", 4)])),
                ("SO20260073", "c9001", "delivered", 18, "上海市浦东新区世纪大道100号", "顺丰标快",
                 [("P2004-BLK-42", "P2004", "云弹缓震跑步鞋", 1, 399.0),
                  ("P7004-GREEN", "P7004", "防滑 TPE 瑜伽垫 6mm", 1, 89.0)],
                 ("SF", "SF2026070202", "delivered",
                  [("已揽收", 18), ("运输中", 17), ("已签收", 16)])),
                ("SO20260088", "u1001", "pending_payment", 0, "北京市海淀区中关村大街1号", "中通快递",
                 [("P1005-STD", "P1005", "清风 27 英寸 4K 显示器", 1, 1699.0),
                  ("P8003-GRAY", "P8003", "人体工学护腕鼠标垫", 1, 49.0)],
                 None),
            ]
            for (oid, cid, status, off, addr, method, items, shipment) in orders:
                total = round(sum(p * q for (_s, _p, _t, q, p) in items), 2)
                created = now - off * day
                paid = None if status == "pending_payment" else created + 600
                conn.execute(
                    """INSERT INTO orders(order_id, customer_id, status, total, currency,
                       shipping_address, shipping_method, created_at, paid_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (oid, cid, status, total, cny, addr, method, created, paid),
                )
                for sku_code, prod_id, title_snap, qty, unit in items:
                    conn.execute(
                        """INSERT INTO order_items(order_id, sku_code, product_id,
                           title_snapshot, qty, unit_price) VALUES (?,?,?,?,?,?)""",
                        (oid, sku_code, prod_id, title_snap, qty, unit),
                    )
                if shipment is not None:
                    carrier, tracking, ship_status, evts = shipment
                    events = [
                        {"time": now - e_off * day, "desc": desc}
                        for desc, e_off in evts
                    ]
                    conn.execute(
                        """INSERT INTO shipments(shipment_id, order_id, carrier,
                           tracking_no, status, events_json, updated_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (f"SH-{oid}", oid, carrier, tracking, ship_status,
                         json.dumps(events, ensure_ascii=False), now),
                    )

            # Coupons.
            coupons = [
                ("WELCOME20", "fixed", 20.0, 100.0, "新人专享：满100减20", now - 10 * day, now + 60 * day),
                ("SAVE10PCT", "percent", 10.0, 300.0, "满300享9折", now - 5 * day, now + 30 * day),
                ("VIP50", "fixed", 50.0, 500.0, "会员满500减50", now - 5 * day, now + 30 * day),
            ]
            for code, kind, value, min_spend, desc, vfrom, vto in coupons:
                conn.execute(
                    """INSERT INTO coupons(code, kind, value, min_spend, description,
                       valid_from, valid_to, active) VALUES (?,?,?,?,?,?,?,1)""",
                    (code, kind, value, min_spend, desc, vfrom, vto),
                )
            conn.execute(
                """INSERT INTO catalog_meta(key, value) VALUES ('demo_catalog_version', ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (DEMO_CATALOG_VERSION,),
            )
            conn.commit()
