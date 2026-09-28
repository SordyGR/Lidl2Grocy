"""DB-backed settings with env fallback. UI value wins, env seeds."""
import os
import db as dbmod

KNOWN_KEYS = {"LIDL_USER", "LIDL_PASS", "GROCY_URL", "GROCY_API_KEY", "EXT_INGEST_TOKEN",
              "EXCLUDED_CATEGORIES"}


def get(key, default=""):
    if key not in KNOWN_KEYS:
        return default
    try:
        conn = dbmod.get_db()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
    except Exception:
        row = None
    if row is not None and (row["value"] or "") != "":
        return row["value"]
    return os.environ.get(key, default)


def set(key, value):
    if key not in KNOWN_KEYS:
        return
    conn = dbmod.get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                 (key, value or ""))
    conn.commit()
    conn.close()
