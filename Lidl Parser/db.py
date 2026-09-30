"""SQLite schema + helpers for Lidl Parser DB upgrade."""
import os
import sqlite3
import unicodedata
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("LIDL_DB") or os.path.join(BASE_DIR, "data", "lidl.db")

UNIT_SEED = ["Τεμάχιο", "Κιλό", "Γραμμάριο", "Λίτρο", "ml", "Πακέτο", "Κουτί", "Φιάλη"]


def grnorm(s):
    """Greek-insensitive compare key: strip accents, casefold. Α=α=ά, ι=ί=ϊ=ΐ."""
    if s is None:
        return ""
    bare = "".join(c for c in unicodedata.normalize("NFD", str(s)) if unicodedata.category(c) != "Mn")
    return bare.casefold()

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
    card_last4 TEXT,
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
CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grocy_id INTEGER UNIQUE,
    name TEXT UNIQUE NOT NULL,
    source TEXT DEFAULT 'local',
    cached_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grocy_id INTEGER UNIQUE,
    name TEXT UNIQUE NOT NULL,
    source TEXT DEFAULT 'local',
    cached_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS import_staging (
    raw TEXT PRIMARY KEY,
    clean TEXT DEFAULT '',
    category TEXT DEFAULT 'Misc',
    barcode TEXT DEFAULT '',
    brand TEXT DEFAULT '',
    unit TEXT DEFAULT '',
    weight TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    grocy_track INTEGER DEFAULT 1,
    source_file TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS import_warnings (
    barcode TEXT NOT NULL,
    local_raw TEXT NOT NULL,
    incoming_raw TEXT NOT NULL,
    source_file TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS card_map (
    last4 TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS item_splits (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    share REAL NOT NULL CHECK (share > 0 AND share <= 1),
    PRIMARY KEY (item_id, user_id)
);
"""


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("grnorm", 1, grnorm)
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    for col, ddl in (
        ("grocy_id", "ALTER TABLE products ADD COLUMN grocy_id INTEGER"),
        ("grocy_name", "ALTER TABLE products ADD COLUMN grocy_name TEXT"),
        ("grocy_synced_at", "ALTER TABLE products ADD COLUMN grocy_synced_at TEXT"),
        ("grocy_track", "ALTER TABLE products ADD COLUMN grocy_track INTEGER DEFAULT 1"),
        ("purchase_qu_id", "ALTER TABLE products ADD COLUMN purchase_qu_id INTEGER"),
        ("qu_factor", "ALTER TABLE products ADD COLUMN qu_factor REAL"),
        ("market", "ALTER TABLE products ADD COLUMN market TEXT DEFAULT 'Lidl'"),
        ("needs_review", "ALTER TABLE products ADD COLUMN needs_review INTEGER DEFAULT 0"),
    ):
        have = [r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
        if col not in have:
            conn.execute(ddl)
    receipts_have = [r[1] for r in conn.execute("PRAGMA table_info(receipts)").fetchall()]
    if "card_last4" not in receipts_have:
        conn.execute("ALTER TABLE receipts ADD COLUMN card_last4 TEXT")
    users_have = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "active" not in users_have:
        conn.execute("ALTER TABLE users ADD COLUMN active INTEGER DEFAULT 1")
    if "mark" not in receipts_have:
        conn.execute("ALTER TABLE receipts ADD COLUMN mark TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_receipts_mark ON receipts(mark)")
    for seed in UNIT_SEED:
        conn.execute("INSERT OR IGNORE INTO units (name, source) VALUES (?, 'local')", (seed,))
    # Backfill stub products for item raws that predate stub creation
    # (e.g. receipts imported via /upload, which never wrote products rows).
    # Idempotent: INSERT OR IGNORE, so re-runs are safe.
    conn.execute("""INSERT OR IGNORE INTO products (raw, clean, category, needs_review)
                    SELECT DISTINCT i.raw, COALESCE(NULLIF(i.clean,''), i.raw),
                           COALESCE(NULLIF(i.category,''), 'Misc'), 1
                    FROM items i WHERE i.raw NOT IN (SELECT raw FROM products)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_grocy ON products(grocy_id)")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"DB ready: {DB_PATH}")
