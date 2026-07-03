-- SQLite schema for 25组-电商售后客服与用户评价分析系统.
-- The application still creates tables automatically on startup; this file is
-- provided for review, handover, and manual database inspection.

CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    brand TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    rating REAL NOT NULL,
    rating_count INTEGER NOT NULL,
    image_url TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS skus (
    sku_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    sku_code TEXT NOT NULL UNIQUE,
    attributes_json TEXT NOT NULL,
    price REAL NOT NULL,
    stock INTEGER NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    tier TEXT NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    status TEXT NOT NULL,
    total REAL NOT NULL,
    currency TEXT NOT NULL,
    shipping_address TEXT NOT NULL,
    shipping_method TEXT NOT NULL,
    created_at REAL NOT NULL,
    paid_at REAL
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    sku_code TEXT NOT NULL,
    product_id TEXT NOT NULL,
    title_snapshot TEXT NOT NULL,
    qty INTEGER NOT NULL,
    unit_price REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    carrier TEXT NOT NULL,
    tracking_no TEXT NOT NULL,
    status TEXT NOT NULL,
    events_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS coupons (
    code TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    value REAL NOT NULL,
    min_spend REAL NOT NULL,
    description TEXT NOT NULL,
    valid_from REAL NOT NULL,
    valid_to REAL NOT NULL,
    active INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS return_requests (
    return_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    sku_code TEXT,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    refund_amount REAL NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    status TEXT NOT NULL,
    assigned_agent_id TEXT,
    assigned_agent_name TEXT,
    summary TEXT NOT NULL DEFAULT '',
    csat INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    sender_type TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS message_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    customer_id TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    stars INTEGER NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    comment TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_login_at REAL
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS product_reviews (
    review_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    rating INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    aspects_json TEXT NOT NULL DEFAULT '[]',
    sentiment TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    helpful INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL
);
