# Lidl Parser/tests/test_fetch_import.py
import os
import sqlite3
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, get_db
import db as dbmod
from fetch_lib import import_kept_receipt, pick_filename

KEPT = (
    "22 \u03a3\u03b5\u03c0 2026\nlogo\n"
    "TORTI.CHIPS SALT200G          2,78  13.00%\n"
    "    2 x          1,39      \n"
    "     \u03ad\u03ba\u03c0\u03c4\u03c9\u03c3\u03b7 Lidl Plus             -0,10\n"
    "     \u03ad\u03ba\u03c0\u03c4\u03c9\u03c3\u03b7                       -0,06\n"
    "LIMES                         1,15  13.00%\n"
    "  0,304 kg x 3,79   EUR/kg\n"
    "\u03a3\u03a5\u039d\u039f\u039b\u039f                               3,77\n"
    "0000   000000/00/00       01.01.26   12:00\n"
    "\u0395\u03a0\u0399\u03a3\u03a4\u03a1\u039f\u03a6\u0395\u03a3 \u0394\u0395\u039a\u03a4\u0395\u03a3\n"
)

def _mem_conn(tmp_path):
    dbmod.DB_PATH = str(tmp_path / "t.db")
    init_db()
    return get_db()

def test_import_and_idempotent_rerun(tmp_path):
    conn = _mem_conn(tmp_path)
    r1 = import_kept_receipt(conn, KEPT, "26-09-22.txt", "T1")
    assert r1["items"] == 2 and abs(r1["total"] - 3.77) < 0.005
    # stacked discounts (0.10 + 0.06) accumulate on the first item
    disc = conn.execute("SELECT discount FROM items WHERE raw LIKE 'TORTI%'").fetchone()[0]
    assert abs(disc - 0.16) < 0.005
    r2 = import_kept_receipt(conn, KEPT, "26-09-22.txt", "T1")
    assert r2["items"] == 2
    n = conn.execute("SELECT COUNT(*) FROM receipts WHERE filename='26-09-22.txt'").fetchone()[0]
    assert n == 1
    assert conn.execute("SELECT COUNT(*) FROM fetch_state WHERE t_id='T1'").fetchone()[0] == 1
    conn.close()

def test_pick_filename_avoids_collision(tmp_path):
    conn = _mem_conn(tmp_path)
    d = str(tmp_path / "rc")
    os.makedirs(d)
    open(os.path.join(d, "26-09-22.txt"), "w").close()
    assert pick_filename(d, conn, "26", "09", "22") == "26-09-22.1.txt"
    conn.close()
