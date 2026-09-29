import re
import sqlite3
import subprocess, os, sys
def test_paste_cli(tmp_path):
    src = os.path.join(os.path.dirname(__file__), "data_paste.txt")
    assert os.path.exists(src), "fixture missing"
    base = os.path.join(os.path.dirname(__file__), "..")
    name = ""
    env = dict(os.environ, LIDL_DB=str(tmp_path / "cli.db"))
    try:
        r = subprocess.run([sys.executable, "fetch_lidl.py", "--paste", src, "--tid", "CLITEST"],
                           cwd=base, env=env,
                           capture_output=True, text=True, timeout=120)
        assert "saved 26-09-22" in r.stdout, r.stdout + r.stderr
        # Parse the created filename from stdout — never hardcode or glob it.
        m = re.search(r"saved (\S+)", r.stdout)
        name = m.group(1) if m else ""
        # Refuse anything but a sequenced 26-09-22 file (never touch 26-09-22.txt).
        assert re.fullmatch(r"26-09-22\.\d+\.txt", name), f"unexpected saved name: {name!r}"
    finally:
        # Self-cleanup: remove the receipt file + DB rows created above.
        if name and re.fullmatch(r"26-09-22\.\d+\.txt", name):
            fpath = os.path.join(base, "receipts", name)
            if os.path.exists(fpath):
                os.remove(fpath)
        db_path = str(tmp_path / "cli.db")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            if name and re.fullmatch(r"26-09-22\.\d+\.txt", name):
                row = conn.execute("SELECT id FROM receipts WHERE filename=?", (name,)).fetchone()
                if row is not None:
                    conn.execute("DELETE FROM items WHERE receipt_id=?", (row[0],))
                    conn.execute("DELETE FROM receipts WHERE id=?", (row[0],))
            conn.execute("DELETE FROM fetch_state WHERE t_id='CLITEST'")
            conn.commit()
        finally:
            conn.close()
    conn = sqlite3.connect(str(tmp_path / "cli.db"))
    try:
        leftovers = conn.execute("SELECT * FROM fetch_state WHERE t_id='CLITEST'").fetchall()
    finally:
        conn.close()
    assert leftovers == [], f"CLITEST fetch_state not cleaned: {leftovers}"
