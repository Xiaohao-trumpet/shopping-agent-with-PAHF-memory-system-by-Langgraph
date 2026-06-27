"""SQLite-backed virtual store: products, SKUs, customers, orders, shipments,
coupons and after-sales returns.

This is the business data layer that turns the assistant into a real shopping
customer-service agent. It follows the same lightweight, local-first design as
``TicketStore`` (single SQLite file, ``sqlite3.Row`` access, no ORM).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


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
        if auto_seed and self.is_empty():
            self.seed_demo_data()

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
                """
            )
            conn.commit()

    def is_empty(self) -> bool:
        with self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) AS c FROM products")
            return int(cur.fetchone()["c"]) == 0

    def close(self) -> None:  # parity with other stores; connections are per-call
        return None

    # ------------------------------------------------------------------ rows
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
            "image_url": row["image_url"],
            "attributes": json.loads(row["attributes_json"] or "{}"),
            "status": row["status"],
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
    def seed_demo_data(self) -> None:
        """Populate a small but realistic virtual shop. Idempotent via is_empty()."""
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
             "服饰", "Cl0udy", 199.0, 4.5, 3200,
             "https://img.shop.local/p2001.jpg", {"材质": "纯棉", "版型": "宽松"},
             [("GRY-M", {"颜色": "花灰", "尺码": "M"}, 199.0, 80),
              ("GRY-L", {"颜色": "花灰", "尺码": "L"}, 199.0, 0),
              ("BLK-M", {"颜色": "黑色", "尺码": "M"}, 199.0, 45)]),
            ("P2002", "全天候轻量冲锋衣", "三层压胶防水，可拆卸抓绒内胆。",
             "服饰", "Summit", 599.0, 4.7, 760,
             "https://img.shop.local/p2002.jpg", {"防水": "10000mm", "场景": "户外"},
             [("GRN-L", {"颜色": "松林绿", "尺码": "L"}, 599.0, 20),
              ("BLK-XL", {"颜色": "黑色", "尺码": "XL"}, 599.0, 7)]),
            ("P3001", "静音人体工学办公椅", "网布透气，3D 扶手，腰托支撑。",
             "家居", "ErgoNest", 1099.0, 4.6, 1130,
             "https://img.shop.local/p3001.jpg", {"材质": "网布", "承重": "150kg"},
             [("BLK", {"颜色": "黑色"}, 1099.0, 25),
              ("GRY", {"颜色": "浅灰"}, 1099.0, 4)]),
            ("P3002", "暖光护眼台灯", "无频闪，五档色温，Type-C 充电。",
             "家居", "Glowy", 159.0, 4.4, 2050,
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

        with self._connect() as conn:
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

            # Demo customers. customer_id is intended to equal the PAHF person_id /
            # chat user_id so memory and orders line up for the same person.
            customers = [
                ("demo-user", "演示用户", "demo@shop.local", "13800000000", "gold", now - 200 * day),
                ("u1001", "李雷", "lilei@shop.local", "13900000001", "silver", now - 120 * day),
            ]
            for cid, name, email, phone, tier, created in customers:
                conn.execute(
                    """INSERT INTO customers(customer_id, name, email, phone, tier, created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (cid, name, email, phone, tier, created),
                )

            # Orders + items + shipments.
            orders = [
                # order_id, customer, status, created_offset_days, address, method, items, shipment
                ("SO20260012", "demo-user", "shipped", 3, "上海市浦东新区世纪大道100号", "顺丰标快",
                 [("P1002-WHT", "P1002", "声波 X 主动降噪耳机", 1, 899.0)],
                 ("SF", "SF1234567890", "in_transit",
                  [("已揽收", 3), ("到达上海转运中心", 2), ("运输中", 1)])),
                ("SO20260027", "demo-user", "delivered", 12, "上海市浦东新区世纪大道100号", "京东物流",
                 [("P4001-STD", "P4001", "水光保湿精华 30ml", 2, 239.0)],
                 ("JD", "JD9988776655", "delivered",
                  [("已揽收", 12), ("运输中", 11), ("已签收", 10)])),
                ("SO20260041", "demo-user", "pending_payment", 0, "上海市浦东新区世纪大道100号", "顺丰标快",
                 [("P2001-BLK-M", "P2001", "云感纯棉圆领卫衣", 1, 199.0)],
                 None),
                ("SO20260050", "u1001", "shipped", 2, "北京市海淀区中关村大街1号", "中通快递",
                 [("P3001-BLK", "P3001", "静音人体工学办公椅", 1, 1099.0)],
                 ("ZTO", "ZT5566778899", "in_transit",
                  [("已揽收", 2), ("运输中", 1)])),
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
            conn.commit()
