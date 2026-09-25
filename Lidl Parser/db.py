"""SQLite schema + helpers for Lidl Parser DB upgrade."""
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "lidl.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw TEXT UNIQUE NOT NULL,
    clean TEXT,
    category TEXT DEFAULT 'Misc',
    barcode TEXT,
    brand TEXT,
    unit TEXT,
    weight TEXT,
    notes TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    grocy_id INTEGER,
    grocy_name TEXT,
    grocy_synced_at TEXT,
    grocy_track INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT UNIQUE NOT NULL,
    receipt_date TEXT,
    receipt_seq INTEGER DEFAULT 1,
    store TEXT DEFAULT 'Lidl',
    item_count INTEGER DEFAULT 0,
    total REAL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    raw TEXT NOT NULL,
    clean TEXT,
    category TEXT,
    qty REAL DEFAULT 1,
    unit_price REAL DEFAULT 0,
    vat REAL DEFAULT 0,
    discount REAL DEFAULT 0,
    total REAL DEFAULT 0,
    final REAL DEFAULT 0,
    match_type TEXT DEFAULT 'new',
    match_score REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_clean ON items(clean);
CREATE INDEX IF NOT EXISTS idx_items_receipt ON items(receipt_id);
CREATE INDEX IF NOT EXISTS idx_receipts_date ON receipts(receipt_date);
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw TEXT,
    clean TEXT,
    category TEXT,
    date TEXT,
    price REAL,
    source TEXT DEFAULT 'legacy_csv'
);
CREATE INDEX IF NOT EXISTS idx_ph_clean ON price_history(clean);
CREATE TABLE IF NOT EXISTS fetch_state (
    t_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    fetched_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS grocy_pushes (
    receipt_id INTEGER PRIMARY KEY REFERENCES receipts(id) ON DELETE CASCADE,
    pushed_at TEXT DEFAULT (datetime('now')),
    pushed_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    errors TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
"""


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    for col, ddl in (
        ("grocy_id", "ALTER TABLE products ADD COLUMN grocy_id INTEGER"),
        ("grocy_name", "ALTER TABLE products ADD COLUMN grocy_name TEXT"),
        ("grocy_synced_at", "ALTER TABLE products ADD COLUMN grocy_synced_at TEXT"),
        ("grocy_track", "ALTER TABLE products ADD COLUMN grocy_track INTEGER DEFAULT 1"),
    ):
        have = [r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
        if col not in have:
            conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_grocy ON products(grocy_id)")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"DB ready: {DB_PATH}")
