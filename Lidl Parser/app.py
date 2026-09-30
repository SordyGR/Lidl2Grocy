"""Flask browser frontend for Lidl Parser (SQLite). Run: python app.py -> http://127.0.0.1:5000"""
import hmac
import os
import secrets
import sys
import csv
import io
import sqlite3
from datetime import date, timedelta
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session, Response
from db import init_db, get_db, grnorm
from fetch_lib import (trim_receipt_text, receipt_body_date,
                       pick_filename, import_kept_receipt)
from receipt_parser import parse_receipt, parse_receipt_date, smart_match, build_memory, extract_card_last4
import grocy as grocy_client
import settings_store as app_settings
import fetch_lidl

RECEIPT_DIR = os.path.join(BASE_DIR, "receipts")

app = Flask(__name__)
app.secret_key = "lidl-local-dev"


def _ext_token():
    tok = app_settings.get("EXT_INGEST_TOKEN")
    if not tok:
        tok = secrets.token_urlsafe(32)
        app_settings.set("EXT_INGEST_TOKEN", tok)
    return tok


def _ext_authorized():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    # Read-only: a wrong token must not mint and persist a fresh one.
    tok = app_settings.get("EXT_INGEST_TOKEN")
    if not tok:
        return False
    return hmac.compare_digest(auth[len("Bearer "):], tok)


def _excluded_categories():
    """Categories excluded from charts (Settings); empty set = everything shown."""
    import json
    try:
        vals = json.loads(app_settings.get("EXCLUDED_CATEGORIES") or "[]")
    except (ValueError, TypeError):
        return set()
    if not isinstance(vals, list):
        return set()
    return {str(v).strip() for v in vals if str(v).strip()}


def _excluded_sql(alias="COALESCE(category,'Misc')"):
    excl = sorted(_excluded_categories())
    if not excl:
        return "", []
    holders = ",".join("?" for _ in excl)
    return f" AND {alias} NOT IN ({holders})", excl


def _grocy_base():
    try:
        base_url, _ = grocy_client.get_config()
    except Exception:
        return ""
    return base_url.rstrip("/").removesuffix("/api")


def _abc_rank_map():
    try:
        xclause, xargs = _excluded_sql("COALESCE(i.category,'Misc')")
        rows = query(f"""
            SELECT i.clean AS clean, ROUND(SUM(i.final),2) AS v
            FROM items i JOIN receipts r ON r.id=i.receipt_id
            WHERE r.receipt_date IS NOT NULL{xclause}
            GROUP BY i.clean ORDER BY v DESC
        """, xargs)
    except Exception:
        return {}
    total = sum(float(r["v"] or 0) for r in rows) or 1.0
    cum, out = 0.0, {}
    for idx, r in enumerate(rows, start=1):
        cum += float(r["v"] or 0)
        pct = round(100 * cum / total, 1)
        cls = "A" if pct <= 80 else ("B" if pct <= 95 else "C")
        out[r["clean"]] = {"rank": idx, "class": cls}
    return out


@app.route("/api/ext/status")
def api_ext_status():
    if not _ext_authorized():
        return jsonify({"error": "bad or missing token"}), 401
    conn = get_db()
    try:
        last = conn.execute("SELECT MAX(fetched_at) AS at FROM fetch_state").fetchone()["at"]
        n = conn.execute("SELECT COUNT(*) AS c FROM receipts").fetchone()["c"]
    finally:
        conn.close()
    return jsonify({"ok": True, "last_sync": last, "receipts": n})


@app.after_request
def _ext_cors(resp):
    if request.path.startswith("/api/ext/"):
        resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/ext/need", methods=["POST"])
def api_ext_need():
    if not _ext_authorized():
        return jsonify({"error": "bad or missing token"}), 401
    data = request.get_json(force=True, silent=True) or {}
    tids = data.get("tids")
    if (not isinstance(tids, list) or len(tids) > 5000
            or not all(isinstance(t, str) for t in tids)):
        return jsonify({"error": "tids must be a list of at most 5000 strings"}), 400
    ordered = list(dict.fromkeys(tids))
    conn = get_db()
    try:
        have = {r[0] for r in conn.execute("SELECT t_id FROM fetch_state").fetchall()}
    finally:
        conn.close()
    return jsonify({"new": [t for t in ordered if t not in have]})


@app.route("/api/ext/receipt", methods=["POST"])
def api_ext_receipt():
    if not _ext_authorized():
        return jsonify({"error": "bad or missing token"}), 401
    data = request.get_json(force=True, silent=True) or {}
    tid = data.get("tid")
    text = data.get("text", "")
    if not tid or not isinstance(tid, str) or not isinstance(text, str) or not text.strip():
        return jsonify({"error": "tid and non-empty text required"}), 400
    conn = get_db()
    try:
        if conn.execute("SELECT 1 FROM fetch_state WHERE t_id=?", (tid,)).fetchone():
            return jsonify({"status": "duplicate", "tid": tid})
        try:
            from fetch_lib import extract_mark as _extract_mark
            mark = _extract_mark(text)
            if mark:
                hit = conn.execute("SELECT filename FROM receipts WHERE mark=?", (mark,)).fetchone()
                if hit:
                    conn.execute("INSERT OR IGNORE INTO fetch_state (t_id, filename) VALUES (?, ?)",
                                 (tid, hit["filename"]))
                    conn.commit()
                    return jsonify({"status": "duplicate", "tid": tid, "mark": mark})
            kept = trim_receipt_text(text)
            yy, mm, dd = receipt_body_date(kept)
            name = pick_filename(RECEIPT_DIR, conn, yy, mm, dd)
            res = import_kept_receipt(conn, kept, name, tid, mark=mark)
            if res.get("duplicate"):
                return jsonify({"status": "duplicate", "tid": tid, "mark": mark})
        except Exception as e:
            return jsonify({"status": "error", "tid": tid, "error": str(e)[:200]}), 422
        os.makedirs(RECEIPT_DIR, exist_ok=True)
        if mark and "MAPK:" not in kept:
            kept = kept + f"MAPK: {mark}\n"
        with open(os.path.join(RECEIPT_DIR, name), "w", encoding="utf-8") as f:
            f.write(kept)
        return jsonify({"status": "imported", "tid": tid,
                        "items": res["items"], "total": res["total"]})
    finally:
        conn.close()


@app.route("/fetch/paste", methods=["POST"])
def fetch_paste():
    import tempfile
    text = request.form.get("text", "")
    tid = (request.form.get("tid") or "").strip() or None
    if not text.strip():
        flash("No receipt text pasted")
        return redirect(url_for("dashboard"))
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        res = fetch_lidl.cmd_paste(path, tid)
    except Exception as e:
        flash(f"Paste import failed: {e}")
        return redirect(url_for("dashboard"))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    flash(f"Pasted receipt saved ({res['items']} items, {res['total']:.2f} EUR)")
    return redirect(url_for("dashboard"))


@app.context_processor
def inject_theme():
    mode = request.cookies.get("theme", "system")
    if mode not in ("light", "dark", "system"):
        mode = "system"
    return {"theme": mode}


def _safe_next(default="/"):
    """Relative-path-only redirect target (blocks absolute-URL open redirects)."""
    nxt = request.args.get("next") or ""
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return default


@app.route("/theme/<mode>")
def theme_set(mode):
    if mode not in ("light", "dark", "system"):
        return redirect(_safe_next(url_for("dashboard")))
    resp = redirect(_safe_next(url_for("dashboard")))
    resp.set_cookie("theme", mode, max_age=31536000, samesite="Lax")
    return resp


def use_v2():
    """Additive v2 skin opt-in (cookie). Old templates stay the default."""
    return request.cookies.get("ui", "v1") == "v2"


def _tpl(base):
    return base.replace(".html", "_v2.html") if use_v2() else base


@app.route("/ui/<v>")
def ui_switch(v):
    if v not in ("v1", "v2"):
        return redirect(_safe_next(url_for("dashboard")))
    resp = redirect(_safe_next(url_for("dashboard")))
    resp.set_cookie("ui", v, max_age=31536000, samesite="Lax")
    return resp


def memory_df():
    conn = get_db()
    df = pd.read_sql("SELECT raw, clean, category FROM products", conn)
    conn.close()
    return build_memory(df)


def query(sql, args=()):
    conn = get_db()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


@app.route("/")
def dashboard():
    xclause, xargs = _excluded_sql("COALESCE(category,'Misc')")
    kpis = query(f"""
        SELECT (SELECT COUNT(*) FROM receipts) AS receipts,
               (SELECT COUNT(*) FROM items) AS items,
               (SELECT COALESCE(SUM(final),0) FROM items WHERE 1=1{xclause}) AS spent,
               (SELECT COUNT(*) FROM items WHERE match_type='new') AS unmapped,
               (SELECT COUNT(*) FROM products) AS products
    """, xargs)[0]
    ixclause, ixargs = _excluded_sql("COALESCE(i.category,'Misc')")
    monthly = query(f"""
        SELECT substr(receipt_date,1,7) AS m, ROUND(SUM(i.final),2) AS total
        FROM items i JOIN receipts r ON r.id=i.receipt_id
        WHERE r.receipt_date IS NOT NULL{ixclause}
        GROUP BY m ORDER BY m
    """, ixargs)
    pie, frm, to = _pie_bounds(request.args.get("pie", ""), request.args.get("from", ""), request.args.get("to", ""))
    if frm and to:
        by_cat = query(f"""
            SELECT COALESCE(category,'Misc') AS c, ROUND(SUM(final),2) AS total
            FROM items i JOIN receipts r ON r.id=i.receipt_id
            WHERE r.receipt_date BETWEEN ? AND ?{ixclause}
            GROUP BY c ORDER BY total DESC LIMIT 12
        """, (frm, to, *ixargs))
        full_total = query(f"""
            SELECT COALESCE(SUM(final),0) AS t
            FROM items i JOIN receipts r ON r.id=i.receipt_id
            WHERE r.receipt_date BETWEEN ? AND ?{ixclause}
        """, (frm, to, *ixargs))[0]["t"]
    else:
        by_cat = query(f"""
            SELECT COALESCE(category,'Misc') AS c, ROUND(SUM(final),2) AS total
            FROM items WHERE 1=1{xclause} GROUP BY c ORDER BY total DESC LIMIT 12
        """, xargs)
        full_total = kpis["spent"]
    by_cat = [dict(r) for r in by_cat]
    shown = round(sum(float(r["total"] or 0) for r in by_cat), 2)
    full_total = round(float(full_total or 0), 2)
    if full_total - shown > 0.004:
        by_cat.append({"c": "Other", "total": round(full_total - shown, 2)})
    recent = query("SELECT * FROM receipts ORDER BY receipt_date DESC, receipt_seq ASC, id ASC LIMIT 10")
    top = query(f"""
        SELECT clean, COUNT(*) AS n, ROUND(SUM(final),2) AS total
        FROM items WHERE 1=1{xclause} GROUP BY clean ORDER BY total DESC LIMIT 10
    """, xargs)
    user_totals = query(f"""
        SELECT u.name AS n, ROUND(SUM(i.final * s.share), 2) AS total
        FROM item_splits s
        JOIN items i ON i.id = s.item_id
        JOIN users u ON u.id = s.user_id
        WHERE 1=1{ixclause}
        GROUP BY u.name ORDER BY total DESC
    """, ixargs)
    return render_template(_tpl("dashboard.html"), k=kpis, monthly=monthly,
                           by_cat=by_cat, recent=recent, top=top,
                           user_totals=user_totals, pie=pie, pie_from=frm or "", pie_to=to or "",
                           pie_total=round(sum(float(r["total"] or 0) for r in by_cat), 2))


def _pie_bounds(pie, frm, to):
    """Dashboard pie presets → (pie_key, from_iso, to_iso). Empty bounds = all time."""
    today = date.today()
    if pie == "this_week":
        monday = today - timedelta(days=today.isoweekday() - 1)
        return pie, str(monday), str(today)
    if pie == "last_week":
        monday = today - timedelta(days=today.isoweekday() - 1)
        return pie, str(monday - timedelta(days=7)), str(monday - timedelta(days=1))
    if pie == "30d":
        return pie, str(today - timedelta(days=29)), str(today)
    if pie == "last_month":
        first = today.replace(day=1)
        prev_end = first - timedelta(days=1)
        return pie, str(prev_end.replace(day=1)), str(prev_end)
    if pie == "custom":
        if not frm or not to:
            return pie, str(today - timedelta(days=179)), str(today)
        try:
            date.fromisoformat(frm)
            date.fromisoformat(to)
        except ValueError:
            return pie, str(today - timedelta(days=179)), str(today)
        if frm <= to:
            return pie, frm, to
        return pie, str(today - timedelta(days=179)), str(today)
    return "", "", ""


@app.route("/receipts")
def receipts():
    rows = query("SELECT * FROM receipts ORDER BY receipt_date DESC, receipt_seq ASC, id ASC")
    return render_template(_tpl("receipts.html"), rows=rows)


@app.route("/receipts/<int:rid>/download")
def receipt_download(rid):
    conn = get_db()
    r = conn.execute("SELECT * FROM receipts WHERE id=?", (rid,)).fetchone()
    if not r:
        conn.close()
        return "Not found", 404
    fname = r["filename"] or f"receipt-{rid}.txt"
    path = os.path.join(RECEIPT_DIR, os.path.basename(fname))
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        items = conn.execute("SELECT raw, qty, unit_price, final FROM items WHERE receipt_id=? ORDER BY id",
                             (rid,)).fetchall()
        conn.close()
        lines = [f"{fname} {r['receipt_date'] or ''}".strip()]
        for it in items:
            lines.append(f"{it['raw']} x{it['qty']} @ {it['unit_price']} = {it['final']}")
        text = "\n".join(lines) + "\n"
        resp = app.response_class(text, mimetype="text/plain; charset=utf-8")
        resp.headers["Content-Disposition"] = f"attachment; filename={os.path.basename(fname)}"
        return resp
    conn.close()
    resp = app.response_class(text, mimetype="text/plain; charset=utf-8")
    resp.headers["Content-Disposition"] = f"attachment; filename={os.path.basename(fname)}"
    return resp


@app.route("/receipts/rescan-cards", methods=["POST"])
def receipts_rescan_cards():
    """Fill NULL card_last4 from on-disk receipt texts (fetch imports keep them)."""
    import glob
    fixed = 0
    conn = get_db()
    missing = conn.execute("SELECT id, filename FROM receipts WHERE card_last4 IS NULL OR card_last4 = ''").fetchall()
    texts = {}
    for path in glob.glob(os.path.join(BASE_DIR, "receipts", "*.txt")):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                texts[os.path.basename(path)] = f.read()
        except OSError:
            continue
    for m in missing:
        text = texts.get(m["filename"])
        if not text:
            continue
        last4 = extract_card_last4(text)
        if last4:
            conn.execute("UPDATE receipts SET card_last4=? WHERE id=?", (last4, m["id"]))
            fixed += 1
    conn.commit()
    conn.close()
    flash(f"Rescanned cards: fixed {fixed} of {len(missing)}")
    return redirect(url_for("receipts"))


@app.route("/receipts/resync-names", methods=["POST"])
def receipts_resync_names():
    """One-time repair: re-apply products.clean/category onto item lines whose
    raw was curated after import (rows that predate save-time propagation)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE items
        SET clean=(SELECT p.clean FROM products p WHERE p.raw=items.raw),
            category=(SELECT p.category FROM products p WHERE p.raw=items.raw),
            match_type='exact', match_score=100
        WHERE EXISTS (SELECT 1 FROM products p
                      WHERE p.raw=items.raw
                        AND (p.clean IS NOT items.clean
                             OR p.category IS NOT items.category))""")
    fixed = cur.rowcount
    conn.commit()
    conn.close()
    flash(f"Resynced names: fixed {fixed} line(s)")
    return redirect(url_for("receipts"))


@app.route("/receipts/<int:rid>/delete", methods=["POST"])
def receipt_delete(rid):
    conn = get_db()
    row = conn.execute("SELECT filename FROM receipts WHERE id=?", (rid,)).fetchone()
    if not row:
        conn.close()
        return "Not found", 404
    conn.execute("DELETE FROM receipts WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    flash(f"Deleted {row['filename']} and its items")
    return redirect(url_for("receipts"))


@app.route("/receipts/<int:rid>/card", methods=["POST"])
def receipt_card(rid):
    last4 = (request.form.get("last4") or "").strip()
    if last4 and (len(last4) != 4 or not last4.isdigit()):
        return "Card needs exactly 4 digits (or empty to clear)", 400
    conn = get_db()
    row = conn.execute("SELECT id FROM receipts WHERE id=?", (rid,)).fetchone()
    if not row:
        conn.close()
        return "Not found", 404
    conn.execute("UPDATE receipts SET card_last4=? WHERE id=?", (last4 or None, rid))
    conn.commit()
    conn.close()
    flash(f"Card set to {last4 or 'cash'}")
    return redirect(url_for("receipt_detail", rid=rid))


@app.route("/export.csv")
def export_csv():
    today = date.today()
    frm = request.args.get("from", str(today - timedelta(days=29)))
    to = request.args.get("to", str(today))
    try:
        date.fromisoformat(frm)
        date.fromisoformat(to)
    except ValueError:
        return "Bad date (use YYYY-MM-DD)", 400
    rows = query("""
        SELECT r.receipt_date AS receipt_date, r.filename AS filename, r.store AS store,
               i.raw AS raw, i.clean AS clean, i.category AS category, i.qty AS qty,
               p.unit AS unit, i.unit_price AS unit_price, i.discount AS discount, i.final AS final,
               r.card_last4 AS card_last4, u.name AS payer,
               (SELECT GROUP_CONCAT(u2.name || ':' || s.share, ';')
                FROM item_splits s JOIN users u2 ON u2.id = s.user_id
                WHERE s.item_id = i.id) AS splits,
               CASE WHEN g.pushed_count > 0 THEN 'pushed' ELSE 'unpushed' END AS grocy_status
        FROM items i JOIN receipts r ON r.id = i.receipt_id
        LEFT JOIN products p ON p.raw = i.raw
        LEFT JOIN card_map c ON c.last4 = r.card_last4
        LEFT JOIN users u ON u.id = c.user_id
        LEFT JOIN grocy_pushes g ON g.receipt_id = r.id
        WHERE r.receipt_date BETWEEN ? AND ?
        ORDER BY r.receipt_date, r.filename, i.id
    """, (frm, to))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["receipt_date", "filename", "store", "raw", "clean", "category", "qty", "unit",
                "unit_price", "discount", "final", "card_last4", "payer", "splits", "grocy_status"])
    for x in rows:
        w.writerow([x["receipt_date"], x["filename"], x["store"], x["raw"], x["clean"], x["category"],
                    x["qty"], x["unit"] or "", x["unit_price"], x["discount"], x["final"],
                    x["card_last4"] or "", x["payer"] or "", x["splits"] or "", x["grocy_status"]])
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=export_{frm}_{to}.csv"})


@app.route("/export-analysis.xlsx")
def export_analysis():
    rows = query("""
        SELECT r.filename AS receipt, r.receipt_date AS date, i.raw AS product,
               i.clean AS clean, i.category AS category, i.qty AS qty,
               i.unit_price AS unit_price, i.vat AS vat, i.discount AS discount,
               i.final AS final, i.match_type AS match_type,
               i.match_score AS match_score, p.barcode AS barcode,
               r.store AS store, r.card_last4 AS card
        FROM items i JOIN receipts r ON r.id = i.receipt_id
        LEFT JOIN products p ON p.raw = i.raw
        ORDER BY r.receipt_date, r.filename, i.id
    """)
    cols = ["Receipt", "Date", "Product", "Clean", "Category", "Qty",
            "Unit Price", "VAT", "Discount", "Final", "Match Type",
            "Match Score", "Barcode", "Store", "Card"]
    all_data = pd.DataFrame([dict(r) for r in rows],
                            columns=["receipt", "date", "product", "clean",
                                     "category", "qty", "unit_price", "vat",
                                     "discount", "final", "match_type",
                                     "match_score", "barcode", "store", "card"])
    all_data.columns = cols
    # Barcodes / card tails must stay text in Excel (leading zeros matter).
    for text_col in ("Barcode", "Card"):
        all_data[text_col] = all_data[text_col].astype("string")
    if len(all_data):
        trends = (all_data.groupby(["Clean", "Date"])["Unit Price"]
                          .agg(["mean", "min", "max"]).reset_index()
                          .rename(columns={"Clean": "Clean Product"}))
    else:
        trends = pd.DataFrame(columns=["Clean Product", "Date", "mean", "min", "max"])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as writer:
        all_data.to_excel(writer, sheet_name="All Data", index=False)
        trends.to_excel(writer, sheet_name="Price Trends", index=False)
    buf.seek(0)
    stamp = date.today().strftime("%Y-%m-%d")
    return Response(buf.getvalue(),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=analysis_{stamp}.xlsx"})


@app.route("/export-products.xlsx")
def export_products():
    rows = query("""
        SELECT raw, clean, category, barcode, brand, unit, weight, notes,
               grocy_track
        FROM products ORDER BY raw
    """)
    cols = ["raw", "clean", "category", "barcode", "brand", "unit",
            "weight", "notes", "grocy_track"]
    df = pd.DataFrame([dict(r) for r in rows], columns=cols)
    df["barcode"] = df["barcode"].astype("string")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as writer:
        df.to_excel(writer, sheet_name="Products", index=False)
    buf.seek(0)
    stamp = date.today().strftime("%Y-%m-%d")
    return Response(buf.getvalue(),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=products_{stamp}.xlsx"})


_SHARE_COLS = ["raw", "clean", "category", "barcode", "brand", "unit",
               "weight", "notes", "grocy_track"]


def _share_norm(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def _share_track(v):
    return 0 if _share_norm(v).lower() in ("0", "false", "no") else 1


@app.route("/import-products", methods=["GET", "POST"])
def import_products():
    if request.method == "GET":
        return render_template(_tpl("import_products.html"))
    f = request.files.get("file")
    if not f or not f.filename:
        flash("No file selected")
        return redirect(url_for("import_products"))
    name = f.filename
    try:
        if name.lower().endswith(".csv"):
            df = pd.read_csv(f, dtype=str, keep_default_na=False)
        else:
            df = pd.read_excel(f, dtype=str)
    except Exception as e:
        flash(f"Could not read {name}: {str(e)[:150]}")
        return redirect(url_for("import_products"))
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "raw" not in df.columns:
        flash("File needs a 'raw' column")
        return redirect(url_for("import_products"))
    conn = get_db()
    conn.execute("DELETE FROM import_staging")
    conn.execute("DELETE FROM import_warnings")
    staged = 0
    for _, row in df.iterrows():
        raw = _share_norm(row.get("raw"))
        if not raw:
            continue
        conn.execute("""INSERT OR REPLACE INTO import_staging
                        (raw, clean, category, barcode, brand, unit, weight,
                         notes, grocy_track, source_file)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (raw, _share_norm(row.get("clean")),
                      _share_norm(row.get("category")) or "Misc",
                      _share_norm(row.get("barcode")), _share_norm(row.get("brand")),
                      _share_norm(row.get("unit")), _share_norm(row.get("weight")),
                      _share_norm(row.get("notes")), _share_track(row.get("grocy_track")),
                      name))
        staged += 1
    added = 0
    for s in conn.execute("SELECT * FROM import_staging").fetchall():
        d = dict(s)
        bc = (d["barcode"] or "").strip()
        if bc:
            for o in conn.execute("SELECT raw FROM products WHERE barcode=? AND raw<>?",
                                  (bc, d["raw"])).fetchall():
                conn.execute("""INSERT INTO import_warnings (barcode, local_raw, incoming_raw, source_file)
                                VALUES (?,?,?,?)""", (bc, o[0], d["raw"], name))
        if conn.execute("SELECT 1 FROM products WHERE raw=?", (d["raw"],)).fetchone():
            continue
        conn.execute("""INSERT INTO products
                        (raw, clean, category, barcode, brand, unit, weight,
                         notes, grocy_track, needs_review)
                        VALUES (?,?,?,?,?,?,?,?,?,0)""",
                     (d["raw"], d["clean"], d["category"], d["barcode"],
                      d["brand"], d["unit"], d["weight"], d["notes"],
                      d["grocy_track"]))
        conn.execute("DELETE FROM import_staging WHERE raw=?", (d["raw"],))
        added += 1
    conn.commit()
    conn.close()
    flash(f"Imported {staged} rows from {name}: {added} new products added")
    return redirect(url_for("import_products_review"))


@app.route("/import-products/review")
def import_products_review():
    conn = get_db()
    staged = [dict(r) for r in conn.execute("SELECT * FROM import_staging ORDER BY raw").fetchall()]
    warnings = [dict(r) for r in
                conn.execute("SELECT barcode, local_raw, incoming_raw FROM import_warnings ORDER BY barcode").fetchall()]
    conflicts = []
    for s in staged:
        local = conn.execute("SELECT clean, category, barcode, brand, unit, weight, notes, grocy_track"
                             " FROM products WHERE raw=?", (s["raw"],)).fetchone()
        if not local:
            continue
        local = {k: ("" if local[k] is None else str(local[k])) for k in _SHARE_COLS[1:]}
        incoming = {c: ("" if s[c] is None else str(s[c])) for c in _SHARE_COLS[1:]}
        diffs = {c: (local[c], incoming[c]) for c in _SHARE_COLS[1:]
                 if local[c] != incoming[c]}
        if diffs:
            conflicts.append({"raw": s["raw"], "local": local,
                              "incoming": incoming, "diffs": diffs})
        bc = (s["barcode"] or "").strip()
        if bc:
            for o in conn.execute("SELECT raw FROM products WHERE barcode=? AND raw<>?",
                                  (bc, s["raw"])).fetchall():
                if not any(w["local_raw"] == o[0] and w["incoming_raw"] == s["raw"] for w in warnings):
                    warnings.append({"barcode": bc, "local_raw": o[0], "incoming_raw": s["raw"]})
    conn.close()
    return render_template(_tpl("import_review.html"), conflicts=conflicts,
                           warnings=warnings)


@app.route("/import-products/resolve", methods=["POST"])
def import_products_resolve():
    default = request.form.get("default", "local")
    conn = get_db()
    staged = [dict(r) for r in conn.execute("SELECT * FROM import_staging").fetchall()]
    counts = {"local": 0, "new": 0, "both": 0}
    for s in staged:
        choice = request.form.get(f"choice:{s['raw']}", default)
        if choice not in ("local", "new", "both"):
            choice = "local"
        if choice == "local":
            counts["local"] += 1
        elif choice == "new":
            conn.execute("""UPDATE products SET clean=?, category=?, barcode=?, brand=?,
                            unit=?, weight=?, notes=?, grocy_track=?, updated_at=datetime('now')
                            WHERE raw=?""",
                         (s["clean"], s["category"], s["barcode"], s["brand"],
                          s["unit"], s["weight"], s["notes"], s["grocy_track"], s["raw"]))
            counts["new"] += 1
        else:
            new_raw = f"{s['raw']} [from {s['source_file']}]"
            if not conn.execute("SELECT 1 FROM products WHERE raw=?", (new_raw,)).fetchone():
                conn.execute("""INSERT INTO products
                                (raw, clean, category, barcode, brand, unit, weight,
                                 notes, grocy_track, needs_review)
                                VALUES (?,?,?,?,?,?,?,?,?,0)""",
                             (new_raw, s["clean"], s["category"], s["barcode"],
                              s["brand"], s["unit"], s["weight"], s["notes"],
                              s["grocy_track"]))
            counts["both"] += 1
    conn.execute("DELETE FROM import_staging")
    conn.execute("DELETE FROM import_warnings")
    conn.commit()
    conn.close()
    flash(f"Merge done: {counts['new']} kept-new, {counts['local']} kept-local, {counts['both']} kept-both")
    return redirect(url_for("products"))


@app.route("/categories/merge", methods=["GET", "POST"])
def categories_merge():
    conn = get_db()
    local = [r[0] for r in conn.execute(
        "SELECT DISTINCT COALESCE(category,'Misc') FROM products ORDER BY 1").fetchall()]
    groups = [r[0] for r in conn.execute("SELECT name FROM categories ORDER BY name").fetchall()]
    if request.method == "POST":
        renamed_cats = renamed_products = renamed_items = 0
        for cat in local:
            choice = (request.form.get(f"map:{cat}") or "__keep__").strip()
            if choice in ("__keep__", "", cat) or choice not in groups:
                continue
            cur = conn.cursor()
            cur.execute("UPDATE products SET category=?, updated_at=datetime('now') WHERE category=?",
                        (choice, cat))
            renamed_products += cur.rowcount
            cur.execute("UPDATE items SET category=? WHERE category=?", (choice, cat))
            renamed_items += cur.rowcount
            renamed_cats += 1
        conn.commit()
        conn.close()
        flash(f"Renamed {renamed_cats} categories ({renamed_products} products, {renamed_items} lines)")
        return redirect(url_for("products"))
    lower = {g.lower(): g for g in groups}
    rows = [{"local": cat, "match": lower.get(cat.lower())} for cat in local]
    unused = [g for g in groups if g.lower() not in {c.lower() for c in local}]
    conn.close()
    return render_template(_tpl("categories_merge.html"), rows=rows, groups=groups,
                           unused=unused)


@app.route("/backup-db")
def backup_db():
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = get_db()
        conn.execute(f"VACUUM INTO '{path.replace(chr(39), chr(39) * 2)}'")
        conn.close()
        with open(path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    stamp = date.today().strftime("%Y-%m-%d")
    return Response(data, mimetype="application/x-sqlite3",
                    headers={"Content-Disposition": f"attachment; filename=lidl-backup-{stamp}.db"})


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        files = request.files.getlist("files")
        if not files or files[0].filename == "":
            flash("No files selected")
            return redirect(url_for("upload"))
        mem = memory_df()
        conn = get_db()
        cur = conn.cursor()
        imported = 0
        for f in files:
            fname = f.filename
            text = f.read().decode("utf-8", errors="replace")
            from fetch_lib import extract_mark as _extract_mark
            mark = _extract_mark(text)
            if mark and cur.execute("SELECT 1 FROM receipts WHERE mark=?", (mark,)).fetchone():
                flash(f"{fname}: already imported (MAPK {mark}) — skipped")
                continue
            df = parse_receipt(text, fname)
            if df.empty:
                flash(f"{fname}: no items parsed")
                continue
            iso_date, seq = parse_receipt_date(fname)
            total = float((df["Qty"] * df["Unit Price"] - df["Discount"]).sum())
            card_last4 = extract_card_last4(text)
            # Keep the raw text on disk (like paste imports) so cards and
            # other fields can be re-extracted later without re-uploading.
            try:
                os.makedirs(os.path.join(BASE_DIR, "receipts"), exist_ok=True)
                with open(os.path.join(BASE_DIR, "receipts", os.path.basename(fname)),
                          "w", encoding="utf-8") as _rf:
                    _rf.write(text)
            except OSError:
                pass
            cur.execute(
                """INSERT OR IGNORE INTO receipts (filename, receipt_date, receipt_seq, item_count, total, card_last4, mark)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (fname, iso_date, seq, len(df), round(total, 2), card_last4, mark))
            conn.commit()
            rid = cur.execute("SELECT id FROM receipts WHERE filename=?", (fname,)).fetchone()["id"]
            cur.execute("DELETE FROM items WHERE receipt_id=?", (rid,))
            from fetch_lib import canonical_raw
            for _, item in df.iterrows():
                raw = canonical_raw(conn, item["Product"])
                clean, cat, mtype, score = smart_match(raw, mem)
                tot = float(item["Qty"] * item["Unit Price"])
                cur.execute(
                    """INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price,
                                          vat, discount, total, final, match_type, match_score)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (rid, raw, clean, cat, float(item["Qty"]),
                     float(item["Unit Price"]), float(item["VAT"]), float(item["Discount"]),
                     round(tot, 2), round(tot - float(item["Discount"]), 2), mtype, float(score)))
                cur.execute("""INSERT OR IGNORE INTO products (raw, clean, category, needs_review)
                               VALUES (?,?,?,1)""", (raw, clean, cat))
            imported += 1
        conn.commit()
        conn.close()
        flash(f"Imported {imported} receipt(s)")
        return redirect(url_for("receipts"))
    return render_template(_tpl("upload.html"))


def _product_filters(q, f, cat, gf, tf):
    """Shared WHERE-clause builder for the Products hub page + JSON API."""
    clauses, args = [], []
    if f == "unmapped":
        # products whose clean equals raw (never curated) OR items with match_type new
        clauses.append("(clean = raw OR clean IS NULL)")
    elif f == "needs-review":
        clauses.append("needs_review = 1")
    if q:
        clauses.append("(grnorm(raw) LIKE '%' || grnorm(?) || '%' OR grnorm(clean) LIKE '%' || grnorm(?) || '%' "
                       "OR grnorm(category) LIKE '%' || grnorm(?) || '%' OR barcode LIKE ?)")
        args += [q, q, q, f"%{q}%"]
    if cat:
        clauses.append("category = ?")
        args.append(cat)
    if gf == "linked":
        clauses.append("grocy_id IS NOT NULL")
    elif gf == "unlinked":
        clauses.append("grocy_id IS NULL")
    if tf == "tracked":
        clauses.append("grocy_track != 0")
    elif tf == "skipped":
        clauses.append("grocy_track = 0")
    return clauses, args


@app.route("/mappings")
def mappings_redirect():
    qs = request.query_string.decode("utf-8", errors="replace")
    return redirect("/products" + ("?" + qs if qs else ""), 301)


@app.route("/products")
def products():
    q = request.args.get("q", "").strip()
    f = request.args.get("filter", "all")
    cat = request.args.get("cat", "").strip()
    gf = request.args.get("grocy", "all")
    tf = request.args.get("track", "all")
    sort = request.args.get("sort", "raw")
    if sort not in ("raw", "clean", "category", "barcode"):
        sort = "raw"
    direction = request.args.get("dir", "asc")
    if direction not in ("asc", "desc"):
        direction = "asc"
    try:
        per = int(request.args.get("per", "100"))
    except ValueError:
        per = 100
    if per not in (25, 50, 100):
        per = 100
    conn = get_db()
    clauses, args = _product_filters(q, f, cat, gf, tf)
    sql = "SELECT * FROM products"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    count = conn.execute(f"SELECT COUNT(*) FROM products" + (" WHERE " + " AND ".join(clauses) if clauses else ""), args).fetchone()[0]
    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1
    per_page = per
    pages = max((count + per_page - 1) // per_page, 1)
    page = min(page, pages)
    sql += f" ORDER BY {sort} {direction} LIMIT {per_page} OFFSET {(page - 1) * per_page}"
    rows = conn.execute(sql, args).fetchall()
    new_items = conn.execute(
        "SELECT DISTINCT raw FROM items WHERE match_type='new' AND raw NOT IN (SELECT raw FROM products) LIMIT 100"
    ).fetchall()
    cats = conn.execute("SELECT DISTINCT category FROM products ORDER BY category").fetchall()
    grocy_cats = [r[0] for r in conn.execute("SELECT name FROM categories ORDER BY name").fetchall()]
    dup_groups = conn.execute("SELECT COUNT(*) FROM (SELECT grnorm(raw) AS k FROM products GROUP BY k HAVING COUNT(*) > 1)").fetchone()[0]
    linked_ids = [r[0] for r in conn.execute("SELECT grocy_id FROM products WHERE grocy_id IS NOT NULL").fetchall()]
    conn.close()
    grocy_error = None
    lookups = {"locations": [], "quantity_units": []}
    try:
        base_url, api_key = grocy_client.get_config()
        catalog = grocy_client.fetch_catalog(base_url, api_key)
        grocy_configured = True
        grocy_names = catalog["names"][:2000]
        grocy_products = [{"id": p["id"], "name": str(p.get("name", ""))} for p in catalog["products"][:2000]]
        if catalog.get("stale"):
            grocy_error = "Grocy unreachable — showing cached list."
        else:
            try:
                lookups = grocy_client.fetch_lookups(base_url, api_key)
            except Exception:
                pass
    except grocy_client.GrocyConfigError:
        catalog = None
        grocy_configured = False
        grocy_names = []
        grocy_products = []
    except Exception as e:
        catalog = None
        grocy_configured = True
        grocy_names = []
        grocy_products = []
        grocy_error = f"Grocy unreachable: {str(e)[:150]}"
    orphans = set(grocy_client.find_orphans(linked_ids, catalog)) if catalog is not None else set()
    enriched = []
    for r in rows:
        d = dict(r)
        d["is_orphan"] = d.get("grocy_id") in orphans
        if grocy_configured and catalog is not None and not d.get("grocy_id"):
            try:
                d["suggestions"] = grocy_client.suggest_grocy(d.get("clean"), d.get("barcode"), catalog)
            except Exception:
                d["suggestions"] = []
        else:
            d["suggestions"] = []
        enriched.append(d)
    return render_template(_tpl("products.html"), rows=enriched, new_items=new_items, q=q, filt=f, cats=cats,
                           grocy_cats=grocy_cats,
                           grocy_configured=grocy_configured, grocy_names=grocy_names,
                           grocy_error=grocy_error, orphans=sorted(orphans),
                           sort=sort, direction=direction, cat=cat, gf=gf, tf=tf, per=per,
                           page=page, pages=pages, count=count, grocy_products=grocy_products,
                           locations=lookups["locations"], quantity_units=lookups["quantity_units"],
                           dup_groups=dup_groups, grocy_base=_grocy_base(),
                           abc_rank=_abc_rank_map())


@app.route("/products/duplicates")
def products_duplicates():
    groups = [{"key": grnorm(g[0]["raw"]),
               "rows": sorted(g, key=lambda r: (-r["nitems"], r["id"]))}
              for g in _duplicate_groups()]
    return render_template(_tpl("duplicates.html"), groups=groups)


@app.route("/products/duplicates/merge", methods=["POST"])
def products_duplicates_merge():
    groups = _duplicate_groups()
    conn = get_db()
    merged = 0
    for g in groups:
        key = "winner:" + grnorm(g[0]["raw"])
        raws = [r["raw"] for r in g]
        winner = request.form.get(key)
        if winner not in raws:
            winner = sorted(g, key=lambda r: (-r["nitems"], r["id"]))[0]["raw"]
        w = next(r for r in g if r["raw"] == winner)
        for l in g:
            if l["raw"] == winner:
                continue
            conn.execute("UPDATE items SET raw=?, clean=?, category=? WHERE raw=?",
                         (winner, w["clean"], w["category"], l["raw"]))
            if w["grocy_id"] is None and l["grocy_id"] is not None:
                conn.execute("""UPDATE products SET grocy_id=?, grocy_name=?, grocy_track=?,
                                purchase_qu_id=?, qu_factor=? WHERE raw=?""",
                             (l["grocy_id"], l["grocy_name"], l["grocy_track"],
                              l["purchase_qu_id"], l["qu_factor"], winner))
                w["grocy_id"] = l["grocy_id"]
            conn.execute("DELETE FROM products WHERE raw=?", (l["raw"],))
            merged += 1
    conn.commit()
    conn.close()
    flash(f"Merged {merged} duplicate products")
    return redirect(url_for("products_duplicates"))


def _duplicate_groups():
    conn = get_db()
    rows = conn.execute("""
        SELECT p.id AS id, p.raw AS raw, p.clean AS clean, p.category AS category,
               p.grocy_id AS grocy_id, p.grocy_name AS grocy_name,
               p.grocy_track AS grocy_track, p.purchase_qu_id AS purchase_qu_id,
               p.qu_factor AS qu_factor, COUNT(i.id) AS nitems
        FROM products p LEFT JOIN items i ON i.raw = p.raw
        GROUP BY p.id ORDER BY p.id""").fetchall()
    conn.close()
    groups = {}
    for r in rows:
        groups.setdefault(grnorm(r["raw"]), []).append(dict(r))
    return [v for v in groups.values() if len(v) > 1]


@app.route("/api/products")
def api_products():
    q = request.args.get("q", "").strip()
    f = request.args.get("filter", "all")
    cat = request.args.get("cat", "").strip()
    gf = request.args.get("grocy", "all")
    tf = request.args.get("track", "all")
    sort = request.args.get("sort", "raw")
    if sort not in ("raw", "clean", "category", "barcode"):
        sort = "raw"
    direction = request.args.get("dir", "asc")
    if direction not in ("asc", "desc"):
        direction = "asc"
    try:
        per = int(request.args.get("per", "100"))
    except ValueError:
        per = 100
    if per not in (25, 50, 100):
        per = 100
    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1
    clauses, args = _product_filters(q, f, cat, gf, tf)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM products" + where, args).fetchone()[0]
    pages = max((count + per - 1) // per, 1)
    page = min(page, pages)
    rows = conn.execute(f"SELECT * FROM products{where} ORDER BY {sort} {direction} LIMIT {per} OFFSET {(page - 1) * per}",
                        args).fetchall()
    conn.close()
    return jsonify({"rows": [dict(r) for r in rows], "page": page, "pages": pages, "count": count})


@app.route("/products/<int:pid>", methods=["GET", "POST"])
def product_detail(pid):
    conn = get_db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        conn.close()
        return "Not found", 404
    p = dict(p)
    if request.method == "POST":
        form = request.form
        updates, vals = [], []
        for key in ("clean", "category", "barcode", "brand", "unit", "weight", "notes", "market", "grocy_name"):
            if key in form:
                updates.append(f"{key} = ?")
                vals.append(form.get(key).strip())
        gid_raw = form.get("grocy_id", "").strip()
        if gid_raw:
            try:
                gid = int(gid_raw)
            except ValueError:
                conn.close()
                flash("Grocy id must be a number")
                return redirect(url_for("product_detail", pid=pid))
            updates.append("grocy_id = ?")
            vals.append(gid)
        else:
            updates.append("grocy_id = NULL")
        for key in ("purchase_qu_id",):
            v = (form.get(key) or "").strip()
            if v:
                try:
                    updates.append(f"{key} = ?")
                    vals.append(int(v))
                except ValueError:
                    conn.close()
                    flash(f"{key} must be a number")
                    return redirect(url_for("product_detail", pid=pid))
            else:
                updates.append(f"{key} = NULL")
        if "qu_factor" in form:
            v = (form.get("qu_factor") or "").strip()
            if v:
                try:
                    updates.append("qu_factor = ?")
                    vals.append(float(v))
                except ValueError:
                    conn.close()
                    flash("Factor must be a number")
                    return redirect(url_for("product_detail", pid=pid))
            else:
                updates.append("qu_factor = NULL")
        # The track checkbox always renders, so absence means unchecked.
        updates.append("grocy_track = ?")
        vals.append(1 if form.get("grocy_track") == "1" else 0)
        updates.append("needs_review = 0")
        updates.append("updated_at = datetime('now')")
        vals.append(pid)
        conn.execute(f"UPDATE products SET {', '.join(updates)} WHERE id=?", vals)
        # Re-resolve existing items like /mappings/update does, so receipts
        # show the curated clean name immediately (not the stale raw value).
        if "clean" in form or "category" in form:
            new_clean = (form.get("clean") or "").strip() if "clean" in form else p["clean"]
            new_cat = (form.get("category") or "").strip() if "category" in form else p["category"]
            conn.execute("""UPDATE items SET clean=?, category=?, match_type='exact', match_score=100
                            WHERE raw=?""", (new_clean, new_cat, p["raw"]))
        if gid_raw and not (form.get("qu_factor") or "").strip() and not (form.get("purchase_qu_id") or "").strip():
            _prefill_qu(conn, p["raw"], gid)
        conn.commit()
        new_barcode = (form.get("barcode") or "").strip() if "barcode" in form else (p.get("barcode") or "")
        new_gid = gid if gid_raw else p.get("grocy_id")
        if new_gid and new_barcode and new_barcode != (p.get("barcode") or ""):
            try:
                base_url, api_key = grocy_client.get_config()
                new_qu = form.get("purchase_qu_id", "").strip() if "purchase_qu_id" in form else None
                try:
                    new_qu = int(new_qu) if new_qu else p.get("purchase_qu_id")
                except (TypeError, ValueError):
                    new_qu = p.get("purchase_qu_id")
                _save_barcode(base_url, api_key, int(new_gid), new_barcode, new_qu)
                flash(f"Barcode {new_barcode} synced to Grocy #{new_gid}")
            except Exception as e:
                flash(f"Barcode sync failed (stored locally): {str(e)[:150]}")
        if request.args.get("next") == "1":
            nxt = conn.execute("SELECT id FROM products WHERE needs_review = 1 AND id != ? ORDER BY id LIMIT 1",
                               (pid,)).fetchone()
            conn.close()
            if nxt:
                return redirect(url_for("product_detail", pid=nxt["id"], next="1"))
            flash("No more unreviewed products")
            return redirect(url_for("products", filter="needs-review"))
        conn.close()
        flash("Product saved")
        return redirect(url_for("product_detail", pid=pid))
    receipts = [dict(r) for r in conn.execute(
        """SELECT r.id AS id, r.filename AS filename, r.receipt_date AS receipt_date,
                  i.qty AS qty, i.unit_price AS unit_price, i.final AS final
           FROM items i JOIN receipts r ON r.id = i.receipt_id
           WHERE i.raw = ? ORDER BY r.receipt_date DESC, r.id DESC""", (p["raw"],)).fetchall()]
    stores = conn.execute(
        "SELECT r.store AS store, COUNT(*) AS n FROM items i JOIN receipts r ON r.id = i.receipt_id "
        "WHERE i.raw = ? GROUP BY r.store ORDER BY n DESC LIMIT 1", (p["raw"],)).fetchone()
    units = [dict(u) for u in conn.execute("SELECT id, grocy_id, name FROM units ORDER BY name").fetchall()]
    cats = sorted({r[0] for r in conn.execute("SELECT category FROM products WHERE category IS NOT NULL").fetchall()} |
                  {r[0] for r in conn.execute("SELECT name FROM categories").fetchall()} |
                  ({p["category"]} if p.get("category") else set()))
    conn.close()
    p["usual_store"] = p.get("market") or (stores["store"] if stores else "Lidl")
    suggestions = []
    try:
        base_url, api_key = grocy_client.get_config()
        catalog = grocy_client.fetch_catalog(base_url, api_key)
        if not p.get("grocy_id"):
            suggestions = grocy_client.suggest_grocy(p.get("clean"), p.get("barcode"), catalog)
    except Exception:
        suggestions = []
    return render_template(_tpl("product_detail.html"), p=p, receipts=receipts, units=units,
                           suggestions=suggestions, cats=cats, grocy_base=_grocy_base())


def _prefill_qu(conn, raw, gid):
    """Best-effort: store the Grocy product's purchase QU + factor on the raw row."""
    try:
        base_url, api_key = grocy_client.get_config()
        details = grocy_client.get_product_details(base_url, api_key, gid)
    except Exception:
        return
    try:
        factor = float(details.get("qu_conversion_factor_purchase_to_stock", 1.0) or 0) or 1.0
    except (TypeError, ValueError):
        factor = 1.0
    qu_id = details.get("qu_id_purchase")
    try:
        qu_id = int(qu_id) if qu_id is not None else None
    except (TypeError, ValueError):
        qu_id = None
    conn.execute("UPDATE products SET purchase_qu_id=?, qu_factor=? WHERE raw=?", (qu_id, factor, raw))


def _save_barcode(base_url, api_key, product_id, barcode, qu_id):
    """Post a barcode with its packaging size; fall back without qu_id.

    Returns (barcode_object_id, used_qu_size: bool). Older Grocy versions
    reject the qu_id field — push never fails on this.
    """
    if qu_id is not None:
        try:
            return grocy_client.add_barcode(base_url, api_key, product_id, barcode, qu_id=qu_id), True
        except grocy_client.GrocyApiError:
            pass
    return grocy_client.add_barcode(base_url, api_key, product_id, barcode), False


@app.route("/mappings/link", methods=["POST"])
def mapping_link():
    raw = request.form["raw"]
    try:
        gid = int(request.form["grocy_id"])
    except (KeyError, ValueError):
        flash("Missing grocy_id")
        return redirect(url_for("products"))
    gname = request.form.get("grocy_name", "").strip()
    try:
        base_url, api_key = grocy_client.get_config()
        catalog = grocy_client.fetch_catalog(base_url, api_key)
        if gid not in catalog["by_id"]:
            flash(f"Grocy #{gid} not found (stale list)")
            return redirect(url_for("products"))
        if not gname:
            gname = str(catalog["by_id"][gid].get("name", ""))
    except Exception as e:
        flash(f"Grocy check failed: {e}")
        return redirect(url_for("products"))
    conn = get_db()
    conn.execute("UPDATE products SET grocy_id=?, grocy_name=?, grocy_synced_at=datetime('now') WHERE raw=?", (gid, gname, raw))
    _prefill_qu(conn, raw, gid)
    conn.commit()
    conn.close()
    flash(f"Linked {raw} -> Grocy #{gid}")
    return redirect(url_for("products", q=request.form.get("_q", "")))


@app.route("/mappings/unlink", methods=["POST"])
def mapping_unlink():
    raw = request.form["raw"]
    conn = get_db()
    conn.execute("UPDATE products SET grocy_id=NULL, grocy_name=NULL, grocy_synced_at=NULL WHERE raw=?", (raw,))
    conn.commit()
    conn.close()
    flash(f"Unlinked {raw}")
    return redirect(url_for("products"))


@app.route("/api/grocy_status")
def api_grocy_status():
    try:
        base_url, api_key = grocy_client.get_config()
    except Exception:
        return jsonify({"configured": False, "hint": grocy_client.CONFIG_HINT})
    try:
        catalog = grocy_client.fetch_catalog(base_url, api_key)
        conn = get_db()
        linked = [r[0] for r in conn.execute("SELECT grocy_id FROM products WHERE grocy_id IS NOT NULL").fetchall()]
        conn.close()
        orphans = grocy_client.find_orphans(linked, catalog)
        return jsonify({"configured": True, "product_count": len(catalog["products"]), "cached_at": catalog["cached_at"], "orphans": orphans, "stale": bool(catalog.get("stale"))})
    except Exception as e:
        return jsonify({"configured": True, "error": str(e)[:200]}), 502


def resolve_push_items(receipt_id):
    conn = get_db()
    r = conn.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
    items = conn.execute("SELECT * FROM items WHERE receipt_id=?", (receipt_id,)).fetchall()
    prods = {p["raw"]: dict(p) for p in conn.execute(
        "SELECT raw, grocy_id, grocy_name, grocy_track, purchase_qu_id, qu_factor FROM products").fetchall()}
    unit_names = {u["grocy_id"]: u["name"] for u in
                  conn.execute("SELECT grocy_id, name FROM units WHERE grocy_id IS NOT NULL").fetchall()}
    conn.close()
    grocy_error = None
    try:
        base_url, api_key = grocy_client.get_config()
        catalog = grocy_client.fetch_catalog(base_url, api_key)
        grocy_configured = True
        if catalog.get("stale"):
            grocy_error = "Grocy unreachable — showing cached list."
    except grocy_client.GrocyConfigError:
        return r, [], None, False, grocy_client.CONFIG_HINT
    except Exception as e:
        return r, [], None, True, f"Grocy unreachable: {str(e)[:150]}"
    out = []
    for i in items:
        d = dict(i)
        p = prods.get(d["raw"])
        qty = float(d.get("qty") or 0)
        if not qty:
            d.update({"grocy_id": None, "grocy_name": "", "amount": 0, "price": 0.0, "status": "bad-quantity"})
        elif p is not None and not p.get("grocy_track", 1):
            d.update({"grocy_id": p.get("grocy_id"), "grocy_name": p.get("grocy_name") or "",
                        "amount": qty, "price": float(d.get("final") or 0) / qty,
                        "status": "excluded-flagged"})
        elif p is None or p.get("grocy_id") is None:
            d.update({"grocy_id": None, "grocy_name": "", "amount": qty,
                        "price": float(d.get("final") or 0) / qty, "status": "excluded-no-link"})
        elif int(p["grocy_id"]) not in catalog["by_id"]:
            d.update({"grocy_id": p["grocy_id"], "grocy_name": p.get("grocy_name") or "",
                        "amount": qty, "price": float(d.get("final") or 0) / qty,
                        "status": "orphan-link"})
        else:
            gid = int(p["grocy_id"])
            try:
                factor = float(p.get("qu_factor") or 1.0)
            except (TypeError, ValueError):
                factor = 1.0
            if factor <= 0:
                factor = 1.0
            amount = round(qty * factor, 4)
            unit_name = unit_names.get(p.get("purchase_qu_id"), "")
            d.update({"grocy_id": gid,
                        "grocy_name": str(catalog["by_id"][gid].get("name", "")),
                        "amount": amount, "price": float(d.get("final") or 0) / amount if amount else 0.0,
                        "purchase_qty": qty, "purchase_unit": unit_name, "qu_factor": factor,
                        "status": "ready"})
        out.append(d)
    return r, out, catalog, grocy_configured, grocy_error


def _receipt_items(rid):
    conn = get_db()
    rows = conn.execute("SELECT * FROM items WHERE receipt_id=?", (rid,)).fetchall()
    conn.close()
    return rows


def _push_log(rid):
    conn = get_db()
    row = conn.execute("SELECT * FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _json_wants_json():
    return "application/json" in (request.headers.get("Accept", ""))


def _split_context(conn, r, items):
    """Users list, per-item splits dict, paid-with label; first-view equal-share seeding."""
    users = [dict(u) for u in conn.execute(
        "SELECT id, name FROM users WHERE COALESCE(active,1)=1 ORDER BY name").fetchall()]
    # First-view seeding writes equal shares — but not while rendering a
    # just-failed POST (pending flashes): a rejected split must leave the row
    # unchanged, so don't auto-create rows on that render.
    if users and not session.get("_flashes"):
        share = 1.0 / len(users)
        for it in items:
            has = conn.execute("SELECT 1 FROM item_splits WHERE item_id=?", (it["id"],)).fetchone()
            if not has:
                for u in users:
                    conn.execute(
                        "INSERT OR IGNORE INTO item_splits (item_id, user_id, share) VALUES (?,?,?)",
                        (it["id"], u["id"], share))
        conn.commit()
    splits = {}
    for s in conn.execute("""
            SELECT s.item_id AS item_id, s.user_id AS user_id, s.share AS share,
                   u.name AS user_name
            FROM item_splits s JOIN users u ON u.id = s.user_id
            ORDER BY s.item_id, u.name""").fetchall():
        splits.setdefault(s["item_id"], []).append(dict(s))
    last4 = r["card_last4"] if r and "card_last4" in r.keys() else None
    if not last4:
        paid_with = "Cash"
    else:
        owner = conn.execute(
            "SELECT u.name AS name FROM card_map c JOIN users u ON u.id = c.user_id WHERE c.last4=?",
            (last4,)).fetchone()
        paid_with = owner["name"] if owner else "Card \u2022\u2022" + last4
    return users, splits, paid_with


@app.route("/receipts/<int:rid>/splits", methods=["POST"])
def receipt_splits(rid):
    try:
        item_id = int(request.form.get("item_id") or "0")
    except ValueError:
        return redirect(url_for("receipt_detail", rid=rid))
    conn = get_db()
    ok = conn.execute("SELECT 1 FROM items WHERE id=? AND receipt_id=?",
                      (item_id, rid)).fetchone()
    if not ok:
        conn.close()
        return redirect(url_for("receipt_detail", rid=rid))
    named, bad = [], False
    for k, v in request.form.items():
        if not k.startswith("share_"):
            continue
        try:
            uid = int(k.split("_", 1)[1])
        except ValueError:
            continue
        v = (v or "").strip()
        if not v:
            continue
        try:
            s = float(v.replace(",", "."))
        except ValueError:
            bad = True
            continue
        named.append((uid, s))
    if named or bad:
        if named and not bad and all(s > 0 for _, s in named) \
                and abs(sum(s for _, s in named) - 1.0) < 0.001:
            conn.execute("DELETE FROM item_splits WHERE item_id=?", (item_id,))
            for u, s in named:
                conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,?)",
                             (item_id, u, s))
            conn.commit()
        else:
            flash("Shares must be positive and sum to 1.0 — row unchanged")
    elif "user_id" in request.form and request.form.get("user_id"):
        try:
            uid = int(request.form["user_id"])
        except ValueError:
            uid = 0
        if uid > 0:
            conn.execute("DELETE FROM item_splits WHERE item_id=?", (item_id,))
            conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,1.0)",
                         (item_id, uid))
            conn.commit()
    elif request.form.get("shares"):
        parts = []
        try:
            for chunk in request.form["shares"].split(","):
                u, s = chunk.split(":")
                parts.append((int(u), float(s)))
        except ValueError:
            parts = []
        if parts and abs(sum(s for _, s in parts) - 1.0) < 0.001 and all(s > 0 for _, s in parts):
            conn.execute("DELETE FROM item_splits WHERE item_id=?", (item_id,))
            for u, s in parts:
                conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,?)",
                             (item_id, u, s))
            conn.commit()
        else:
            flash("Shares must be positive and sum to 1.0 — row unchanged")
    conn.close()
    return redirect(url_for("receipt_detail", rid=rid))


@app.route("/receipts/<int:rid>/grocy-preview")
def grocy_preview(rid):
    r, resolved, catalog, configured, err = resolve_push_items(rid)
    if not r:
        return "Not found", 404
    if _json_wants_json():
        return jsonify({"configured": configured, "error": err,
                        "stale": bool(catalog and catalog.get("stale")),
                        "items": resolved})
    items = _receipt_items(rid)
    conn = get_db()
    users, splits, paid_with = _split_context(conn, r, items)
    pids = {x["raw"]: x["id"] for x in conn.execute("SELECT id, raw FROM products")}
    conn.close()
    return render_template(_tpl("receipt_detail.html"), r=r,
                           items=items, preview=resolved,
                           grocy_configured=configured, grocy_error=err,
                           push_log=_push_log(rid), pids=pids,
                           users=users, splits=splits, paid_with=paid_with)


@app.route("/receipts/<int:rid>")
def receipt_detail(rid):
    conn = get_db()
    r = conn.execute("SELECT * FROM receipts WHERE id=?", (rid,)).fetchone()
    items = conn.execute("SELECT * FROM items WHERE receipt_id=?", (rid,)).fetchall()
    if not r:
        conn.close()
        return "Not found", 404
    users, splits, paid_with = _split_context(conn, r, items)
    pids = {x["raw"]: x["id"] for x in conn.execute("SELECT id, raw FROM products")}
    conn.close()
    try:
        _, preview, catalog, configured, err = resolve_push_items(rid)
    except Exception:
        preview, configured, err = [], False, None
    return render_template(_tpl("receipt_detail.html"), r=r, items=items, preview=preview,
                           grocy_configured=configured, grocy_error=err,
                           push_log=_push_log(rid), pids=pids,
                           users=users, splits=splits, paid_with=paid_with)


@app.route("/receipts/<int:rid>/grocy-push", methods=["POST"])
def grocy_push(rid):
    import json as _json
    force = request.form.get("force") == "1"
    conn = get_db()
    existing = conn.execute("SELECT * FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    if existing and not force:
        flash("Already pushed at {} — use Force to re-push (may duplicate entries in Grocy).".format(existing["pushed_at"]))
        return redirect(url_for("receipt_detail", rid=rid))
    r, resolved, catalog, configured, err = resolve_push_items(rid)
    if not r:
        return "Not found", 404
    if not configured:
        flash(grocy_client.CONFIG_HINT)
        return redirect(url_for("receipt_detail", rid=rid))
    if err and not resolved:
        flash(f"Grocy push aborted: {err} No entries were sent and the previous push log was kept.")
        return redirect(url_for("receipt_detail", rid=rid))
    try:
        base_url, api_key = grocy_client.get_config()
    except Exception as e:
        flash(f"Grocy check failed: {e}")
        return redirect(url_for("receipt_detail", rid=rid))
    note = f"Lidl {r['filename']} {r['receipt_date'] or ''}".strip()
    pushed, skipped, errors = 0, 0, []
    for d in resolved:
        if d["status"] == "bad-quantity":
            errors.append({"raw": d["raw"], "error": "bad quantity: qty is 0, nothing sent"})
            continue
        if d["status"] != "ready":
            skipped += 1
            continue
        item_note = note
        if d.get("purchase_unit"):
            item_note = f"{note} [{d['purchase_qty']} {d['purchase_unit']}]".strip()
        try:
            entries = grocy_client.add_stock(base_url, api_key, d["grocy_id"], d["amount"], d["price"], item_note)
            stock_id = (entries or [{}])[0].get("stock_id")
            if r["receipt_date"] and stock_id:
                try:
                    grocy_client.set_purchased_date(base_url, api_key, stock_id, r["receipt_date"])
                except Exception as e:
                    errors.append({"raw": d["raw"], "error": f"backdate failed: {str(e)[:150]}"})
            pushed += 1
        except Exception as e:
            errors.append({"raw": d["raw"], "error": str(e)[:200]})
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO grocy_pushes (receipt_id, pushed_at, pushed_count, skipped_count, errors)
           VALUES (?, datetime('now'), ?, ?, ?)""",
        (rid, pushed, skipped, _json.dumps(errors)))
    conn.commit()
    conn.close()
    failed = len([e for e in errors if "backdate" not in e.get("error", "")])
    backdated = "; ".join(e["raw"] for e in errors if "backdate" in e.get("error", ""))
    msg = f"Pushed {pushed}, skipped {skipped}, failed {failed}."
    if backdated:
        msg += f" Backdate issues: {backdated}."
    flash(msg)
    return redirect(url_for("receipt_detail", rid=rid))


@app.route("/mappings/track", methods=["POST"])
def mapping_track():
    raw = request.form.get("raw", "")
    track = 1 if request.form.get("track") == "1" else 0
    if not raw:
        flash("Missing raw")
        return redirect(url_for("products"))
    conn = get_db()
    conn.execute("UPDATE products SET grocy_track=? WHERE raw=?", (track, raw))
    conn.commit()
    conn.close()
    flash(f"{'Tracking' if track else 'Skipping'} {raw} for Grocy push")
    return redirect(url_for("products", q=request.form.get("_q", "")))


@app.route("/mappings/create-in-grocy", methods=["POST"])
def mapping_create_in_grocy():
    raw = request.form.get("raw", "").strip()
    name = request.form.get("name", "").strip()
    barcode = request.form.get("barcode", "").strip()
    if not raw or not name:
        flash("Missing product name")
        return redirect(url_for("products"))

    def _opt(key):
        v = (request.form.get(key) or "").strip()
        try:
            return int(v)
        except ValueError:
            return None

    location_id = _opt("location_id")
    qu_purchase = _opt("qu_purchase")
    qu_stock = _opt("qu_stock")
    try:
        base_url, api_key = grocy_client.get_config()
        gid = grocy_client.create_product(base_url, api_key, name, location_id, qu_purchase, qu_stock)
        if barcode:
            try:
                _save_barcode(base_url, api_key, gid, barcode, qu_purchase)
            except Exception as e:
                flash(f"Created Grocy #{gid} but barcode failed: {e}")
        conn = get_db()
        conn.execute("""UPDATE products SET grocy_id=?, grocy_name=?, grocy_synced_at=datetime('now'),
                        purchase_qu_id=COALESCE(purchase_qu_id, ?), qu_factor=COALESCE(qu_factor, 1.0) WHERE raw=?""",
                     (gid, name, qu_purchase, raw))
        conn.commit()
        conn.close()
        flash(f"Created Grocy #{gid} and linked {raw}")
    except Exception as e:
        flash(f"Grocy create failed: {e}")
    return redirect(url_for("products", q=request.form.get("_q", "")))


@app.route("/mappings/new-item")
def mapping_new_item():
    raw = request.args.get("raw", "").strip()
    conn = get_db()
    prod = conn.execute("SELECT * FROM products WHERE raw=?", (raw,)).fetchone()
    conn.close()
    prod = dict(prod) if prod else {"raw": raw, "clean": raw, "brand": "",
                                   "barcode": "", "category": "Misc"}
    grocy_error, groups = None, []
    groups_cached = False
    lookups = {"locations": [], "quantity_units": []}
    try:
        base_url, api_key = grocy_client.get_config()
        grocy_configured = True
        try:
            lookups = grocy_client.fetch_lookups(base_url, api_key)
            groups = grocy_client.fetch_product_groups(base_url, api_key)
        except Exception as e:
            grocy_error = f"Grocy lookups unavailable: {str(e)[:150]}"
            conn = get_db()
            groups = [{"id": r[0], "name": r[1]} for r in
                      conn.execute("SELECT grocy_id, name FROM categories "
                                   "WHERE grocy_id IS NOT NULL ORDER BY name").fetchall()]
            conn.close()
            groups_cached = bool(groups)
    except grocy_client.GrocyConfigError:
        grocy_configured = False
    return render_template(_tpl("item_new.html"), prod=prod,
                           grocy_configured=grocy_configured,
                           grocy_error=grocy_error,
                           groups_cached=groups_cached,
                           locations=lookups["locations"],
                           quantity_units=lookups["quantity_units"],
                           groups=groups)


@app.route("/mappings/new-item", methods=["POST"])
def mapping_new_item_create():
    raw = request.form.get("raw", "").strip()
    name = request.form.get("name", "").strip()
    if not raw or not name:
        flash("Missing product name")
        return redirect(url_for("mapping_new_item", raw=raw))

    def _opt(key):
        v = (request.form.get(key) or "").strip()
        if v == "":
            return None
        try:
            return int(v)
        except ValueError:
            return None

    def _dec(key):
        v = (request.form.get(key) or "").strip()
        return v or None

    barcode = (request.form.get("barcode") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    try:
        base_url, api_key = grocy_client.get_config()
        gid = grocy_client.create_product(
            base_url, api_key, name, _opt("location_id"),
            _opt("qu_purchase"), _opt("qu_stock"),
            min_stock_amount=_dec("min_stock"), qu_factor=_dec("qu_factor"),
            product_group_id=_opt("product_group_id"),
            parent_product_id=_opt("parent_product_id"),
            description=description)
        if barcode:
            try:
                _save_barcode(base_url, api_key, gid, barcode, _opt("qu_purchase"))
            except Exception as e:
                flash(f"Created Grocy #{gid} but barcode failed: {e}")
        conn = get_db()
        conn.execute("UPDATE products SET grocy_id=?, grocy_name=?, grocy_synced_at=datetime('now') WHERE raw=?",
                     (gid, name, raw))
        conn.commit()
        conn.close()
        flash(f"Created Grocy #{gid} and linked {raw}")
    except Exception as e:
        flash(f"Grocy create failed: {e}")
        return redirect(url_for("mapping_new_item", raw=raw))
    return redirect(url_for("products"))


def _resolve_category(form):
    """Dropdown value, or the typed-in name when '+ New category' was chosen."""
    cat = form.get("category", "").strip()
    if cat == "__new__":
        cat = form.get("new_category", "").strip()
    return cat or "Misc"


@app.route("/mappings/update", methods=["POST"])
def mapping_update():
    raw = request.form["raw"]
    clean = request.form.get("clean", "").strip() or raw
    category = _resolve_category(request.form)
    barcode = request.form.get("barcode", "").strip() or None
    brand = request.form.get("brand", "").strip() or None
    unit = request.form.get("unit", "").strip() or None
    notes = request.form.get("notes", "").strip() or None
    conn = get_db()
    conn.execute(
        """UPDATE products SET clean=?, category=?, barcode=?, brand=?, unit=?, notes=?,
           updated_at=datetime('now') WHERE raw=?""",
        (clean, category, barcode, brand, unit, notes, raw))
    # re-resolve existing items with this raw so dashboard updates immediately
    conn.execute("UPDATE items SET clean=?, category=?, match_type='exact', match_score=100 WHERE raw=?",
                 (clean, category, raw))
    conn.commit()
    conn.close()
    flash(f"Updated {raw}")
    return redirect(url_for("products", q=request.form.get("_q", "")))


@app.route("/mappings/bulk-update", methods=["POST"])
def mapping_bulk_update():
    try:
        count = int(request.form.get("count", "0"))
    except ValueError:
        count = 0
    conn = get_db()
    updated = 0
    for i in range(count):
        raw = (request.form.get(f"raw_{i}") or "").strip()
        if not raw:
            continue
        clean = (request.form.get(f"clean_{i}") or "").strip() or raw
        cat = (request.form.get(f"category_{i}") or "").strip()
        if cat == "__new__":
            cat = (request.form.get(f"new_category_{i}") or "").strip()
        category = cat or "Misc"
        barcode = (request.form.get(f"barcode_{i}") or "").strip() or None
        brand = (request.form.get(f"brand_{i}") or "").strip() or None
        unit = (request.form.get(f"unit_{i}") or "").strip() or None
        notes = (request.form.get(f"notes_{i}") or "").strip() or None
        cur = conn.execute(
            """UPDATE products SET clean=?, category=?, barcode=?, brand=?, unit=?, notes=?,
               updated_at=datetime('now') WHERE raw=?""",
            (clean, category, barcode, brand, unit, notes, raw))
        if cur.rowcount:
            conn.execute("UPDATE items SET clean=?, category=?, match_type='exact', match_score=100 WHERE raw=?",
                         (clean, category, raw))
            updated += 1
    conn.commit()
    conn.close()
    flash(f"Updated {updated} mapping(s)")
    return redirect(url_for("products", q=request.form.get("_q", "")))


@app.route("/mappings/add", methods=["POST"])
def mapping_add():
    raw = request.form["raw"].strip()
    clean = request.form.get("clean", "").strip() or raw
    category = _resolve_category(request.form)
    if not raw:
        return redirect(url_for("products"))
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO products (raw, clean, category) VALUES (?,?,?)",
                 (raw, clean, category))
    conn.execute("UPDATE items SET clean=?, category=?, match_type='exact', match_score=100 WHERE raw=?",
                 (clean, category, raw))
    conn.commit()
    conn.close()
    return redirect(url_for("products"))


@app.route("/mappings/delete/<path:raw>", methods=["POST"])
def mapping_delete(raw):
    conn = get_db()
    conn.execute("DELETE FROM products WHERE raw=?", (raw,))
    conn.commit()
    conn.close()
    return redirect(url_for("products"))


@app.route("/prices")
def prices():
    return render_template(_tpl("prices.html"))


@app.route("/api/price_history")
def api_price_history():
    clean = request.args.get("clean", "")
    rows = query("""
        SELECT r.receipt_date AS d, i.unit_price AS p, i.qty AS q
        FROM items i JOIN receipts r ON r.id=i.receipt_id
        WHERE i.clean=? AND r.receipt_date IS NOT NULL
        ORDER BY d
    """, (clean,))
    legacy = query("SELECT date AS d, price AS p FROM price_history WHERE clean=? ORDER BY d", (clean,))
    live = [dict(x) for x in rows]
    points = sorted([dict(x) for x in rows] + [dict(x) for x in legacy],
                    key=lambda x: (x["d"] or "", x["p"] or 0))
    return jsonify({"live": live, "legacy": [dict(x) for x in legacy], "points": points})


@app.route("/api/price_history_multi")
def api_price_history_multi():
    cleans = [c for c in request.args.getlist("clean") if c.strip()][:50]
    out = {}
    for clean in cleans:
        out[clean] = [dict(x) for x in query("""
            SELECT r.receipt_date AS d, i.unit_price AS p, i.qty AS q
            FROM items i JOIN receipts r ON r.id=i.receipt_id
            WHERE i.clean=? AND r.receipt_date IS NOT NULL
            ORDER BY d
        """, (clean,))]
    return jsonify(out)


@app.route("/api/category_history")
def api_category_history():
    """Per-line points grouped by category — same shape as price_history_multi,
    so the Prices stats table and trend chart work unchanged in category mode."""
    cats = [c for c in request.args.getlist("category") if c.strip()][:50]
    out = {}
    for cat in cats:
        out[cat] = [dict(x) for x in query("""
            SELECT r.receipt_date AS d, i.unit_price AS p, i.qty AS q
            FROM items i JOIN receipts r ON r.id=i.receipt_id
            WHERE COALESCE(i.category,'Misc')=? AND r.receipt_date IS NOT NULL
            ORDER BY d
        """, (cat,))]
    return jsonify(out)


ABC_METRICS = {
    "spend": "ROUND(SUM(i.final),2)",
    "quantity": "ROUND(SUM(i.qty),3)",
    "frequency": "COUNT(*)",
}

@app.route("/api/abc_analysis")
def api_abc_analysis():
    metric = request.args.get("metric", "spend")
    if metric not in ABC_METRICS:
        metric = "spend"
    group = request.args.get("group", "product")
    if group not in ("product", "category"):
        group = "product"
    try:
        top = int(request.args.get("top", "5"))
    except ValueError:
        top = 5
    top = max(1, min(top, 50))
    xclause, xargs = _excluded_sql("COALESCE(i.category,'Misc')")
    if group == "category":
        rows = query(f"""
            SELECT COALESCE(i.category,'Misc') AS clean, {ABC_METRICS[metric]} AS v
            FROM items i JOIN receipts r ON r.id=i.receipt_id
            WHERE r.receipt_date IS NOT NULL{xclause}
            GROUP BY COALESCE(i.category,'Misc') ORDER BY v DESC
        """, xargs)
    else:
        rows = query(f"""
            SELECT i.clean AS clean, {ABC_METRICS[metric]} AS v
            FROM items i JOIN receipts r ON r.id=i.receipt_id
            WHERE r.receipt_date IS NOT NULL{xclause}
            GROUP BY i.clean ORDER BY v DESC
        """, xargs)
    total = sum(float(r["v"] or 0) for r in rows) or 1.0
    cum, out, classes = 0.0, [], {"A": 0, "B": 0, "C": 0}
    for idx, r in enumerate(rows, start=1):
        cum += float(r["v"] or 0)
        pct = round(100 * cum / total, 1)
        cls = "A" if pct <= 80 else ("B" if pct <= 95 else "C")
        classes[cls] += 1
        out.append({"clean": r["clean"], "value": r["v"],
                    "cum_pct": pct, "class": cls, "rank": idx})
    return jsonify({"metric": metric, "group": group, "rows": out[:top], "total": round(total, 2),
                    "count": len(rows), "classes": classes})


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        which = request.form.get("form", "grocy")
        if which == "excluded":
            import json as _json
            excl = sorted({e.strip() for e in request.form.getlist("excluded") if e.strip()})
            app_settings.set("EXCLUDED_CATEGORIES", _json.dumps(excl))
        else:
            url = (request.form.get("GROCY_URL") or "").strip()
            if url and not url.rstrip("/").endswith("/api"):
                flash("Grocy URL must end with /api - value not saved")
            else:
                app_settings.set("GROCY_URL", url)
                key = (request.form.get("GROCY_API_KEY") or "").strip()
                if key:
                    app_settings.set("GROCY_API_KEY", key)
        flash("Settings saved")
        return redirect(url_for("settings"))
    vals = {k: app_settings.get(k) for k in ("GROCY_URL", "GROCY_API_KEY")}
    vals["GROCY_API_KEY"] = ""  # never render the secret; empty submission keeps it
    vals["EXT_INGEST_TOKEN"] = _ext_token()
    conn = get_db()
    users = [dict(r) for r in conn.execute(
        "SELECT id, name, COALESCE(active,1) AS active FROM users ORDER BY name").fetchall()]
    cards = [dict(r) for r in conn.execute(
        """SELECT c.last4 AS last4, c.user_id AS user_id, u.name AS user_name
           FROM card_map c JOIN users u ON u.id = c.user_id ORDER BY c.last4""").fetchall()]
    units = [dict(r) for r in conn.execute("SELECT id, grocy_id, name, source FROM units ORDER BY name").fetchall()]
    categories = [dict(r) for r in conn.execute("SELECT id, grocy_id, name, source FROM categories ORDER BY name").fetchall()]
    all_cats = [r[0] for r in conn.execute("SELECT DISTINCT COALESCE(category,'Misc') FROM products ORDER BY 1").fetchall()]
    conn.close()
    return render_template(_tpl("settings.html"), v=vals, users=users, cards=cards, units=units,
                           categories=categories, all_cats=all_cats,
                           excluded=sorted(_excluded_categories()))


@app.route("/settings/units-refresh", methods=["POST"])
def settings_units_refresh():
    try:
        base_url, api_key = grocy_client.get_config()
        lookups = grocy_client.fetch_lookups(base_url, api_key)
    except Exception as e:
        flash(f"Grocy units unavailable, keeping cached list: {str(e)[:150]}")
        return redirect(url_for("settings"))
    conn = get_db()
    n = 0
    for u in lookups.get("quantity_units", []):
        try:
            gid = int(u.get("id"))
        except (TypeError, ValueError):
            continue
        name = str(u.get("name", "")).strip()
        if not name:
            continue
        try:
            conn.execute("""INSERT INTO units (grocy_id, name, source, cached_at)
                            VALUES (?, ?, 'grocy', datetime('now'))
                            ON CONFLICT(grocy_id) DO UPDATE SET name=excluded.name, source='grocy',
                            cached_at=datetime('now')""", (gid, name))
            n += 1
        except Exception:
            continue
    conn.commit()
    conn.close()
    flash(f"Units refreshed from Grocy: {n}")
    return redirect(url_for("settings"))


@app.route("/settings/categories-refresh", methods=["POST"])
def settings_categories_refresh():
    try:
        base_url, api_key = grocy_client.get_config()
        groups = grocy_client.fetch_product_groups(base_url, api_key)
    except Exception as e:
        flash(f"Grocy categories unavailable, keeping cached list: {str(e)[:150]}")
        return redirect(url_for("settings"))
    conn = get_db()
    n = 0
    for g in groups:
        try:
            gid = int(g.get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        name = str(g.get("name", "")).strip()
        if not name:
            continue
        try:
            conn.execute("""INSERT INTO categories (grocy_id, name, source, cached_at)
                            VALUES (?, ?, 'grocy', datetime('now'))
                            ON CONFLICT(grocy_id) DO UPDATE SET name=excluded.name, source='grocy',
                            cached_at=datetime('now')""", (gid, name))
            n += 1
        except Exception:
            continue
    conn.commit()
    conn.close()
    flash(f"Categories refreshed from Grocy: {n}")
    return redirect(url_for("settings"))


@app.route("/settings/ext-regenerate", methods=["POST"])
def settings_ext_regenerate():
    app_settings.set("EXT_INGEST_TOKEN", secrets.token_urlsafe(32))
    flash("Extension token regenerated — update the Firefox extension")
    return redirect(url_for("settings"))


@app.route("/settings/users", methods=["POST"])
def settings_user_add():
    name = (request.form.get("name") or "").strip()
    if name:
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO users (name) VALUES (?)", (name,))
        conn.commit()
        conn.close()
    return redirect(url_for("settings"))


@app.route("/settings/users/rename", methods=["POST"])
def settings_user_rename():
    try:
        uid = int(request.form.get("id") or "0")
    except ValueError:
        uid = 0
    name = (request.form.get("name") or "").strip()
    if uid > 0 and name:
        conn = get_db()
        try:
            conn.execute("UPDATE users SET name=? WHERE id=?", (name, uid))
            conn.commit()
        except Exception:
            flash("Name already taken")
        finally:
            conn.close()
    return redirect(url_for("settings"))


@app.route("/settings/users/vacation", methods=["POST"])
def settings_user_vacation():
    try:
        uid = int(request.form.get("id") or "0")
    except ValueError:
        uid = 0
    active = 1 if (request.form.get("active") or "") == "1" else 0
    if uid > 0:
        conn = get_db()
        if not active and conn.execute(
                "SELECT COUNT(*) FROM users WHERE COALESCE(active,1)=1 AND id != ?",
                (uid,)).fetchone()[0] == 0:
            flash("Cannot hide the last active user")
        else:
            conn.execute("UPDATE users SET active=? WHERE id=?", (active, uid))
            conn.commit()
        conn.close()
    return redirect(url_for("settings"))


@app.route("/settings/users/delete", methods=["POST"])
def settings_user_delete():
    try:
        uid = int(request.form.get("id") or "0")
    except ValueError:
        uid = 0
    if uid > 0:
        conn = get_db()
        tied = conn.execute("SELECT COUNT(*) FROM item_splits WHERE user_id=?", (uid,)).fetchone()[0]
        cards = conn.execute("SELECT COUNT(*) FROM card_map WHERE user_id=?", (uid,)).fetchone()[0]
        if tied or cards:
            flash(f"Cannot delete: user is tied to {tied} split line(s) and {cards} card(s)")
        else:
            conn.execute("DELETE FROM users WHERE id=?", (uid,))
            conn.commit()
        conn.close()
    return redirect(url_for("settings"))


@app.route("/settings/cards", methods=["POST"])
def settings_card_link():
    last4 = (request.form.get("last4") or "").strip()
    try:
        uid = int(request.form.get("user_id") or "0")
    except ValueError:
        uid = 0
    if len(last4) == 4 and last4.isdigit() and uid > 0:
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO card_map (last4, user_id) VALUES (?, ?)",
                     (last4, uid))
        conn.commit()
        conn.close()
    else:
        flash("Card needs exactly 4 digits and a user")
    return redirect(url_for("settings"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True, use_reloader=False)
